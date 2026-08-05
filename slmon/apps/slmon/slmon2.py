#!/usr/bin/env seiscomp-python

from getopt import getopt, GetoptError
from time import time, gmtime, sleep
from datetime import datetime
import os
import sys
import signal
import glob
import re
import json
import threading

from seiscomp.myconfig import MyConfig
import seiscomp.slclient
import seiscomp.kernel, seiscomp.config
from urllib.request import urlopen

# A dictionary to store station coordinates
station_coordinates = {}

def load_station_coordinates(config):
    """Load station coordinates from FDSN web service"""
    global station_coordinates

    # Get base URL from config or use default
    base_url = config['setup'].get('fdsnws_url', 'http://localhost:8080/fdsnws/')

    # Create a dictionary in the format needed by data_fetcher
    stations_config = {}
    for key in config.station:
        network = config.station[key]['net']
        station = config.station[key]['sta']
        station_id = f"{network}.{station}"
        stations_config[station_id] = {
            'network': network,
            'station': station,
            'location': '',  # Default location
            'stream': 'HHZ'  # Default stream
        }

    # Fetch coordinates for each station
    for station_id, station_info in stations_config.items():
        network = station_info['network']
        station = station_info['station']

        try:
            url = base_url + f"station/1/query?net={network}&sta={station}&format=text"
            with urlopen(url, timeout=10) as fp:
                fp.readline()
                location_info = dict(zip(('lat', 'lon', 'elevation'), map(float, fp.readline().split(b'|')[2:5])))

            if location_info:
                station_coordinates[f"{network}_{station}"] = location_info
                print(f"Loaded coordinates for {network}_{station}: {location_info}")
            else:
                print(f"Could not fetch coordinates for {network}_{station}")
        except Exception as e:
            print(f"Error fetching coordinates for {network}_{station}: {str(e)}")

    # Print summary
    print(f"Loaded coordinates for {len(station_coordinates)} stations")



usage_info = """
Usage:
  slmon [options]

Enhanced SeedLink monitor creating modern, interactive web dashboards

Options:
  -h, --help       display this help message
  -c               ini_setup = arg
  -s               ini_stations = arg
  -t               refresh = float(arg) # XXX not yet used
  -v               verbose = 1
  -g, --generate   generate only template files and exit

Examples:
Start slmon from the command line
  slmon -c $SEISCOMP_ROOT/var/lib/slmon/config.ini

Restart slmon in order to update the web pages. Use crontab entries for
automatic restart, e.g.:
  */3 * * * * /home/sysop/seiscomp/bin/seiscomp check slmon >/dev/null 2>&1
"""

def usage(exitcode=0):
    sys.stderr.write(usage_info)
    exit(exitcode)

try:
    seiscompRoot = os.environ["SEISCOMP_ROOT"]
except:
    print("\nSEISCOMP_ROOT must be defined - EXIT\n", file=sys.stderr)
    usage(exitcode=2)

ini_stations = os.path.join(seiscompRoot, 'var/lib/slmon2/stations.ini')
ini_setup = os.path.join(seiscompRoot, 'var/lib/slmon2/config.ini')

regexStreams = re.compile("[SLBVEH][HNLGD][ZNE123ADHF]")
verbose = 0
generate_only = False


class Module(seiscomp.kernel.Module):
    def __init__(self, env):
        seiscomp.kernel.Module.__init__(self, env, env.moduleName(__file__))

    def printCrontab(self):
        print("3 * * * * %s/bin/seiscomp check slmon >/dev/null 2>&1" % (self.env.SEISCOMP_ROOT))


class Status:
    def __repr__(self):
        return "%2s %-5s %2s %3s %1s %s %s" % \
               (self.net, self.sta, self.loc, self.cha, self.typ,
                str(self.last_data), str(self.last_feed))


class StatusDict(dict):
    def __init__(self, source=None):
        if source:
            self.read(source)

    def fromSlinkTool(self, server="", stations=["AU_ARMA", "AU_BLDU", "AU_YAPP"]):
        # later this shall use XML
        cmd = "slinktool -nd 10 -nt 10 -Q %s" % server
        print(cmd)
        f = os.popen(cmd)
        # regex = re.compile("[SLBVEH][HNLG][ZNE123]")
        regex = regexStreams
        for line in f:
            # A truncated/corrupted 'slinktool -Q' response (e.g. from a
            # network hiccup mid-transfer) can hand us a short or garbled
            # line here. Skip it instead of letting an IndexError/ValueError
            # crash the whole bootstrap before slmon2 even starts.
            try:
                net_sta = line[:2].strip() + "_" + line[3:8].strip()
                if not net_sta in stations:
                    continue
                typ = line[16]
                if typ != "D":
                    continue
                cha = line[12:15].strip()
                if not regex.match(cha):
                    continue

                d = Status()
                d.net = line[0: 2].strip()
                d.sta = line[3: 8].strip()
                d.loc = line[9:11].strip()
                d.cha = line[12:15]
                d.typ = line[16]
                d.last_data = seiscomp.slclient.timeparse(line[47:70])
                d.last_feed = d.last_data
                sec = "%s_%s" % (d.net, d.sta)
                sec = "%s.%s.%s.%s.%c" % (d.net, d.sta, d.loc, d.cha, d.typ)
                self[sec] = d
            except (IndexError, ValueError) as e:
                print(f"skipping malformed 'slinktool -Q' line: {line!r} ({e})",
                      file=sys.stderr)
                continue

    def read(self, source):
        """
        Read status data from various source types (file path, file object, or list of lines)
        Python 3 compatible version
        """
        lines = []

        # Handle different source types
        if isinstance(source, str):
            # String - treat as file path
            with open(source, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        elif hasattr(source, 'readlines'):
            # File-like object
            lines = source.readlines()
        elif isinstance(source, list):
            # Already a list of lines
            lines = source
        else:
            raise TypeError(f'Cannot read from {type(source).__name__}')

        # Process each line
        for line in lines:
            line = str(line).rstrip('\n\r')

            # Skip lines that are too short
            if len(line) < 65:
                continue

            # Create status object and parse fields
            d = Status()
            d.net = line[0:2].strip()
            d.sta = line[3:8].strip()
            d.loc = line[9:11].strip()
            d.cha = line[12:15].strip()
            d.typ = line[16]

            # Parse timestamps with error handling
            try:
                d.last_data = seiscomp.slclient.timeparse(line[18:41])
            except:
                d.last_data = None

            try:
                d.last_feed = seiscomp.slclient.timeparse(line[42:65])
            except:
                d.last_feed = None

            # Ensure last_feed is not earlier than last_data
            if d.last_feed and d.last_data and d.last_feed < d.last_data:
                d.last_feed = d.last_data

            # Create dictionary key and store
            sec = f"{d.net}_{d.sta}:{d.loc}.{d.cha}.{d.typ}"
            self[sec] = d

    def write(self, f):
        """
        Write status data to file or file-like object
        Python 3 compatible version
        """
        should_close = False

        if isinstance(f, str):
            # String - treat as file path
            f = open(f, "w", encoding='utf-8')
            should_close = True

        try:
            # Prepare and write sorted lines
            lines = [str(self[key]) for key in sorted(self.keys())]
            f.write('\n'.join(lines) + '\n')
        finally:
            if should_close:
                f.close()

    def to_json(self):
        """Convert status dictionary to JSON for JavaScript use"""
        global station_coordinates
        stations_data = {}

        # Group by network and station
        for key, value in self.items():
            net_sta = f"{value.net}_{value.sta}"
            if net_sta not in stations_data:
                stations_data[net_sta] = {
                    "network": value.net,
                    "station": value.sta,
                    "channels": [],
                    "channelGroups": {
                        "HH": [],  # High-frequency, High-gain channels
                        "BH": [],  # Broadband, High-gain channels
                        "LH": [],  # Long-period, High-gain channels
                        "SH": [],  # Short-period, High-gain channels
                        "EH": [],  # Extremely Short-period, High-gain channels
                        "other": []  # All other channel types
                    }
                }

                # Add coordinates if available
                if net_sta in station_coordinates:
                    stations_data[net_sta]["coordinates"] = station_coordinates[net_sta]

            # Get latency information
            now = datetime.utcnow()
            if value.last_data:
                latency_seconds = total_seconds(now - value.last_data)
            else:
                # No valid last-data timestamp (e.g. a skipped malformed
                # line from 'slinktool -Q'); treat as maximally stale
                # instead of crashing on `now - None`. Use a large finite
                # value, not float('inf'), since this ends up in JSON
                # output and Infinity isn't valid JSON.
                latency_seconds = 10**9

            # Extract channel type (first two characters, e.g., 'LH', 'BH', 'HH', 'EH')
            channel_type = value.cha[:2] if len(value.cha) >= 2 else "other"

            # Get status with channel-aware thresholds
            status = get_status_from_seconds(latency_seconds, channel_type)

            # Create channel data
            channel_data = {
                "location": value.loc,
                "channel": value.cha,
                "type": value.typ,
                "channelType": channel_type,
                "last_data": value.last_data.isoformat() if value.last_data else None,
                "last_feed": value.last_feed.isoformat() if value.last_feed else None,
                "latency": latency_seconds,
                "status": status
            }

            # Add to main channels list
            stations_data[net_sta]["channels"].append(channel_data)

            # Add to channel group for separated status calculation
            if channel_type in stations_data[net_sta]["channelGroups"]:
                stations_data[net_sta]["channelGroups"][channel_type].append(channel_data)
            else:
                stations_data[net_sta]["channelGroups"]["other"].append(channel_data)

        # Convert to list for easier JavaScript processing
        stations_list = []
        for net_sta, data in stations_data.items():
            # Calculate overall station status based on priority channels (non-LH channels)
            # First try HH channels
            if data["channelGroups"]["HH"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["HH"]])
                data["status"] = get_status_from_seconds(worst_latency)
                data["primaryChannelType"] = "HH"
            # Then try BH channels
            elif data["channelGroups"]["BH"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["BH"]])
                data["status"] = get_status_from_seconds(worst_latency)
                data["primaryChannelType"] = "BH"
            # Then try SH channels
            elif data["channelGroups"]["SH"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["SH"]])
                data["status"] = get_status_from_seconds(worst_latency)
                data["primaryChannelType"] = "SH"
            # Then try EH channels
            elif data["channelGroups"]["EH"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["EH"]])
                data["status"] = get_status_from_seconds(worst_latency)
                data["primaryChannelType"] = "EH"
            # Only use LH if nothing else is available
            elif data["channelGroups"]["LH"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["LH"]])
                data["status"] = get_status_from_seconds(worst_latency, "LH")
                data["primaryChannelType"] = "LH"
            # Fall back to other channels
            elif data["channelGroups"]["other"]:
                worst_latency = max([ch["latency"] for ch in data["channelGroups"]["other"]])
                data["status"] = get_status_from_seconds(worst_latency)
                data["primaryChannelType"] = "other"
            else:
                # Failsafe if no channels
                data["status"] = "unavailable"
                data["primaryChannelType"] = "none"
                worst_latency = 0

            data["latency"] = worst_latency
            data["id"] = net_sta
            stations_list.append(data)

        return json.dumps(stations_list)

def get_map_settings(config):
    """Extract map settings from config for JavaScript use"""
    map_settings = {
        'center': {
            'lat': -25.6,  # Default latitude
            'lon': 134.3,  # Default longitude
            'zoom': 6   # Default zoom
        },
        'defaultLayer': 'street',
        'enableClustering': True,
        'showFullscreenControl': True,
        'showLayerControl': True,
        'showLocateControl': True,
        'darkModeLayer': 'dark',
        'lightModeLayer': 'street'
    }

    # Extract center coordinates from config
    if 'center_map' in config['setup']:
        if 'lat' in config['setup']['center_map']:
            map_settings['center']['lat'] = float(config['setup']['center_map']['lat'])
        if 'lon' in config['setup']['center_map']:
            map_settings['center']['lon'] = float(config['setup']['center_map']['lon'])
        if 'zoom' in config['setup']['center_map']:
            map_settings['center']['zoom'] = int(config['setup']['center_map']['zoom'])

    # Extract other map settings
    if 'map_settings' in config['setup']:
        map_config = config['setup']['map_settings']

        if 'default_layer' in map_config:
            map_settings['defaultLayer'] = map_config['default_layer']

        if 'enable_clustering' in map_config:
            map_settings['enableClustering'] = map_config['enable_clustering'] == 'true' or map_config['enable_clustering'] is True

        if 'show_fullscreen_control' in map_config:
            map_settings['showFullscreenControl'] = map_config['show_fullscreen_control'] == 'true' or map_config['show_fullscreen_control'] is True

        if 'show_layer_control' in map_config:
            map_settings['showLayerControl'] = map_config['show_layer_control'] == 'true' or map_config['show_layer_control'] is True

        if 'show_locate_control' in map_config:
            map_settings['showLocateControl'] = map_config['show_locate_control'] == 'true' or map_config['show_locate_control'] is True

        if 'dark_mode_layer' in map_config:
            map_settings['darkModeLayer'] = map_config['dark_mode_layer']

        if 'light_mode_layer' in map_config:
            map_settings['lightModeLayer'] = map_config['light_mode_layer']

    return map_settings

def get_status_from_seconds(seconds, channel_type=None):
    """
    Get status code based on latency in seconds with channel-specific thresholds

    Args:
        seconds (float): Latency in seconds
        channel_type (str): Channel type (e.g., 'LH', 'BH', 'HH', 'EH')

    Returns:
        str: Status code (good, delayed, etc.)
    """
    # Special handling for LH channels - they're naturally delayed
    if channel_type == 'LH':
        # More lenient thresholds for LH channels
        if seconds > 604800:  # > 7 days
            return "unavailable"
        elif seconds > 518400:  # > 6 days
            return "four-day"
        elif seconds > 432000:  # > 5 days
            return "three-day"
        elif seconds > 345600:  # > 4 days
            return "multi-day"
        elif seconds > 259200:  # > 3 days
            return "day-delayed"
        elif seconds > 86400:  # > 1 day
            return "critical"
        elif seconds > 43200:  # > 12 hours
            return "warning"
        elif seconds > 21600:  # > 6 hours
            return "hour-delayed"
        elif seconds > 10800:  # > 3 hours
            return "very-delayed"
        elif seconds > 3600:  # > 1 hour
            return "long-delayed"
        elif seconds > 1800:  # > 30 minutes
            return "delayed"
        else:  # <= 30 minutes (LH channels are considered good even with moderate delay)
            return "good"

    # Standard thresholds for other channels
    if seconds > 432000:  # > 5 days
        return "unavailable"
    elif seconds > 345600:  # > 4 days
        return "four-day"
    elif seconds > 259200:  # > 3 days
        return "three-day"
    elif seconds > 172800:  # > 2 days
        return "multi-day"
    elif seconds > 86400:  # > 1 day
        return "day-delayed"
    elif seconds > 21600:  # > 6 hours
        return "critical"
    elif seconds > 7200:  # > 2 hours
        return "warning"
    elif seconds > 3600:  # > 1 hour
        return "hour-delayed"
    elif seconds > 1800:  # > 30 minutes
        return "very-delayed"
    elif seconds > 600:  # > 10 minutes
        return "long-delayed"
    elif seconds > 60:  # > 1 minute
        return "delayed"
    else:  # <= 1 minute
        return "good"


# Single source of truth for status -> color/label, in increasing severity
# order. CSS (:root vars + .station-* classes), the browser-side JS
# (window.STATUS_COLORS, getStatusColor(), the map legend) and getColor()
# below are all generated/derived from this one list so they can no longer
# silently drift apart the way the old CSS palette and the old hardcoded
# JS/Python palette did (same status name, different color on screen
# depending which part of the page you looked at).
#
# 'good' is intentionally neutral (no color) rather than green: several
# buckets in the middle of this list (e.g. hour-delayed) used to be
# rendered green, which reads as "healthy" even though it is well past
# the point where an operator should be concerned.
STATUS_STYLES = [
    {"name": "good",         "bg": "#ffffff", "fg": "#1f2937", "caption": "Good (&le; 1 min)"},
    {"name": "delayed",      "bg": "#fef3c7", "fg": "#92400e", "caption": "&gt; 1 min"},
    {"name": "long-delayed", "bg": "#fde68a", "fg": "#78350f", "caption": "&gt; 10 min"},
    {"name": "very-delayed", "bg": "#fbbf24", "fg": "#78350f", "caption": "&gt; 30 min"},
    {"name": "hour-delayed", "bg": "#f59e0b", "fg": "#7c2d12", "caption": "&gt; 1 hour"},
    {"name": "warning",      "bg": "#f97316", "fg": "#ffffff", "caption": "&gt; 2 hours"},
    {"name": "critical",     "bg": "#ea580c", "fg": "#ffffff", "caption": "&gt; 6 hours"},
    {"name": "day-delayed",  "bg": "#dc2626", "fg": "#ffffff", "caption": "&gt; 1 day"},
    {"name": "multi-day",    "bg": "#b91c1c", "fg": "#ffffff", "caption": "&gt; 2 days"},
    {"name": "three-day",    "bg": "#991b1b", "fg": "#ffffff", "caption": "&gt; 3 days"},
    {"name": "four-day",     "bg": "#7c2d12", "fg": "#ffffff", "caption": "&gt; 4 days"},
    {"name": "unavailable",  "bg": "#57534e", "fg": "#ffffff", "caption": "&gt; 5 days"},
]
STATUS_COLORS = {s["name"]: s["bg"] for s in STATUS_STYLES}


def status_color(name):
    """Look up a status's color in the single shared palette (STATUS_STYLES)."""
    return STATUS_COLORS.get(name, STATUS_COLORS["good"])


def render_legend_html():
    """Legend swatches for all 12 statuses, generated from STATUS_STYLES so
    it can't fall out of sync with the palette (the old hand-written legends
    on the main and station pages silently covered only 6-8 of the 12)."""
    return "\n".join(
        f'            <div class="legend-item">\n'
        f'                <div class="legend-color" style="background-color: {s["bg"]}; '
        f'{"border: 1px solid #e5e7eb;" if s["name"] == "good" else ""}"></div>\n'
        f'                <span>{s["caption"]}</span>\n'
        f'            </div>'
        for s in STATUS_STYLES
    )


def getColor(delta):
    """Color for a raw latency, via the same status buckets/thresholds and
    the same shared palette used everywhere else (get_status_from_seconds
    uses the identical cutoffs this function used to duplicate by hand)."""
    return status_color(get_status_from_seconds(total_seconds(delta)))


def total_seconds(td):
    return td.seconds + (td.days*86400)


def myrename(name1, name2):
    # fault-tolerant rename that doesn't cause an exception if it fails, which
    # may happen e.g. if the target is on a non-reachable NFS directory
    try:
        os.rename(name1, name2)
    except OSError:
        print("failed to rename(%s,%s)" % (name1, name2), file=sys.stderr)


def formatLatency(delta):
    """Format latency for display"""
    if delta is None: return 'n/a'

    t = total_seconds(delta)

    if t > 86400: return f"{t/86400:.1f} d"
    elif t > 7200: return f"{t/3600:.1f} h"
    elif t > 120: return f"{t/60:.1f} m"
    else: return f"{t:.1f} s"


def generate_css_file(config):
    """Generate the CSS file with theme support"""
    css_content = """
:root {
    /* Light theme variables */
    --primary-color: #4f46e5;
    --primary-hover: #4338ca;
    --text-primary: #1f2937;
    --text-secondary: #6b7280;
    --bg-primary: #ffffff;
    --bg-secondary: #f9fafb;
    --bg-tertiary: #f3f4f6;
    --border-color: #e5e7eb;
    --border-radius: 8px;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.1);
    --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);

    /* Status colors */
/*__STATUS_VARS__*/
}

.dark-mode {
    /* Dark theme variables */
    --primary-color: #818cf8;
    --primary-hover: #a5b4fc;
    --text-primary: #f9fafb;
    --text-secondary: #9ca3af;
    --bg-primary: #1f2937;
    --bg-secondary: #111827;
    --bg-tertiary: #374151;
    --border-color: #374151;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.3);
    --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.3);

    /* Dark theme status colors - background stays dark */
    --status-good: #1f2937;
}

/* General Styles */
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
    color: var(--text-primary);
    background-color: var(--bg-secondary);
    padding: 0;
    margin: 0;
}

.container {
    max-width: 1400px;
    margin: 20px auto;
    padding: 30px;
    background-color: var(--bg-primary);
    border-radius: var(--border-radius);
    box-shadow: var(--shadow-md);
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 15px;
}

h1 {
    font-size: 28px;
    font-weight: 600;
    color: var(--text-primary);
    letter-spacing: -0.5px;
}

.subtitle {
    color: var(--text-secondary);
    font-size: 16px;
    margin-bottom: 20px;
}

/* Navigation Tabs */
.view-toggle {
    display: flex;
    gap: 10px;
}

.view-toggle a {
    padding: 8px 15px;
    border-radius: 6px;
    color: var(--text-secondary);
    text-decoration: none;
    transition: all 0.2s ease;
    font-weight: 500;
    font-size: 14px;
}

.view-toggle a:hover {
    background-color: var(--bg-tertiary);
    color: var(--primary-color);
}

.view-toggle a.active {
    background-color: var(--primary-color);
    color: white;
}

/* Controls */
.controls {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: 20px 0;
    flex-wrap: wrap;
    gap: 15px;
}

.actions {
    display: flex;
    gap: 10px;
}

.action-button {
    padding: 8px 15px;
    display: flex;
    align-items: center;
    gap: 6px;
    background-color: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.2s ease;
}

.action-button:hover {
    background-color: var(--bg-primary);
    color: var(--primary-color);
}

.action-button svg {
    width: 16px;
    height: 16px;
}

.refresh-control {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 15px;
    background-color: var(--bg-tertiary);
    border-radius: 6px;
}

.input-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

.refresh-control input {
    width: 60px;
    padding: 6px 10px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-size: 14px;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    text-align: center;
}

.refresh-control button {
    padding: 6px 12px;
    background-color: var(--primary-color);
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-weight: 500;
    transition: background-color 0.2s ease;
}

.refresh-control button:hover {
    background-color: var(--primary-hover);
}

.status-counter {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
}

#refresh-status {
    font-size: 13px;
    color: var(--text-secondary);
}

.countdown {
    font-size: 13px;
    color: var(--text-secondary);
}

#next-refresh {
    color: var(--primary-color);
    font-weight: 500;
}

/* Filter and Search */
.filters {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 20px;
    padding: 15px;
    background-color: var(--bg-tertiary);
    border-radius: var(--border-radius);
}

.filter-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

.filter-group label {
    font-size: 14px;
    color: var(--text-secondary);
    font-weight: 500;
}

.filter-group select {
    padding: 6px 10px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-size: 14px;
}

.search-box {
    padding: 6px 12px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-size: 14px;
    min-width: 200px;
}

/* Table View */
.table-container {
    overflow-x: auto;
    margin-bottom: 20px;
}

table {
    width: 100%;
    border-collapse: collapse;
}

table th {
    padding: 12px 15px;
    background-color: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    text-align: left;
    border-bottom: 1px solid var(--border-color);
    position: sticky;
    top: 0;
    z-index: 10;
}

table td {
    padding: 10px 15px;
    border-bottom: 1px solid var(--border-color);
}

table tr:hover {
    background-color: var(--bg-tertiary);
}

/* Grid View */
.grid-container {
    display: table;
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
}

.grid-row {
    display: table-row;
}

.network-label {
    display: table-cell;
    vertical-align: middle;
    text-align: center;
    font-weight: 600;
    width: 60px;
    min-width: 60px;
    height: 34px;
    background-color: var(--bg-tertiary);
    border-radius: 6px;
    color: var(--text-secondary);
    box-shadow: var(--shadow-sm);
    padding: 4px;
    margin: 2px;
    border: 1px solid var(--border-color);
}

.stations-container {
    display: table-cell;
    padding-left: 6px;
}

.stations-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 2px 0;
}

.grid-cell {
    width: 60px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    box-shadow: var(--shadow-sm);
    text-decoration: none;
    color: var(--text-primary);
    transition: all 0.15s ease;
    position: relative;
    border: 1px solid var(--border-color);
    background-color: var(--status-good);
}

.grid-cell:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
    z-index: 10;
}

/* Map View */
.map-container {
    width: 100%;
    height: 600px;
    background-color: var(--bg-tertiary);
    border-radius: var(--border-radius);
    margin-bottom: 20px;
    position: relative;
}

/* Status Colors */
/*__STATUS_CLASSES__*/

/* Tooltip */
.grid-cell::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: 120%;
    left: 50%;
    transform: translateX(-50%);
    background-color: #1f2937;
    color: white;
    text-align: center;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    white-space: nowrap;
    z-index: 20;
    opacity: 0;
    visibility: hidden;
    transition: all 0.2s ease;
    pointer-events: none;
    box-shadow: var(--shadow-md);
}

.grid-cell::before {
    content: '';
    position: absolute;
    top: -6px;
    left: 50%;
    transform: translateX(-50%);
    border-width: 6px 6px 0;
    border-style: solid;
    border-color: #1f2937 transparent transparent;
    z-index: 20;
    opacity: 0;
    visibility: hidden;
    transition: all 0.2s ease;
    pointer-events: none;
}

.grid-cell:hover::after,
.grid-cell:hover::before {
    opacity: 1;
    visibility: visible;
}

/* Stats */
.stats-container {
    margin: 20px 0;
    padding: 20px;
    background-color: var(--bg-tertiary);
    border-radius: var(--border-radius);
    display: none;
}

.stats-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}

.stats-title {
    font-weight: 600;
    font-size: 16px;
    color: var(--text-primary);
}

.network-stats {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 15px;
}

.network-stat {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.network-name {
    font-weight: 600;
    font-size: 14px;
    color: var(--text-primary);
    display: flex;
    justify-content: space-between;
}

.progress-bar {
    height: 8px;
    background-color: var(--border-color);
    border-radius: 4px;
    overflow: hidden;
}

.progress {
    height: 100%;
    background-color: var(--primary-color);
    border-radius: 4px;
}

/* Legend */
.legend {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 25px 0;
    padding: 15px;
    background-color: var(--bg-tertiary);
    border-radius: var(--border-radius);
    justify-content: center;
}

.legend-item {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 13px;
    color: var(--text-secondary);
}

.legend-color {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba(0, 0, 0, 0.1);
}

/* Footer */
.footer {
    margin-top: 30px;
    padding-top: 15px;
    border-top: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    color: var(--text-secondary);
    font-size: 14px;
}

.footer a {
    color: var(--primary-color);
    text-decoration: none;
}

.footer a:hover {
    text-decoration: underline;
}

/* Loading */
#loading {
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 30px 0;
    color: var(--text-secondary);
}

.loading-spinner {
    width: 24px;
    height: 24px;
    border: 3px solid var(--bg-tertiary);
    border-top: 3px solid var(--primary-color);
    border-radius: 50%;
    margin-right: 12px;
    animation: spin 1s linear infinite;
}

@keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

/* Error Message */
#error-message {
    padding: 15px;
    margin: 20px 0;
    border-radius: var(--border-radius);
    background-color: #fee2e2;
    color: #b91c1c;
    border-left: 4px solid #ef4444;
    display: none;
}

/* Station Detail Panel (slide-over, replaces per-station page navigation) */
.detail-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    z-index: 1998;
}

.detail-overlay.open {
    display: block;
}

.detail-panel {
    position: fixed;
    top: 0;
    right: 0;
    height: 100%;
    width: 720px;
    max-width: 92vw;
    background-color: var(--bg-primary);
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.2);
    transform: translateX(100%);
    transition: transform 0.25s ease;
    z-index: 1999;
    display: flex;
    flex-direction: column;
}

.detail-panel.open {
    transform: translateX(0);
}

.detail-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border-color);
    font-weight: 600;
    font-size: 16px;
}

.detail-panel-close {
    background: none;
    border: none;
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
    color: var(--text-secondary);
    padding: 4px 8px;
}

.detail-panel-close:hover {
    color: var(--text-primary);
}

.detail-panel-body {
    padding: 16px 20px;
    overflow-y: auto;
    flex: 1;
}

.detail-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
}

.detail-table-wrap {
    overflow-x: auto;
    margin-top: 12px;
}

.detail-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}

.detail-table th, .detail-table td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-color);
    text-align: left;
    white-space: nowrap;
}

@media (max-width: 768px) {
    .detail-panel {
        width: 100vw;
        max-width: 100vw;
    }
}

/* Responsive Design */
@media (max-width: 768px) {
    .container {
        margin: 10px;
        padding: 15px;
        border-radius: 6px;
    }

    .header {
        flex-direction: column;
        align-items: flex-start;
        gap: 10px;
    }

    .view-toggle {
        align-self: flex-end;
    }

    .controls {
        flex-direction: column;
        align-items: stretch;
    }

    .actions {
        justify-content: space-between;
    }

    .refresh-control {
        flex-direction: column;
        align-items: flex-start;
        gap: 10px;
    }

    .filters {
        flex-direction: column;
        gap: 10px;
    }

    .filter-group {
        width: 100%;
    }

    .search-box {
        width: 100%;
    }

    .network-stats {
        grid-template-columns: 1fr;
    }

    .map-container {
            height: 400px;
        }
    }
    /* Marker Cluster Styles */
    .marker-cluster {
        background-clip: padding-box;
        border-radius: 20px;
    }

    .marker-cluster div {
        width: 36px;
        height: 36px;
        margin-left: 2px;
        margin-top: 2px;
        text-align: center;
        border-radius: 18px;
        font-size: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    /* Map Controls */
    .leaflet-control-locate {
        border: 2px solid rgba(0,0,0,0.2);
        background-clip: padding-box;
    }

    .leaflet-control-locate a {
        background-color: var(--bg-primary);
        background-position: 50% 50%;
        background-repeat: no-repeat;
        display: block;
        width: 30px;
        height: 30px;
        line-height: 30px;
        color: var(--text-primary);
        text-align: center;
    }

    .leaflet-control-locate a:hover {
        background-color: var(--bg-tertiary);
        color: var(--primary-color);
    }

    .leaflet-control-locate.active a {
        color: var(--primary-color);
    }

    .leaflet-control-fullscreen {
        border: 2px solid rgba(0,0,0,0.2);
        background-clip: padding-box;
    }

    .leaflet-control-fullscreen a {
        background-color: var(--bg-primary);
        background-position: 50% 50%;
        background-repeat: no-repeat;
        display: block;
        width: 30px;
        height: 30px;
        line-height: 30px;
        color: var(--text-primary);
        text-align: center;
    }

    .leaflet-control-fullscreen a:hover {
        background-color: var(--bg-tertiary);
        color: var(--primary-color);
    }

    /* Map layers control */
    .leaflet-control-layers {
        border-radius: var(--border-radius);
        background-color: var(--bg-primary);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
        box-shadow: var(--shadow-sm);
    }

    .dark-mode .leaflet-control-layers {
        background-color: var(--bg-tertiary);
    }

    .leaflet-control-layers-toggle {
        width: 36px;
        height: 36px;
        background-size: 20px 20px;
    }

    .leaflet-control-layers-expanded {
        padding: 10px;
        background-color: var(--bg-primary);
        color: var(--text-primary);
        border-radius: var(--border-radius);
    }

    .dark-mode .leaflet-control-layers-expanded {
        background-color: var(--bg-tertiary);
    }

    .leaflet-control-layers-list {
        margin-top: 8px;
    }

    .leaflet-control-layers label {
        margin-bottom: 5px;
        display: block;
    }

    /* Map layer selection buttons */
    .map-layers-control {
        position: absolute;
        top: 10px;
        right: 10px;
        z-index: 1000;
        background: white;
        padding: 5px;
        border-radius: 4px;
        box-shadow: 0 1px 5px rgba(0,0,0,0.65);
    }

    .map-layers-control button {
        display: block;
        margin: 5px 0;
        padding: 5px;
        width: 100%;
        border: none;
        background: #f8f8f8;
        cursor: pointer;
    }

    .map-layers-control button:hover {
        background: #f0f0f0;
    }

    .map-layers-control button.active {
        background: #ddd;
        font-weight: bold;
    }

    /* Map tools control */
    .map-tools-control {
        position: absolute;
        bottom: 30px;
        right: 10px;
        z-index: 1000;
        display: flex;
        flex-direction: column;
        gap: 5px;
    }

    .map-tools-control button {
        width: 34px;
        height: 34px;
        background: white;
        border: 2px solid rgba(0,0,0,0.2);
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        color: #333;
    }

    .map-tools-control button:hover {
        background: #f4f4f4;
    }

    .dark-mode .map-tools-control button {
        background: #333;
        color: #fff;
        border-color: rgba(255,255,255,0.2);
    }

    .dark-mode .map-tools-control button:hover {
        background: #444;
    }

    /* Map measurement widget */
    .leaflet-measure-path-measurement {
        position: absolute;
        font-size: 12px;
        color: black;
        text-shadow: -1px 0 white, 0 1px white, 1px 0 white, 0 -1px white;
        white-space: nowrap;
        transform-origin: 0;
        pointer-events: none;
    }

    .dark-mode .leaflet-measure-path-measurement {
        color: white;
        text-shadow: -1px 0 black, 0 1px black, 1px 0 black, 0 -1px black;
    }

    /* Popup styling */
    .leaflet-popup-content-wrapper {
        border-radius: var(--border-radius);
        background-color: var(--bg-primary);
        color: var(--text-primary);
        box-shadow: var(--shadow-md);
    }

    .dark-mode .leaflet-popup-content-wrapper {
        background-color: var(--bg-tertiary);
    }

    .leaflet-popup-content {
        margin: 12px;
        line-height: 1.5;
    }

    .leaflet-popup-tip {
        background-color: var(--bg-primary);
    }

    .dark-mode .leaflet-popup-tip {
        background-color: var(--bg-tertiary);
    }

    .leaflet-popup-content a {
        color: var(--primary-color);
        text-decoration: none;
    }

    .leaflet-popup-content a:hover {
        text-decoration: underline;
    }

    /* Make the map more responsive on mobile */
    @media (max-width: 768px) {
        .map-container {
            height: 450px;
        }

        .leaflet-control-layers,
        .leaflet-control-zoom,
        .leaflet-control-fullscreen,
        .leaflet-control-locate {
            margin-right: 10px !important;
        }

        .leaflet-control-scale {
            margin-bottom: 40px !important;
        }
    }
    """

    status_vars_css = "\n".join(
        f"    --status-{s['name']}: {s['bg']};" for s in STATUS_STYLES
    )

    station_class_blocks = []
    for s in STATUS_STYLES:
        fg = "var(--text-primary)" if s["name"] == "good" else s["fg"]
        border = "var(--border-color)" if s["name"] == "good" else f"var(--status-{s['name']})"
        station_class_blocks.append(
            f".station-{s['name']} {{\n"
            f"    background-color: var(--status-{s['name']});\n"
            f"    color: {fg};\n"
            f"    border-color: {border};\n"
            f"}}"
        )
    station_classes_css = "\n\n".join(station_class_blocks)

    css_content = css_content.replace("/*__STATUS_VARS__*/", status_vars_css)
    css_content = css_content.replace("/*__STATUS_CLASSES__*/", station_classes_css)

    try:
        css_path = os.path.join(config['setup']['wwwdir'], 'styles.css')
        with open(css_path, 'w') as f:
            f.write(css_content)
        print(f"CSS file generated at {css_path}")
        return css_path
    except Exception as e:
        print(f"Error generating CSS file: {str(e)}")
        return None


def generate_js_file(config):
    """Generate the JavaScript file with interactive features"""
    js_content = """
// Global variables
let refreshTimer = null;
let currentRefreshInterval = 60;
let lastRefreshTime = 0;
let isRefreshing = false;
let stationsData = [];
let viewMode = 'table'; // 'table', 'grid', or 'map'
let mapInitialized = false;
let map = null;
let markers = [];

// Minimal HTML-escaping helper for text interpolated into innerHTML
// (station/channel codes, admin-configured per-station text, etc.)
function esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Leaflet plots raw longitude on a single, unwrapped copy of the world, so
// a station at e.g. -169 (Samoa/Tonga area) renders numerically far from
// a Pacific-centered map (lon ~134) even though it's geographically just
// across the +/-180 antimeridian from it -- it ends up drawn near the
// Americas instead of next to Australia/NZ/Fiji. Shift by +/-360 until
// the longitude is within 180 degrees of the map's reference point so
// geographically close stations stay numerically close.
function normalizeLongitude(lon, refLon) {
    let normalized = lon;
    while (normalized - refLon > 180) normalized -= 360;
    while (normalized - refLon < -180) normalized += 360;
    return normalized;
}

function mapRefLon() {
    return (window.mapSettings && window.mapSettings.center && typeof window.mapSettings.center.lon === 'number')
        ? window.mapSettings.center.lon
        : 0;
}

// Function to initialize the application
document.addEventListener('DOMContentLoaded', function() {
    // Load saved preferences
    loadPreferences();

    // Set up event listeners
    setupEventListeners();

    //Add channel type filter
    setupFilters();

    // Set active view based on URL or default
    setActiveView();

    // Initial data load
    fetchData();
});

// Function to load user preferences from localStorage
function loadPreferences() {
    // Load refresh interval
    const savedInterval = parseInt(localStorage.getItem('seedlinkRefreshInterval'));
    if (savedInterval && savedInterval >= 10) {
        document.getElementById('refresh-interval').value = savedInterval;
        currentRefreshInterval = savedInterval;
    }

    // Load dark mode preference
    const darkModeEnabled = localStorage.getItem('seedlink-dark-mode') === 'true';
    if (darkModeEnabled) {
        document.body.classList.add('dark-mode');
        updateThemeToggleButton(true);
    }

    // Load view mode
    const savedViewMode = localStorage.getItem('seedlink-view-mode');
    if (savedViewMode) {
        viewMode = savedViewMode;
    }
}

// Function to set up all event listeners
function setupEventListeners() {
    // View toggle buttons
    document.querySelectorAll('.view-toggle a').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const view = this.getAttribute('data-view');
            switchView(view);
        });
    });

    // Refresh controls
    document.getElementById('apply-refresh').addEventListener('click', function() {
        const interval = parseInt(document.getElementById('refresh-interval').value);
        if (interval && interval >= 10) {
            updateRefreshInterval(interval);
        }
    });

    document.getElementById('refresh-now').addEventListener('click', function() {
        if (refreshTimer) {
            clearTimeout(refreshTimer);
        }
        fetchData();
    });

    // Theme toggle
    document.getElementById('theme-toggle').addEventListener('click', toggleDarkMode);

    // Export CSV
    document.getElementById('export-csv').addEventListener('click', exportToCsv);

    // Stats toggle
    document.getElementById('stats-toggle').addEventListener('click', toggleStats);
    document.getElementById('close-stats').addEventListener('click', function() {
        document.getElementById('stats-container').style.display = 'none';
    });

    // Filter inputs
    document.getElementById('network-filter').addEventListener('change', applyFilters);
    document.getElementById('status-filter').addEventListener('change', applyFilters);
    document.getElementById('search-input').addEventListener('input', debounce(applyFilters, 300));

    // Sort headers in table view
    document.querySelectorAll('th[data-sort]').forEach(header => {
        header.addEventListener('click', function() {
            sortTable(this.getAttribute('data-sort'));
        });
    });

    // Handle visibility changes (tab switching)
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') {
            // If data is stale (not refreshed in over half the interval)
            const timeSinceLastRefresh = Date.now() - lastRefreshTime;
            if (timeSinceLastRefresh > (currentRefreshInterval * 500)) {
                if (refreshTimer) {
                    clearTimeout(refreshTimer);
                }
                fetchData();
            }
        }
    });

    // Station detail panel: close button, backdrop click, Escape key
    document.getElementById('detail-panel-close').addEventListener('click', hideDetail);
    document.getElementById('detail-overlay').addEventListener('click', hideDetail);
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && document.getElementById('detail-panel').classList.contains('open')) {
            hideDetail();
        }
    });
}

// Function to switch between views
function switchView(view) {
    viewMode = view;

    // Update URL without reloading the page
    const url = new URL(window.location);
    url.searchParams.set('view', view);
    window.history.pushState({}, '', url);

    setActiveView();

    // Refresh data display for the new view
    renderData();
}

// Function to toggle dark mode
function toggleDarkMode() {
    document.body.classList.toggle('dark-mode');
    const isDarkMode = document.body.classList.contains('dark-mode');
    localStorage.setItem('seedlink-dark-mode', isDarkMode ? 'true' : 'false');

    updateThemeToggleButton(isDarkMode);

    // Update map tiles if map is initialized
    if (mapInitialized && map) {
        updateMapTiles(isDarkMode);
    }
}

// Function to update theme toggle button appearance
function updateThemeToggleButton(isDarkMode) {
    const themeToggle = document.getElementById('theme-toggle');
    if (isDarkMode) {
        themeToggle.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="5"></circle>
                <line x1="12" y1="1" x2="12" y2="3"></line>
                <line x1="12" y1="21" x2="12" y2="23"></line>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                <line x1="1" y1="12" x2="3" y2="12"></line>
                <line x1="21" y1="12" x2="23" y2="12"></line>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
            </svg>
            Light Mode
        `;
    } else {
        themeToggle.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
            </svg>
            Dark Mode
        `;
    }
}

// Function to initialize the map view
// 1. Enhanced map initialization function
// Updated initializeMap function with markerCluster safety checks
function initializeMap() {
    // Check if Leaflet is available
    if (typeof L === 'undefined') {
        console.error('Leaflet library not loaded');
        document.getElementById('map-container').innerHTML = '<div class="error-message">Map library not available. Please check your internet connection.</div>';
        return;
    }

    // Initialize markerCluster as null so it's defined even if the plugin isn't available
    markerCluster = null;

    // Read map settings from the page data if available
    const mapSettings = window.mapSettings || {
        center: { lat: 20, lon: 0, zoom: 2 },
        defaultLayer: 'street',
        enableClustering: true,
        showFullscreenControl: true,
        showLayerControl: true,
        showLocateControl: true
    };

    // Create map instance
    map = L.map('map-container', {
        center: [mapSettings.center.lat, mapSettings.center.lon],
        zoom: mapSettings.center.zoom,
        zoomControl: false // We'll add this separately for better positioning
    });

    // Define available base layers
    const baseLayers = {
        'Street': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19
        }),
        'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Imagery &copy; Esri &copy; ArcGIS',
            maxZoom: 19
        }),
        'Terrain': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
            maxZoom: 17
        }),
        'Dark': L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: 'abcd',
            maxZoom: 19
        })
    };

    // Add appropriate layer based on settings or dark mode
    const isDarkMode = document.body.classList.contains('dark-mode');
    let defaultLayer = isDarkMode ? 'Dark' : (mapSettings.defaultLayer || 'Street');
    defaultLayer = defaultLayer.charAt(0).toUpperCase() + defaultLayer.slice(1); // Capitalize

    // Add the default layer to the map
    if (baseLayers[defaultLayer]) {
        baseLayers[defaultLayer].addTo(map);
    } else {
        // Fallback to the first available layer
        baseLayers[Object.keys(baseLayers)[0]].addTo(map);
    }

    // Add layer control if enabled
    if (mapSettings.showLayerControl !== false) {
        L.control.layers(baseLayers, {}, {
            position: 'topright',
            collapsed: true
        }).addTo(map);
    }

    // Add zoom control in a better position
    L.control.zoom({
        position: 'bottomright'
    }).addTo(map);

    // Add scale control
    L.control.scale().addTo(map);

    // Add fullscreen control if enabled and the plugin is available
    if (mapSettings.showFullscreenControl !== false && typeof L.Control.Fullscreen !== 'undefined') {
        L.control.fullscreen({
            position: 'topright',
            title: {
                'false': 'View Fullscreen',
                'true': 'Exit Fullscreen'
            }
        }).addTo(map);
    }

    // Add locate control if enabled and the plugin is available
    if (mapSettings.showLocateControl !== false && typeof L.Control.Locate !== 'undefined') {
        L.control.locate({
            position: 'bottomright',
            icon: 'fa fa-location-arrow',
            strings: {
                title: 'Show my location'
            },
            locateOptions: {
                enableHighAccuracy: true,
                maxZoom: 10
            }
        }).addTo(map);
    }

    // Initialize marker cluster group if enabled and the plugin is available
    if (mapSettings.enableClustering !== false && typeof L.MarkerClusterGroup !== 'undefined') {
        try {
            markerCluster = L.markerClusterGroup({
                disableClusteringAtZoom: 10,
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false,
                iconCreateFunction: function(cluster) {
                    const count = cluster.getChildCount();

                    // Determine color based on worst status in the cluster.
                    // Severity rank comes from window.STATUS_LEGEND (same
                    // server-generated, single-source-of-truth order used
                    // for colors elsewhere) instead of a separately
                    // hand-maintained copy of the ordering.
                    const severityRank = name => window.STATUS_LEGEND.findIndex(s => s.name === name);
                    let worstStatus = 'good';
                    for (const marker of cluster.getAllChildMarkers()) {
                        const status = marker.options.status || 'good';
                        if (severityRank(status) > severityRank(worstStatus)) {
                            worstStatus = status;
                        }
                    }

                    // Get color for worst status
                    const color = getStatusColor(worstStatus);

                    const textColor = getBestTextColor(color);

                    return L.divIcon({
                        html: `<div style="background-color: ${color}; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; border-radius: 50%; border: 2px solid white; color: ${textColor}; font-weight: bold;">${count}</div>`,
                        className: 'marker-cluster',
                        iconSize: new L.Point(40, 40)
                    });
                }
            });

            map.addLayer(markerCluster);
            console.log("Marker clustering initialized successfully");
        } catch (e) {
            console.error("Error initializing marker clustering:", e);
            markerCluster = null; // Reset to null if initialization failed
        }
    } else {
        console.log("Marker clustering is disabled or not available");
    }

    // Mark as initialized
    mapInitialized = true;

    // Update markers if we already have data
    if (stationsData.length > 0) {
        updateMapMarkers();
    }
}

// Helper function to determine best text color (black or white) based on background color
function getBestTextColor(bgColor) {
    // Handle named colors
    if (bgColor.toLowerCase() === '#ffffff') return '#000000';
    if (bgColor.toLowerCase() === '#000000') return '#ffffff';

    // Convert hex to rgb
    let hex = bgColor.replace('#', '');
    let r, g, b;

    if (hex.length === 3) {
        r = parseInt(hex.charAt(0) + hex.charAt(0), 16);
        g = parseInt(hex.charAt(1) + hex.charAt(1), 16);
        b = parseInt(hex.charAt(2) + hex.charAt(2), 16);
    } else {
        r = parseInt(hex.substring(0, 2), 16);
        g = parseInt(hex.substring(2, 4), 16);
        b = parseInt(hex.substring(4, 6), 16);
    }

    // Calculate luminance
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

    // Return white for dark backgrounds, black for light backgrounds
    return luminance > 0.5 ? '#000000' : '#ffffff';
}

function updateMapTiles(isDarkMode) {
    if (!map) return;

    // Get available layers from map's layer control
    const baseLayers = {};
    map.eachLayer(layer => {
        if (layer instanceof L.TileLayer) {
            map.removeLayer(layer);
        }
    });

    // Add the default layer based on theme
    if (isDarkMode) {
        if (window.mapSettings && window.mapSettings.darkModeLayer) {
            // Use configured dark mode layer
            const darkLayer = window.mapSettings.darkModeLayer.toLowerCase();
            if (darkLayer === 'satellite') {
                L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                    attribution: 'Imagery &copy; Esri &copy; ArcGIS',
                    maxZoom: 19
                }).addTo(map);
            } else {
                L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                    subdomains: 'abcd',
                    maxZoom: 19
                }).addTo(map);
            }
        } else {
            // Default dark theme
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                subdomains: 'abcd',
                maxZoom: 19
            }).addTo(map);
        }
    } else {
        if (window.mapSettings && window.mapSettings.lightModeLayer) {
            // Use configured light mode layer
            const lightLayer = window.mapSettings.lightModeLayer.toLowerCase();
            if (lightLayer === 'satellite') {
                L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                    attribution: 'Imagery &copy; Esri &copy; ArcGIS',
                    maxZoom: 19
                }).addTo(map);
            } else if (lightLayer === 'terrain') {
                L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
                    attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
                    maxZoom: 17
                }).addTo(map);
            } else {
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                    maxZoom: 19
                }).addTo(map);
            }
        } else {
            // Default light theme
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 19
            }).addTo(map);
        }
    }
}

function updateMapMarkers() {
    if (!mapInitialized || !map) return;

    // Clear existing markers
    if (markerCluster) {
        try {
            markerCluster.clearLayers();
        } catch (e) {
            console.error("Error clearing marker cluster:", e);
            // Fall back to standard markers if cluster fails
            markerCluster = null;
            markers.forEach(marker => {
                try { map.removeLayer(marker); } catch(e) {}
            });
        }
    } else {
        markers.forEach(marker => {
            try { map.removeLayer(marker); } catch(e) {}
        });
    }
    markers = [];

    // Variables to track bounds for auto-zooming
    let validCoordinates = false;
    const bounds = L.latLngBounds();

    // Add markers for each station
    stationsData.forEach(station => {
        // Skip stations without coordinates
        if (!station.coordinates || !station.coordinates.lat || !station.coordinates.lon) {
            console.log(`Station ${station.network}_${station.station} has no coordinates`);
            return;
        }

        validCoordinates = true;
        const lat = station.coordinates.lat;
        const lon = station.coordinates.lon;
        // Only the plotted position needs the antimeridian-normalized
        // longitude; popups/labels should keep showing the real value.
        const plotLon = normalizeLongitude(lon, mapRefLon());

        // Add to bounds for auto-zooming
        bounds.extend([lat, plotLon]);

        // Create marker with appropriate color based on status
        const markerColor = getStatusColor(station.status);

        // Create marker with a badge if it's using LH channels
        const isLH = station.primaryChannelType === 'LH';
        const markerIcon = L.divIcon({
            html: isLH
                ? `<div style="background-color: ${markerColor}; width: 14px; height: 14px; border-radius: 50%; border: 2px solid white; position: relative;">
                      <span style="position: absolute; top: -8px; right: -8px; background: #f3f4f6; border-radius: 50%; width: 10px; height: 10px; font-size: 7px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #1f2937;">L</span>
                   </div>`
                : `<div style="background-color: ${markerColor}; width: 14px; height: 14px; border-radius: 50%; border: 2px solid white;"></div>`,
            className: 'station-marker',
            iconSize: [18, 18],
            iconAnchor: [9, 9]
        });

        const marker = L.marker([lat, plotLon], {
            icon: markerIcon,
            title: `${station.network}_${station.station}`,
            status: station.status // Store status for cluster coloring
        });

        // Create channel group summary for popup
        const channelGroupsText = station.channels.reduce((groups, channel) => {
            const type = channel.channelType || 'other';
            if (!groups[type]) groups[type] = 0;
            groups[type]++;
            return groups;
        }, {});

        const channelGroupsHTML = Object.entries(channelGroupsText)
            .map(([type, count]) => `${type}: ${count}`)
            .join(', ');

        // Add popup with station info
        marker.bindPopup(`
            <strong>${station.network}_${station.station}</strong><br>
            Primary channel type: <b>${station.primaryChannelType || 'N/A'}</b><br>
            Status: ${formatStatus(station.status, station.primaryChannelType)}<br>
            Latency: ${formatLatency(station.latency)}<br>
            Channels: ${channelGroupsHTML}<br>
            Coordinates: ${lat.toFixed(4)}, ${lon.toFixed(4)}
            ${station.coordinates.elevation ? '<br>Elevation: ' + station.coordinates.elevation.toFixed(1) + ' m' : ''}
            <br><a href="#" onclick="showDetail('${station.network}_${station.station}');return false">View Details →</a>
        `);

        // Add to the cluster group or directly to the map
        try {
            if (markerCluster) {
                markerCluster.addLayer(marker);
            } else {
                marker.addTo(map);
            }
            markers.push(marker);
        } catch (e) {
            console.error("Error adding marker:", e);
            // If cluster fails, try adding directly to map
            try {
                marker.addTo(map);
                markers.push(marker);
            } catch (e2) {
                console.error("Also failed to add directly to map:", e2);
            }
        }
    });

    // Auto-zoom to fit all markers if we have valid coordinates
    if (validCoordinates && markers.length > 0) {
        // Don't zoom too close if there's only one station
        if (markers.length === 1) {
            map.setView(bounds.getCenter(), 8);
        } else {
            try {
                map.fitBounds(bounds, {
                    padding: [30, 30],
                    maxZoom: 12
                });
            } catch (e) {
                console.error("Error fitting bounds:", e);
                // Fallback to a default view
                map.setView([20, 0], 2);
            }
        }
    } else if (!validCoordinates && markers.length === 0) {
        // Show message if no stations have coordinates
        const noCoordinatesMsg = document.createElement('div');
        noCoordinatesMsg.className = 'error-message';
        noCoordinatesMsg.style.position = 'absolute';
        noCoordinatesMsg.style.top = '50%';
        noCoordinatesMsg.style.left = '50%';
        noCoordinatesMsg.style.transform = 'translate(-50%, -50%)';
        noCoordinatesMsg.style.background = 'rgba(255, 255, 255, 0.9)';
        noCoordinatesMsg.style.padding = '15px';
        noCoordinatesMsg.style.borderRadius = '8px';
        noCoordinatesMsg.style.zIndex = 1000;
        noCoordinatesMsg.innerHTML = `
            <p><strong>No station coordinates available</strong></p>
            <p>Make sure your FDSNWS service is properly configured and accessible.</p>
        `;
        document.getElementById('map-container').appendChild(noCoordinatesMsg);
    }
}

// Custom legend for the map
function addMapLegend() {
    if (!mapInitialized || !map) return;

    // Remove existing legend if any
    const existingLegend = document.querySelector('.map-legend');
    if (existingLegend) {
        existingLegend.remove();
    }

    // Create a custom legend
    const legend = L.control({position: 'bottomright'});

    legend.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-legend');
        // Built from window.STATUS_LEGEND/STATUS_COLORS (same source as the
        // CSS and getStatusColor()) so this legend can't fall out of sync
        // with what the markers actually show, and covers all statuses
        // instead of a hand-picked subset.
        const rows = window.STATUS_LEGEND.map(s =>
            `<div><span style="background-color: ${getStatusColor(s.name)}"></span> ${s.caption}</div>`
        ).join('');
        div.innerHTML = `<h4>Station Status</h4>${rows}`;

        // Add custom styles to the legend
        const style = document.createElement('style');
        style.textContent = `
            .map-legend {
                padding: 10px;
                background: white;
                background: rgba(255, 255, 255, 0.9);
                border-radius: 5px;
                line-height: 1.8;
                color: #333;
                box-shadow: 0 0 15px rgba(0, 0, 0, 0.2);
            }
            .dark-mode .map-legend {
                background: rgba(31, 41, 55, 0.9);
                color: #f9fafb;
            }
            .map-legend h4 {
                margin: 0 0 5px;
                font-size: 14px;
                font-weight: 600;
            }
            .map-legend div {
                display: flex;
                align-items: center;
                font-size: 12px;
                margin-bottom: 3px;
            }
            .map-legend span {
                display: inline-block;
                width: 16px;
                height: 16px;
                margin-right: 8px;
                border-radius: 50%;
                border: 1px solid rgba(0, 0, 0, 0.2);
            }
            .dark-mode .map-legend span {
                border-color: rgba(255, 255, 255, 0.2);
            }
        `;

        div.appendChild(style);
        return div;
    };

    legend.addTo(map);
}
function setupFilters() {
    // Add a channel type filter to the filters area
    const filtersArea = document.querySelector('.filters');

    if (!filtersArea) return;

    // Check if the filter already exists
    if (!document.getElementById('channel-filter')) {
        const channelFilterGroup = document.createElement('div');
        channelFilterGroup.className = 'filter-group';
        channelFilterGroup.innerHTML = `
            <label for="channel-filter">Channel Type:</label>
            <select id="channel-filter">
                <option value="">All Types</option>
                <option value="HH">HH (High Frequency)</option>
                <option value="BH">BH (Broadband)</option>
                <option value="LH">LH (Long Period)</option>
                <option value="SH">SH (Short Period)</option>
                <option value="EH">EH (Extremely Short Period)</option>
                <option value="other">Other</option>
            </select>
        `;

        filtersArea.appendChild(channelFilterGroup);

        // Add event listener
        document.getElementById('channel-filter').addEventListener('change', applyFilters);
    }
}

// Enhanced station filters for the map
function setupMapFilters() {
    if (!mapInitialized || !map) return;

    const mapFilters = L.control({position: 'topleft'});

    mapFilters.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-filters');
        div.innerHTML = `
            <div class="filter-select">
                <label for="map-network-filter">Network:</label>
                <select id="map-network-filter">
                    <option value="">All Networks</option>
                </select>
            </div>
            <div class="filter-select">
                <label for="map-status-filter">Status:</label>
                <select id="map-status-filter">
                    <option value="">All Statuses</option>
                    <option value="good">Good</option>
                    <option value="warning">Warning</option>
                    <option value="critical">Critical</option>
                    <option value="unavailable">Unavailable</option>
                </select>
            </div>
        `;

        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .map-filters {
                padding: 10px;
                background: rgba(255, 255, 255, 0.9);
                border-radius: 5px;
                box-shadow: 0 0 15px rgba(0, 0, 0, 0.2);
                width: 200px;
            }
            .dark-mode .map-filters {
                background: rgba(31, 41, 55, 0.9);
                color: #f9fafb;
            }
            .map-filters .filter-select {
                margin-bottom: 8px;
            }
            .map-filters label {
                display: block;
                margin-bottom: 3px;
                font-weight: 500;
                font-size: 12px;
            }
            .map-filters select {
                width: 100%;
                padding: 4px 8px;
                border-radius: 4px;
                border: 1px solid #ddd;
                font-size: 12px;
            }
            .dark-mode .map-filters select {
                background: #374151;
                color: #f9fafb;
                border-color: #4b5563;
            }
        `;

        div.appendChild(style);

        // Prevent map interactions when using the filters
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);

        // Setup network filter options
        const networkFilter = div.querySelector('#map-network-filter');
        const networks = [...new Set(stationsData.map(station => station.network))].sort();

        networks.forEach(network => {
            const option = document.createElement('option');
            option.value = network;
            option.textContent = network;
            networkFilter.appendChild(option);
        });

        // Add event listeners
        networkFilter.addEventListener('change', function() {
            const selectedNetwork = this.value;
            updateMapMarkersFilter(selectedNetwork, div.querySelector('#map-status-filter').value);
        });

        div.querySelector('#map-status-filter').addEventListener('change', function() {
            const selectedStatus = this.value;
            updateMapMarkersFilter(networkFilter.value, selectedStatus);
        });

        return div;
    };

    mapFilters.addTo(map);
}

// Filter map markers based on selected criteria
function updateMapMarkersFilter(network, status) {
    if (!mapInitialized || !map) return;

    // Clear existing markers
    markers.forEach(marker => map.removeLayer(marker));
    markers = [];

    // Apply filters to data
    let filteredData = stationsData;

    if (network) {
        filteredData = filteredData.filter(station => station.network === network);
    }

    if (status) {
        filteredData = filteredData.filter(station => {
            if (status === 'good') {
                return station.status === 'good';
            } else if (status === 'warning') {
                return ['delayed', 'long-delayed', 'very-delayed', 'hour-delayed', 'warning'].includes(station.status);
            } else if (status === 'critical') {
                return ['critical', 'day-delayed', 'multi-day', 'three-day', 'four-day'].includes(station.status);
            } else if (status === 'unavailable') {
                return station.status === 'unavailable';
            }
            return true;
        });
    }

    // Add filtered markers
    const bounds = L.latLngBounds();
    let validCoordinates = false;

    filteredData.forEach(station => {
        // Skip stations without coordinates
        if (!station.coordinates || !station.coordinates.lat || !station.coordinates.lon) return;

        validCoordinates = true;
        const lat = station.coordinates.lat;
        const lon = station.coordinates.lon;
        // Only the plotted position needs the antimeridian-normalized
        // longitude; popups/labels should keep showing the real value.
        const plotLon = normalizeLongitude(lon, mapRefLon());

        // Add to bounds for auto-zooming
        bounds.extend([lat, plotLon]);

        // Create marker with appropriate color
        const markerColor = getStatusColor(station.status);
        const markerIcon = L.divIcon({
            html: `<div style="background-color: ${markerColor}; width: 14px; height: 14px; border-radius: 50%; border: 2px solid white;"></div>`,
            className: 'station-marker',
            iconSize: [18, 18],
            iconAnchor: [9, 9]
        });

        const marker = L.marker([lat, plotLon], {
            icon: markerIcon,
            title: `${station.network}_${station.station}`
        });

        // Add popup with station info
        marker.bindPopup(`
            <strong>${station.network}_${station.station}</strong><br>
            Status: ${formatStatus(station.status)}<br>
            Latency: ${formatLatency(station.latency)}<br>
            Coordinates: ${lat.toFixed(4)}, ${lon.toFixed(4)}
            ${station.coordinates.elevation ? '<br>Elevation: ' + station.coordinates.elevation.toFixed(1) + ' m' : ''}
            <br><a href="#" onclick="showDetail('${station.network}_${station.station}');return false">View Details →</a>
        `);

        marker.addTo(map);
        markers.push(marker);
    });

    // Auto-zoom to fit all markers if we have valid coordinates
    if (validCoordinates && markers.length > 0) {
        // Don't zoom too close if there's only one station
        if (markers.length === 1) {
            map.setView(bounds.getCenter(), 8);
        } else {
            map.fitBounds(bounds, {
                padding: [30, 30],
                maxZoom: 12
            });
        }
    }
}

// Sets active view based on URL or saved preference, including map init
function setActiveView() {
    // Extract view from URL if present
    const urlParams = new URLSearchParams(window.location.search);
    const urlView = urlParams.get('view');

    if (urlView && ['table', 'grid', 'map'].includes(urlView)) {
        viewMode = urlView;
    }

    // Set active class on the appropriate link
    document.querySelectorAll('.view-toggle a').forEach(link => {
        if (link.getAttribute('data-view') === viewMode) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });

    // Show the appropriate view container
    document.querySelectorAll('.view-container').forEach(container => {
        if (container.id === `${viewMode}-view`) {
            container.style.display = 'block';
        } else {
            container.style.display = 'none';
        }
    });

    // Initialize map if needed
    if (viewMode === 'map') {
        if (!mapInitialized && typeof L !== 'undefined') {
            initializeMap();
            // Add map-specific UI elements after initialization
            setTimeout(() => {
                addMapLegend();
                setupMapFilters();
            }, 100);
        } else if (mapInitialized) {
            // If map is already initialized, ensure it's up to date
            map.invalidateSize();
            updateMapMarkers();
        }
    }

    // Save preference
    localStorage.setItem('seedlink-view-mode', viewMode);
}

// Function to fetch data from the server
function fetchData() {
    if (isRefreshing) return;

    isRefreshing = true;
    lastRefreshTime = Date.now();

    // Show loading state
    //document.getElementById('loading').style.display = 'flex';
    document.getElementById('error-message').style.display = 'none';
    document.getElementById('refresh-status').textContent = 'Refreshing...';

    // Use cache-busting to prevent stale data
    const timestamp = Date.now();

    // Fetch the JSON data
    fetch(`stations_data.json?_=${timestamp}`, {
        cache: 'no-cache',
        headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        stationsData = data;

        // Update the filter dropdowns
        updateFilters();

        // Render the data based on current view
        renderData();

        // Keep an already-open detail panel's numbers fresh, and open one
        // for a ?station= deep link on the very first successful load.
        refreshOpenDetailPanel();
        checkPendingDeepLink();

        // Update timestamp
        const updateTime = new Date().toUTCString();
        document.getElementById('update-time').textContent = updateTime;
        document.getElementById('refresh-status').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();

        // Hide loading state
        document.getElementById('loading').style.display = 'none';
        document.getElementById('error-message').style.display = 'none';

        // Setup next refresh
        setupNextRefresh();
    })
    .catch(error => {
        console.error('Error fetching data:', error);
        document.getElementById('error-message').textContent = `Error loading data: ${error.message}`;
        document.getElementById('error-message').style.display = 'block';
        document.getElementById('loading').style.display = 'none';

        // Still setup next refresh to try again
        setupNextRefresh();
    })
    .finally(() => {
        isRefreshing = false;
    });
}

// Function to update the filter dropdowns based on available data
function updateFilters() {
    // Get unique networks
    const networks = [...new Set(stationsData.map(station => station.network))].sort();

    // Update network filter
    const networkFilter = document.getElementById('network-filter');
    const selectedNetwork = networkFilter.value;

    // Clear existing options except the first one
    while (networkFilter.options.length > 1) {
        networkFilter.remove(1);
    }

    // Add new options
    networks.forEach(network => {
        const option = document.createElement('option');
        option.value = network;
        option.textContent = network;
        networkFilter.appendChild(option);
    });

    // Restore selection if possible
    if (selectedNetwork && networks.includes(selectedNetwork)) {
        networkFilter.value = selectedNetwork;
    }
}

// Function to apply filters to the data
function applyFilters() {
    const networkFilter = document.getElementById('network-filter').value;
    const statusFilter = document.getElementById('status-filter').value;
    const channelFilter = document.getElementById('channel-filter')?.value || '';
    const searchText = document.getElementById('search-input').value.toLowerCase();

    // Apply filters to data
    let filteredData = stationsData;

    if (networkFilter) {
        filteredData = filteredData.filter(station => station.network === networkFilter);
    }

    if (statusFilter) {
        filteredData = filteredData.filter(station => {
            if (statusFilter === 'good') {
                return station.status === 'good';
            } else if (statusFilter === 'warning') {
                return ['delayed', 'long-delayed', 'very-delayed', 'hour-delayed', 'warning'].includes(station.status);
            } else if (statusFilter === 'critical') {
                return ['critical', 'day-delayed', 'multi-day', 'three-day', 'four-day'].includes(station.status);
            } else if (statusFilter === 'unavailable') {
                return station.status === 'unavailable';
            }
            return true;
        });
    }

    if (channelFilter) {
        filteredData = filteredData.filter(station =>
            station.primaryChannelType === channelFilter ||
            (channelFilter === 'other' && !['HH', 'BH', 'LH', 'SH', 'EH'].includes(station.primaryChannelType))
        );
    }

    if (searchText) {
        filteredData = filteredData.filter(station =>
            `${station.network}_${station.station}`.toLowerCase().includes(searchText)
        );
    }

    // Render filtered data
    renderData(filteredData);
}

// Function to render the data in the current view
function renderData(data = stationsData) {
    // Default to all data if not specified
    const displayData = data || stationsData;

    // Render based on current view mode
    if (viewMode === 'table') {
        renderTableView(displayData);
    } else if (viewMode === 'grid') {
        renderGridView(displayData);
    } else if (viewMode === 'map') {
        // Update map markers if map is initialized
        if (mapInitialized) {
            updateMapMarkers();
        }
    }
}

// Function to render table view
function renderTableView(data) {
    const tableBody = document.getElementById('table-body');
    tableBody.innerHTML = '';

    data.forEach(station => {
        const row = document.createElement('tr');

        // Network-Station cell
        const nameCell = document.createElement('td');
        nameCell.innerHTML = `<small>${esc(station.network)}</small> <a href="#" onclick="showDetail('${station.network}_${station.station}');return false">${esc(station.station)}</a>`;
        // Add a badge for primary channel type
        if (station.primaryChannelType) {
            nameCell.innerHTML += ` <span class="channel-badge">${station.primaryChannelType}</span>`;
        }
        row.appendChild(nameCell);

        // Status cell
        const statusCell = document.createElement('td');
        statusCell.classList.add(`station-${station.status}`);
        statusCell.textContent = formatStatus(station.status, station.primaryChannelType);
        row.appendChild(statusCell);

        // Latency cell
        const latencyCell = document.createElement('td');
        latencyCell.textContent = formatLatency(station.latency);
        latencyCell.style.backgroundColor = getStatusColor(station.status);
        row.appendChild(latencyCell);

        // Channels cell
        const channelsCell = document.createElement('td');

        // Create channel type summary
        const channelGroups = {};
        station.channels.forEach(channel => {
            const type = channel.channelType || 'other';
            if (!channelGroups[type]) {
                channelGroups[type] = 0;
            }
            channelGroups[type]++;
        });

        // Format channel groups
        const groupsHTML = Object.keys(channelGroups).map(type =>
            `<span class="channel-group">${type}: ${channelGroups[type]}</span>`
        ).join(' ');

        channelsCell.innerHTML = `<div>${station.channels.length} total</div><div class="channel-groups">${groupsHTML}</div>`;
        row.appendChild(channelsCell);

        // Last updated cell
        const lastDataCell = document.createElement('td');
        if (station.channels.length > 0 && station.channels[0].last_data) {
            const lastDataTime = new Date(station.channels[0].last_data);
            lastDataCell.textContent = lastDataTime.toLocaleString();
        } else {
            lastDataCell.textContent = 'Unknown';
        }
        row.appendChild(lastDataCell);

        tableBody.appendChild(row);
    });

    // Add CSS for channel badges if not already added
    if (!document.getElementById('channel-badges-css')) {
        const style = document.createElement('style');
        style.id = 'channel-badges-css';
        style.textContent = `
            .channel-badge {
                background-color: var(--bg-tertiary);
                color: var(--text-secondary);
                font-size: 10px;
                padding: 2px 4px;
                border-radius: 4px;
                margin-left: 4px;
                font-weight: 500;
                vertical-align: middle;
            }

            .channel-groups {
                display: flex;
                flex-wrap: wrap;
                gap: 4px;
                margin-top: 2px;
                font-size: 11px;
            }

            .channel-group {
                background-color: var(--bg-tertiary);
                border-radius: 3px;
                padding: 1px 4px;
                color: var(--text-secondary);
            }
        `;
        document.head.appendChild(style);
    }
}

// Function to render grid view
function renderGridView(data) {
    const gridContainer = document.getElementById('grid-container');
    gridContainer.innerHTML = '';

    // Group stations by network
    const networks = {};
    data.forEach(station => {
        if (!networks[station.network]) {
            networks[station.network] = [];
        }
        networks[station.network].push(station);
    });

    // Sort networks by name
    const sortedNetworks = Object.keys(networks).sort();

    for (const network of sortedNetworks) {
        const stations = networks[network];

        // Create a row for the network
        const networkRow = document.createElement('div');
        networkRow.className = 'grid-row';

        // Add network label as a separate cell
        const networkLabel = document.createElement('div');
        networkLabel.className = 'network-label';
        networkLabel.textContent = network;
        networkRow.appendChild(networkLabel);

        // Create a container for the stations
        const stationsContainer = document.createElement('div');
        stationsContainer.className = 'stations-container';

        // Create a row for the stations
        const stationsRow = document.createElement('div');
        stationsRow.className = 'stations-row';

        // Sort stations by name
        stations.sort((a, b) => a.station.localeCompare(b.station));

        // Add stations
        for (const station of stations) {
            const stationCell = document.createElement('a');
            stationCell.className = `grid-cell station-${station.status}`;
            stationCell.href = '#';
            stationCell.addEventListener('click', function(e) {
                e.preventDefault();
                showDetail(`${station.network}_${station.station}`);
            });
            stationCell.textContent = station.station;
            stationCell.setAttribute('data-tooltip', `${station.network}_${station.station}: ${formatLatency(station.latency)}`);
            stationCell.setAttribute('data-network', station.network);
            stationCell.setAttribute('data-station', station.station);
            stationCell.setAttribute('data-status', station.status);
            stationCell.setAttribute('data-latency', formatLatency(station.latency));
            stationsRow.appendChild(stationCell);
        }

        stationsContainer.appendChild(stationsRow);
        networkRow.appendChild(stationsContainer);
        gridContainer.appendChild(networkRow);
    }
}

// Function to format latency for display
function formatLatency(seconds) {
    if (seconds === null || seconds === undefined) return 'n/a';

    if (seconds > 86400) return `${(seconds/86400).toFixed(1)} d`;
    if (seconds > 3600) return `${(seconds/3600).toFixed(1)} h`;
    if (seconds > 60) return `${(seconds/60).toFixed(1)} m`;
    return `${seconds.toFixed(1)} s`;
}

// Function to format status for display
function formatStatus(status, channelType) {
    const isLH = channelType === 'LH';

    // Add (LH) tag to status labels for LH channels
    const lhSuffix = isLH ? ' (LH)' : '';

    if (status === 'good') return 'Good' + lhSuffix;
    if (status === 'delayed') return 'Delayed (>1m)' + lhSuffix;
    if (status === 'long-delayed') return 'Delayed (>10m)' + lhSuffix;
    if (status === 'very-delayed') return 'Delayed (>30m)' + lhSuffix;
    if (status === 'hour-delayed') return 'Delayed (>1h)' + lhSuffix;
    if (status === 'warning') return 'Warning (>2h)' + lhSuffix;
    if (status === 'critical') return 'Critical (>6h)' + lhSuffix;
    if (status === 'day-delayed') return 'Delayed (>1d)' + lhSuffix;
    if (status === 'multi-day') return 'Delayed (>2d)' + lhSuffix;
    if (status === 'three-day') return 'Delayed (>3d)' + lhSuffix;
    if (status === 'four-day') return 'Delayed (>4d)' + lhSuffix;
    if (status === 'unavailable') return 'Unavailable (>5d)' + lhSuffix;
    return status.charAt(0).toUpperCase() + status.slice(1) + lhSuffix;
}

// Function to get color for a status. Reads from window.STATUS_COLORS,
// injected server-side from the same STATUS_STYLES list that generates
// styles.css's --status-* variables -- one palette, not a second copy
// that can quietly drift from the CSS (which is what used to make the
// Status and Latency cells of the same table row show different colors).
function getStatusColor(status) {
    return (window.STATUS_COLORS && window.STATUS_COLORS[status]) || '#FFFFFF';
}

// ---------------------------------------------------------------------
// Station detail panel: an inline slide-over instead of navigating to a
// separate NET_STA.html page, so the table/grid scroll position, filters,
// and map view survive opening/closing it. Direct/bookmarked links still
// work via index.html?station=NET_STA (see checkPendingDeepLink()).
// ---------------------------------------------------------------------

function findStation(key) {
    return stationsData.find(s => `${s.network}_${s.station}` === key);
}

function showDetail(key) {
    const station = findStation(key);
    if (!station) return;

    document.getElementById('detail-panel-title').textContent = key;
    document.getElementById('detail-panel-body').innerHTML = renderDetailBody(station);
    document.getElementById('detail-panel').classList.add('open');
    document.getElementById('detail-overlay').classList.add('open');

    const url = new URL(window.location);
    url.searchParams.set('station', key);
    window.history.replaceState({}, '', url);
}

function hideDetail() {
    document.getElementById('detail-panel').classList.remove('open');
    document.getElementById('detail-overlay').classList.remove('open');

    const url = new URL(window.location);
    url.searchParams.delete('station');
    window.history.replaceState({}, '', url);
}

// If the currently-open panel's station still exists in freshly-fetched
// data, refresh its contents in place instead of leaving stale numbers
// showing until the user closes and reopens it.
function refreshOpenDetailPanel() {
    const panel = document.getElementById('detail-panel');
    if (!panel.classList.contains('open')) return;
    const key = document.getElementById('detail-panel-title').textContent;
    const station = findStation(key);
    if (station) {
        document.getElementById('detail-panel-body').innerHTML = renderDetailBody(station);
    }
}

// Opens the panel for ?station=NET_STA on the very first successful load
// only (not every refresh), so a link/bookmark opens straight to that
// station without fighting a user who has since closed the panel.
let pendingDeepLinkChecked = false;
function checkPendingDeepLink() {
    if (pendingDeepLinkChecked) return;
    pendingDeepLinkChecked = true;
    const key = new URLSearchParams(window.location.search).get('station');
    if (key && findStation(key)) {
        showDetail(key);
    }
}

function renderDetailBody(station) {
    const key = `${station.network}_${station.station}`;
    const extra = (window.stationExtraInfo && window.stationExtraInfo[key]) || {};

    const rows = station.channels.map(ch => {
        const dataMs = ch.last_data ? new Date(ch.last_data).getTime() : null;
        const feedMs = ch.last_feed ? new Date(ch.last_feed).getTime() : null;
        // Use the server-computed latency (based on the slmon host's UTC
        // clock) rather than Date.now(), so the panel isn't thrown off by
        // clock skew on the viewer's machine.
        const dataLat = ch.latency !== undefined && ch.latency !== null ? ch.latency : null;
        // Feed latency / diff are redundant when last_feed == last_data;
        // blank them rather than show two identical latency numbers.
        const showFeed = dataMs !== null && feedMs !== null && feedMs !== dataMs;
        const diff = showFeed ? (feedMs - dataMs) / 1000 : null;
        // feedLat = dataLat - diff, derived without "now" so it's equally
        // immune to viewer clock skew.
        const feedLat = showFeed && dataLat !== null ? dataLat - diff : null;
        const color = getStatusColor(ch.status);

        return `<tr>
            <td>${ch.location ? esc(ch.location) + '.' : ''}${esc(ch.channel)}</td>
            <td>${dataMs !== null ? new Date(dataMs).toLocaleString() : 'n/a'}</td>
            <td style="background-color:${color}">${formatLatency(dataLat)}</td>
            <td>${feedMs !== null ? new Date(feedMs).toLocaleString() : 'n/a'}</td>
            <td style="background-color:${showFeed ? color : ''}">${showFeed ? formatLatency(feedLat) : ''}</td>
            <td style="background-color:${showFeed ? color : ''}">${showFeed ? formatLatency(diff) : ''}</td>
        </tr>`;
    }).join('');

    // %s -> station code only (legacy, e.g. GEOFON's liveseis.php?station=%s).
    // {netsta} -> "NET.STA" (e.g. GAPS traceview's #/?stations=NET.STA).
    // Both are plain string substitutions in an admin-configured template
    // (config.ini's [setup] liveurl), so which one a deployment needs is a
    // config change, not a code change.
    const liveUrl = window.liveUrlTemplate
        ? window.liveUrlTemplate
              .replace('%s', station.station)
              .replace('{netsta}', `${station.network}.${station.station}`)
        : '';
    const liveUrlHtml = liveUrl
        ? `<p><a href="${esc(liveUrl)}" target="_blank">View live seismogram →</a></p>`
        : '';

    return `
        <div class="panel-status-row">
            <span class="detail-badge station-${station.status}">${formatStatus(station.status, station.primaryChannelType)}</span>
        </div>
        ${extra.info ? `<div style="margin-top:8px">${extra.info}</div>` : ''}
        ${extra.text ? `<p>${extra.text}</p>` : ''}
        <div class="detail-table-wrap">
            <table class="detail-table">
                <thead>
                    <tr><th>Channel</th><th>Last Sample</th><th>Data Latency</th><th>Last Received</th><th>Feed Latency</th><th>Diff</th></tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
        ${liveUrlHtml}
    `;
}

// Function to update the refresh interval
function updateRefreshInterval(seconds) {
    // Update current refresh interval
    currentRefreshInterval = seconds;

    // Clear existing timer
    if (refreshTimer) {
        clearTimeout(refreshTimer);
    }

    // Store the preference in localStorage
    localStorage.setItem('seedlinkRefreshInterval', seconds);

    // Set up next refresh
    setupNextRefresh();

    return seconds;
}

// Function to set up the next refresh
function setupNextRefresh() {
    // Calculate time until next refresh
    const timeUntilNextRefresh = Math.max(1000, (currentRefreshInterval * 1000));

    // Clear existing timer
    if (refreshTimer) {
        clearTimeout(refreshTimer);
    }

    // Set new timer
    refreshTimer = setTimeout(fetchData, timeUntilNextRefresh);

    // Update the display
    document.getElementById('refresh-interval').value = currentRefreshInterval;
    document.getElementById('next-refresh').textContent = currentRefreshInterval;

    // Start countdown
    startRefreshCountdown();
}

// Function to start countdown to next refresh
function startRefreshCountdown() {
    const countdownElement = document.getElementById('next-refresh');
    const currentValue = parseInt(countdownElement.textContent);

    // Clear any existing interval
    if (window.countdownInterval) {
        clearInterval(window.countdownInterval);
    }

    // Set new interval
    window.countdownInterval = setInterval(() => {
        const newValue = parseInt(countdownElement.textContent) - 1;
        if (newValue > 0) {
            countdownElement.textContent = newValue;
        } else {
            clearInterval(window.countdownInterval);
        }
    }, 1000);
}

// Function to export data to CSV
function exportToCsv() {
    // Create CSV content
    let csvContent = 'Network,Station,Status,Latency,Channels,Last Updated\\n';

    stationsData.forEach(station => {
        const lastUpdate = station.channels.length > 0 && station.channels[0].last_data
            ? new Date(station.channels[0].last_data).toISOString()
            : 'Unknown';

        csvContent += `${station.network},${station.station},${station.status},${station.latency},${station.channels.length},${lastUpdate}\\n`;
    });

    // Create download link
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `seedlink-status-${new Date().toISOString().split('T')[0]}.csv`);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    // Clean up
    setTimeout(() => {
        URL.revokeObjectURL(url);
    }, 100);
}

// Function to toggle stats display
function toggleStats() {
    const statsContainer = document.getElementById('stats-container');

    if (statsContainer.style.display === 'block') {
        statsContainer.style.display = 'none';
        return;
    }

    // Show the stats container
    statsContainer.style.display = 'block';

    // Calculate statistics
    calculateStats();
}

// Function to calculate and display statistics
function calculateStats() {
    const networks = {};

    let totalStations = stationsData.length;
    let goodStations = 0;
    let warningStations = 0;
    let criticalStations = 0;
    let unavailableStations = 0;

    // Group by network and count statuses
    stationsData.forEach(station => {
        // Create network entry if it doesn't exist
        if (!networks[station.network]) {
            networks[station.network] = {
                total: 0,
                good: 0,
                warning: 0,
                critical: 0,
                unavailable: 0
            };
        }

        // Count by network
        networks[station.network].total++;

        // Count by status
        if (station.status === 'good') {
            networks[station.network].good++;
            goodStations++;
        } else if (['delayed', 'long-delayed', 'very-delayed', 'hour-delayed', 'warning'].includes(station.status)) {
            networks[station.network].warning++;
            warningStations++;
        } else if (['critical', 'day-delayed', 'multi-day', 'three-day', 'four-day'].includes(station.status)) {
            networks[station.network].critical++;
            criticalStations++;
        } else if (station.status === 'unavailable') {
            networks[station.network].unavailable++;
            unavailableStations++;
        }
    });

    // Update status counter
    const statusCounter = document.getElementById('status-counter');
    statusCounter.innerHTML = `
        <div style="font-weight: 600; margin-bottom: 10px; font-size: 16px;">
            ${totalStations - unavailableStations} active of ${totalStations} total stations
        </div>
        <div style="display: flex; gap: 20px; margin-bottom: 15px; justify-content: center; flex-wrap: wrap;">
            <div>
                <span style="color: var(--text-primary); font-weight: 500;">${goodStations}</span> good
            </div>
            <div>
                <span style="color: var(--status-warning); font-weight: 500;">${warningStations}</span> warning
            </div>
            <div>
                <span style="color: var(--status-critical); font-weight: 500;">${criticalStations}</span> critical
            </div>
            <div>
                <span style="color: var(--status-unavailable); font-weight: 500;">${unavailableStations}</span> unavailable
            </div>
        </div>
    `;

    // Update network stats
    const networkStats = document.getElementById('network-stats');
    networkStats.innerHTML = '';

    Object.keys(networks).sort().forEach(network => {
        const stats = networks[network];
        const activePercentage = Math.round(((stats.total - stats.unavailable) / stats.total) * 100);

        const networkStat = document.createElement('div');
        networkStat.className = 'network-stat';

        // Create the name and count display
        const nameContainer = document.createElement('div');
        nameContainer.className = 'network-name';
        nameContainer.innerHTML = `
            <span>${network}</span>
            <span>${stats.total - stats.unavailable}/${stats.total}</span>
        `;

        // Create the progress bar
        const progressContainer = document.createElement('div');
        progressContainer.className = 'progress-bar';

        const progressBar = document.createElement('div');
        progressBar.className = 'progress';
        progressBar.style.width = `${activePercentage}%`;

        progressContainer.appendChild(progressBar);

        networkStat.appendChild(nameContainer);
        networkStat.appendChild(progressContainer);

        networkStats.appendChild(networkStat);
    });
}

function sortTable(column) {
    // Get current sort direction from the header
    const header = document.querySelector(`th[data-sort="${column}"]`);
    const currentDirection = header.getAttribute('data-direction') || 'asc';
    const newDirection = currentDirection === 'asc' ? 'desc' : 'asc';

    // Update all headers to remove sort indicators
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.setAttribute('data-direction', '');
        th.classList.remove('sort-asc', 'sort-desc');
    });

    // Set direction on current header
    header.setAttribute('data-direction', newDirection);
    header.classList.add(`sort-${newDirection}`);

    // Sort data based on column
    stationsData.sort((a, b) => {
        let valueA, valueB;

        if (column === 'name') {
            valueA = `${a.network}_${a.station}`;
            valueB = `${b.network}_${b.station}`;
        } else if (column === 'status') {
            valueA = a.status;
            valueB = b.status;
        } else if (column === 'latency') {
            valueA = a.latency;
            valueB = b.latency;
        } else if (column === 'channels') {
            valueA = a.channels.length;
            valueB = b.channels.length;
        } else if (column === 'updated') {
            valueA = a.channels.length > 0 ? new Date(a.channels[0].last_data || 0).getTime() : 0;
            valueB = b.channels.length > 0 ? new Date(b.channels[0].last_data || 0).getTime() : 0;
        }

        // Handle string comparison
        if (typeof valueA === 'string') {
            if (newDirection === 'asc') {
                return valueA.localeCompare(valueB);
            } else {
                return valueB.localeCompare(valueA);
            }
        }
        // Handle number comparison
        else {
            if (newDirection === 'asc') {
                return valueA - valueB;
            } else {
                return valueB - valueA;
            }
        }
    });

    // Re-render the table with sorted data
    renderTableView(stationsData);
}

// Utility function for debouncing
function debounce(func, wait) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}
"""

    try:
        js_path = os.path.join(config['setup']['wwwdir'], 'script.js')
        with open(js_path, 'w') as f:
            f.write(js_content)
        print(f"JavaScript file generated at {js_path}")
        return js_path
    except Exception as e:
        print(f"Error generating JavaScript file: {str(e)}")
        return None


def generate_html_base(config, title, active_view):
    """Generate base HTML structure for all pages"""

    # Determine if map plugins should be included
    include_map_plugins = 'enable_map' in config['setup'] and config['setup']['enable_map']

    # Start with the first part using proper variable interpolation
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{config['setup']['title']} - {title}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="styles.css">
    <link rel="shortcut icon" href="{config['setup']['icon']}">
    <!-- meta http-equiv="refresh" content="{int(config['setup']['refresh'])}" -->

    <!-- Include Leaflet for map view -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
"""

    # Include additional map plugins if map is enabled
    if include_map_plugins:
        html += """
    <!-- Leaflet plugins -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.fullscreen@1.6.0/Control.FullScreen.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.locatecontrol@0.76.0/dist/L.Control.Locate.min.css" />

    <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
    <script src="https://unpkg.com/leaflet.fullscreen@1.6.0/Control.FullScreen.js"></script>
    <script src="https://unpkg.com/leaflet.locatecontrol@0.76.0/dist/L.Control.Locate.min.js"></script>
    <script src="https://unpkg.com/leaflet-measure-path@1.5.0/leaflet-measure-path.js"></script>
"""

    # Continue with the second part, but using f-string instead of .format()
    html += f"""
    <script>
        // Single source of truth for status colors, generated from the same
        // STATUS_STYLES list as styles.css's --status-* variables, so this
        // page's JS (getStatusColor, map legend) can never drift from the
        // CSS/table colors the way the old hand-duplicated palettes did.
        window.STATUS_COLORS = {json.dumps(STATUS_COLORS)};
        window.STATUS_LEGEND = {json.dumps([{"name": s["name"], "caption": s["caption"]} for s in STATUS_STYLES])};
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{config['setup']['title']}</h1>
            <div class="view-toggle">
                <a href="index.html" data-view="table" class="{'active' if active_view == 'table' else ''}">Table View</a>
                <a href="index.html?view=grid" data-view="grid" class="{'active' if active_view == 'grid' else ''}">Grid View</a>
                <a href="index.html?view=map" data-view="map" class="{'active' if active_view == 'map' else ''}">Map View</a>
            </div>
        </div>

        <p class="subtitle">Real-time seismic station monitoring dashboard</p>

        <div class="controls">
            <div class="actions">
                <button id="stats-toggle" class="action-button">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                    </svg>
                    Station Stats
                </button>
                <button id="export-csv" class="action-button">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                    </svg>
                    Export CSV
                </button>
                <button id="theme-toggle" class="action-button">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
                    </svg>
                    Dark Mode
                </button>
            </div>

            <div class="refresh-control">
                <div class="input-group">
                    <label for="refresh-interval">Auto refresh:</label>
                    <input type="number" id="refresh-interval" min="10" value="{int(config['setup']['refresh'])}">
                    <span>seconds</span>
                </div>
                <button id="apply-refresh">Apply</button>
                <button id="refresh-now">Refresh Now</button>
                <div class="status-counter">
                    <div id="refresh-status">Last refresh: -</div>
                    <div class="countdown">Next in <span id="next-refresh">{int(config['setup']['refresh'])}</span> seconds</div>
                </div>
            </div>
        </div>
"""

    return html

def generate_main_html(config, status):
    """Generate the main index.html with all three views"""

    html = generate_html_base(config, "Dashboard", "table")

    map_settings = get_map_settings(config)

    # Add filters
    html += f"""
        <script>
            //Map configuration settings
            window.mapSettings = {json.dumps(map_settings)};
        </script>
        <div class="filters">
            <div class="filter-group">
                <label for="network-filter">Network:</label>
                <select id="network-filter">
                    <option value="">All Networks</option>
                </select>
            </div>
            <div class="filter-group">
                <label for="status-filter">Status:</label>
                <select id="status-filter">
                    <option value="">All Statuses</option>
                    <option value="good">Good</option>
                    <option value="warning">Warning</option>
                    <option value="critical">Critical</option>
                    <option value="unavailable">Unavailable</option>
                </select>
            </div>
            <div class="filter-group">
                <input type="text" id="search-input" placeholder="Search stations..." class="search-box">
            </div>
        </div>

        <div id="stats-container" class="stats-container">
            <div class="stats-header">
                <div class="stats-title">Station Statistics</div>
                <button id="close-stats" class="action-button">Close</button>
            </div>
            <div id="status-counter"></div>
            <div id="network-stats" class="network-stats">
                <!-- Network stats will be inserted here -->
            </div>
        </div>

        <div id="loading">
            <div class="loading-spinner"></div>
            Loading station data...
        </div>

        <div id="error-message"></div>

        <!-- Table View -->
        <div id="table-view" class="view-container">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th data-sort="name">Station</th>
                            <th data-sort="status">Status</th>
                            <th data-sort="latency">Latency</th>
                            <th data-sort="channels">Channels</th>
                            <th data-sort="updated">Last Updated</th>
                        </tr>
                    </thead>
                    <tbody id="table-body">
                        <!-- Table rows will be inserted here -->
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Grid View -->
        <div id="grid-view" class="view-container" style="display: none;">
            <div id="grid-container" class="grid-container">
                <!-- Grid will be inserted here -->
            </div>
        </div>

        <!-- Map View -->
        <div id="map-view" class="view-container" style="display: none;">
            <div id="map-container" class="map-container">
                <!-- Map will be rendered here -->
            </div>
        </div>
    """

    # Add legend (generated from STATUS_STYLES, the single palette source)
    html += f"""
        <div class="legend">
{render_legend_html()}
        </div>
    """

    # Per-station admin-configured extras (info/text) and the live-seismogram
    # URL template, exposed to the client so the inline detail panel can show
    # them without a separate per-station HTML page.
    station_extra = {}
    for k in config.station:
        net_sta = f"{config.station[k]['net']}_{config.station[k]['sta']}"
        extra = {}
        if 'info' in config.station[k]:
            extra['info'] = config.station[k]['info']
        if 'text' in config.station[k]:
            extra['text'] = config.station[k]['text']
        if extra:
            station_extra[net_sta] = extra

    live_url_template = config['setup'].get('liveurl', '')

    # Add footer and close tags
    html += f"""
        <div class="footer">
            <div>Last updated <span id="update-time">{gmtime()[:6][0]:04d}/{gmtime()[:6][1]:02d}/{gmtime()[:6][2]:02d} {gmtime()[:6][3]:02d}:{gmtime()[:6][4]:02d}:{gmtime()[:6][5]:02d} UTC</span></div>
            <div><a href="{config['setup']['linkurl']}" target="_top">{config['setup']['linkname']}</a></div>
        </div>
    </div>

    <!-- Station Detail Panel: replaces per-station HTML page navigation with
         an inline slide-over so filters/scroll position/map state aren't
         lost. Direct links still work via index.html?station=NET_STA. -->
    <div id="detail-overlay" class="detail-overlay"></div>
    <aside id="detail-panel" class="detail-panel">
        <div class="detail-panel-header">
            <span id="detail-panel-title">Station Detail</span>
            <button id="detail-panel-close" class="detail-panel-close" aria-label="Close">&times;</button>
        </div>
        <div id="detail-panel-body" class="detail-panel-body"></div>
    </aside>

    <!-- Export JSON data for JavaScript -->
    <script>
        // Initialize stationsData with server-side rendered data
        const initialStationsData = {status.to_json()};
        // Admin-configured per-station extras (info/text) and the live
        // seismogram URL template, used by the detail panel.
        window.stationExtraInfo = {json.dumps(station_extra)};
        window.liveUrlTemplate = {json.dumps(live_url_template)};
    </script>

    <!-- Include the main JavaScript file -->
    <script src="script.js"></script>
</body>
</html>
"""

    try:
        html_path = os.path.join(config['setup']['wwwdir'], 'index.html')
        with open(html_path, 'w') as f:
            f.write(html)
        print(f"Main HTML file generated at {html_path}")
        return html_path
    except Exception as e:
        print(f"Error generating main HTML file: {str(e)}")
        return None


def generate_station_html(net_sta, config):
    """Write a tiny redirect stub for net_sta.html.

    Station detail used to be a full standalone page, regenerated from
    scratch for every station on every refresh cycle, with its own
    hand-duplicated color palette and a legend that only covered 6 of the
    12 statuses. That's now an inline slide-over panel on index.html (see
    showDetail()/renderDetailBody() in generate_js_file, and stationsData's
    per-channel info, which already has everything the old page rendered).

    This stub only exists so old bookmarks/external links to net_sta.html
    keep working -- it immediately redirects to the equivalent deep link.
    """
    target = f"index.html?station={net_sta}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url={target}">
    <link rel="canonical" href="{target}">
    <title>{net_sta} - redirecting…</title>
    <script>location.replace({json.dumps(target)});</script>
</head>
<body>
    <p>This page has moved. <a href="{target}">Click here</a> if you are not redirected automatically.</p>
</body>
</html>
"""

    try:
        html_path = os.path.join(config['setup']['wwwdir'], f'{net_sta}.html')
        with open(html_path, 'w') as f:
            f.write(html)
        return html_path
    except Exception as e:
        print(f"Error generating station redirect stub for {net_sta}: {str(e)}", file=sys.stderr)
        return None


def generate_json_data(status):
    """Generate a JSON file with station data for JavaScript use"""
    try:
        json_data = status.to_json()
        json_path = os.path.join(config['setup']['wwwdir'], 'stations_data.json')
        with open(json_path, 'w') as f:
            f.write(json_data)
        print(f"JSON data file generated at {json_path}")
        return json_path
    except Exception as e:
        print(f"Error generating JSON data file: {str(e)}")
        return None


def generate_all_files(config, status):
    """Generate all the static files needed for the web interface"""

    # Create the directory if it doesn't exist
    try:
        os.makedirs(config['setup']['wwwdir'], exist_ok=True)
    except Exception as e:
        print(f"Error creating directory: {str(e)}")
        return False

    # Generate files
    css_path = generate_css_file(config)
    js_path = generate_js_file(config)
    json_path = generate_json_data(status)
    main_html = generate_main_html(config, status)

    # Generate station pages - Get UNIQUE station identifiers
    unique_stations = set()
    for k in status:
        net_sta = f"{status[k].net}_{status[k].sta}"
        unique_stations.add(net_sta)

    # Now generate each station page exactly once
    station_htmls = []
    for net_sta in unique_stations:
        html_path = generate_station_html(net_sta, config)
        station_htmls.append(html_path is not None)

    # Return success only if all files were generated
    all_stations_success = len(station_htmls) > 0 and all(station_htmls)

    # Log success or failure
    if all_stations_success:
        print(f"Successfully generated {len(station_htmls)} station HTML files")
    else:
        print(f"ERROR: Failed to generate some station HTML files")

    # Return success if all files were generated
    return all([css_path, js_path, json_path, main_html, all_stations_success])


def read_ini():
    """Read configuration files"""
    global config, ini_setup, ini_stations
    print("reading setup config from '%s'" % ini_setup)
    if not os.path.isfile(ini_setup):
        print("[error] setup config '%s' does not exist" % ini_setup, file=sys.stderr)
        usage(exitcode=2)

    config = MyConfig(ini_setup)
    print("reading station config from '%s'" % ini_stations)
    if not os.path.isfile(ini_stations):
        print("[error] station config '%s' does not exist" % ini_stations, file=sys.stderr)
        usage(exitcode=2)
    config.station = MyConfig(ini_stations)


def SIGINT_handler(signum, frame):
    """Handle interruption signals"""
    global status
    print("received signal #%d => will write status file and exit" % signum)
    sys.exit(0)


def main():
    """Main function to run the program"""
    global config, status, verbose, generate_only, ini_setup, ini_stations

    # Parse command line arguments
    try:
        opts, args = getopt(sys.argv[1:], "c:s:t:hvg", ["help", "generate"])
    except GetoptError:
        print("\nUnknown option in "+str(sys.argv[1:])+" - EXIT.", file=sys.stderr)
        usage(exitcode=2)

    for flag, arg in opts:
        if flag == "-c":        ini_setup = arg
        elif flag == "-s":      ini_stations = arg
        elif flag == "-t":      refresh = float(arg)  # XXX not yet used
        elif flag in ("-h", "--help"):     usage(exitcode=0)
        elif flag == "-v":      verbose = 1
        elif flag in ("-g", "--generate"): generate_only = True

    # Set up signal handlers
    signal.signal(signal.SIGHUP, SIGINT_handler)
    signal.signal(signal.SIGINT, SIGINT_handler)
    signal.signal(signal.SIGQUIT, SIGINT_handler)
    signal.signal(signal.SIGTERM, SIGINT_handler)

    # Read configuration
    read_ini()

    # Load station coordinates from the FDSN web service
    try:
        load_station_coordinates(config)
    except Exception as e:
        print(f"Warning: Failed to load station coordinates: {str(e)}")

    # Prepare station information
    s = config.station
    net_sta = ["%s_%s" % (s[k]['net'], s[k]['sta']) for k in s]
    s_arg = ','.join(net_sta)

    # Set server from config or use default
    if 'server' in config['setup']:
        server = config['setup']['server']
    else:
        server = "localhost"

    # Initialize status dictionary
    status = StatusDict()

    print("generating output to '%s'" % config['setup']['wwwdir'])

    if generate_only:
        # Generate template files without fetching data
        print("Generating template files only...")

        # Create dummy data for template rendering
        for net_sta_item in net_sta:
            net, sta = net_sta_item.split('_')

            d = Status()
            d.net = net
            d.sta = sta
            d.loc = ""
            d.cha = "HHZ"
            d.typ = "D"
            d.last_data = datetime.utcnow()
            d.last_feed = datetime.utcnow()

            sec = "%s.%s.%s.%s.%c" % (d.net, d.sta, d.loc, d.cha, d.typ)
            status[sec] = d

        # Generate all files
        if generate_all_files(config, status):
            print("Template files generated successfully.")
        else:
            print("Error generating template files.")

        sys.exit(0)

    # Get initial data
    print("getting initial time windows from SeedLink server '%s'" % server)
    status.fromSlinkTool(server, stations=net_sta)
    if verbose:
        status.write(sys.stderr)

    # Generate initial files
    generate_all_files(config, status)

    refresh_secs = int(config['setup']['refresh'])

    # Network timeout for the slinktool subprocess (-nt). Without this,
    # slinktool has no way to notice a stalled/dead SeedLink connection and
    # 'for rec in input' below can block forever with no self-recovery.
    network_timeout = int(config['setup'].get('network_timeout', max(120, 2 * refresh_secs)))

    # Regeneration used to happen only inside the loop below, gated on a
    # freshly-received record. That means a station (or the whole feed)
    # going completely silent -- the exact outage this tool exists to
    # detect -- never triggered a re-render, so the dashboard just froze
    # on the last-good snapshot instead of showing growing latencies.
    # A background timer decouples regeneration from record arrival.
    regen_lock = threading.Lock()

    def safe_generate_all_files():
        # Skip (rather than queue) if a previous run is still in flight,
        # so slow/overlapping regenerations can't pile up or race on the
        # same output files.
        if not regen_lock.acquire(blocking=False):
            return
        try:
            generate_all_files(config, status)
        except Exception as e:
            print(f"ERROR regenerating files: {e}", file=sys.stderr)
        finally:
            regen_lock.release()

    def periodic_regenerate():
        while True:
            sleep(refresh_secs)
            safe_generate_all_files()

    regen_thread = threading.Thread(target=periodic_regenerate, daemon=True)
    regen_thread.start()

    print("setting up connection to SeedLink server '%s'" % server)

    # Connect to the SeedLink server and start receiving data
    input = seiscomp.slclient.Input(server, [(s[k]['net'], s[k]['sta'], "", "???") for k in s],
                                     timeout=network_timeout)
    for rec in input:
        id = '.'.join([rec.net, rec.sta, rec.loc, rec.cha, rec.rectype])

        if id not in status:
            # A channel that wasn't part of the initial 'slinktool -Q'
            # snapshot (station was down at startup, or started a new
            # channel since). Register it instead of silently dropping it
            # forever via the old bare except-KeyError -- that used to mean
            # a recovered/new station would never appear until slmon2 was
            # restarted.
            if rec.rectype != 'D' or not regexStreams.match(rec.cha):
                continue
            d = Status()
            d.net, d.sta, d.loc, d.cha, d.typ = rec.net, rec.sta, rec.loc, rec.cha, rec.rectype
            d.last_data = d.last_feed = rec.end_time
            status[id] = d
            continue

        status[id].last_data = rec.end_time
        status[id].last_feed = datetime.utcnow()


if __name__ == "__main__":
    main()
