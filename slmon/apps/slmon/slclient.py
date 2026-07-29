import  os, sys, tempfile
import  datetime, time, re
from    seiscomp import mseedlite as mseed

def _timeparse(t, format):
    """Parse a time string that might contain fractions of a second.

    Fractional seconds are supported using a fragile, miserable hack.
    Given a time string like '02:03:04.234234' and a format string of
    '%H:%M:%S', time.strptime() will raise a ValueError with this
    message: 'unconverted data remains: .234234'.  If %S is in the
    format string and the ValueError matches as above, a datetime
    object will be created from the part that matches and the
    microseconds in the time string.
    """
    try:
        return datetime.datetime(*time.strptime(t, format)[0:6]).time()
    except ValueError as msg:
        if "%S" in format:
            msg = str(msg)
            mat = re.match(r"unconverted data remains:"
                           " \.([0-9]{1,6})$", msg)
            if mat is not None:
                # fractional seconds are present - this is the style
                # used by datetime's isoformat() method
                frac = "." + mat.group(1)
                t = t[:-len(frac)]
                t = datetime.datetime(*time.strptime(t, format)[0:6])
                microsecond = int(float(frac)*1e6)
                return t.replace(microsecond=microsecond)
            else:
                mat = re.match(r"unconverted data remains:"
                               " \,([0-9]{3,3})$", msg)
                if mat is not None:
                    # fractional seconds are present - this is the style
                    # used by the logging module
                    frac = "." + mat.group(1)
                    t = t[:-len(frac)]
                    t = datetime.datetime(*time.strptime(t, format)[0:6])
                    microsecond = int(float(frac)*1e6)
                    return t.replace(microsecond=microsecond)

        raise

def timeparse(t):
    return _timeparse(t, "%Y/%m/%d %H:%M:%S")


def _die_with_parent():
    """preexec_fn: ask the kernel to SIGKILL this child the instant its
    parent process ends, for any reason (normal exit, crash, kill -9,
    OOM), not just a graceful shutdown.

    Without this, __del__() below is the only thing that stops the
    'slinktool' child -- and __del__() only runs if the parent gets to
    finish garbage collection. If the parent is killed abruptly, the
    child is reparented to init and keeps its SeedLink connection open
    forever. Every such leaked connection counts against the source IP
    on servers that cap simultaneous connections per client, so enough
    of them piling up over repeated crashes/restarts eventually makes
    the server refuse or stall new connections from this host.
    """
    try:
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, 9)
    except Exception:
        pass  # best effort; e.g. not available on non-Linux platforms


class Input(mseed.Input):

    def __init__(self, server, streams,
                 stime=None, etime=None, timeout=None, verbose=0):

# XXX Add the possibility for supplying stime and etime as
#     individual times for each stream. 

        """
        'streams' must be a list containing tuples of (net,sta,loc,cha)
        """

        import subprocess

        streams = [ "%-3s %5s %s%3s.D" % s for s in streams ]
        streams.sort()

        self.tmp = tempfile.NamedTemporaryFile(mode="w", prefix="slinktool.")
        self.tmp.write("\n".join(streams)+"\n")
        self.tmp.flush()
        if verbose:
            sys.stderr.write("\n".join(streams)+"\n")

        slinktool = os.getenv("SLINKTOOL")
        if not slinktool:
            slinktool = "slinktool"
        args = [slinktool, "-l", self.tmp.name, "-o", "-"]
        if stime:
            args.append("-tw")
            tw = "%d,%d,%d,%d,%d,%d:" % (stime.year,stime.month,stime.day,stime.hour,stime.minute,stime.second)
            if etime:
                rw += "%d,%d,%d,%d,%d,%d" % (etime.year,etime.month,etime.day,etime.hour,etime.minute,etime.second)
            args.append(tw)
        if verbose: args.append("-v")
        
        if timeout:
            try:    assert int(timeout) > 0
            except: raise TypeError("illegal timeout parameter")
            args += ["-nt", "%d" % int(timeout)]
        
        args.append(server)
        # start 'slinktool' as sub-process. preexec_fn=_die_with_parent
        # guarantees the OS kills this child if we die unexpectedly,
        # even though __del__() below is unreliable for that case.
        self.popen = subprocess.Popen(args, stdout=subprocess.PIPE,
                                       shell=False, preexec_fn=_die_with_parent)
        infile = self.popen.stdout

        mseed.Input.__init__(self, infile)

    def __del__(self):
        """
        Shut down SeedLink connections and close input.
        """
        sys.stderr.write("shutting down slinktool\n")
        sys.stderr.flush()

        slinktool_pid = self.popen.pid
        # slinktool installs its SIGTERM handler with SA_RESTART, so if it
        # is currently blocked inside connect() (e.g. a slow/unreachable
        # server) the kernel transparently restarts that call and the
        # signal is effectively swallowed until the call unblocks on its
        # own, which can take minutes. SIGKILL avoids that, at the cost
        # of skipping a clean SeedLink-level disconnect.
        self.popen.kill()
        self.popen.communicate()
#       mseed.Input.__del__(self) # closes the input file


def server_version(host, port=18000):

    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((host, port))
    except:
        return None
    s.send("HELLO\n")
    data = s.recv(1024)
    s.close() 
    if data[:8] != "SeedLink":
        return None

    return data[10:13]


def server_running(host, port=18000):

    if server_version(host, port):
        return True

    return False
