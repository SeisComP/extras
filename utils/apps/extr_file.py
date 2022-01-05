#!/usr/bin/env seiscomp-python

from __future__ import print_function
import sys
from seiscomp import mseedlite as mseed

open_files = {}

if len(sys.argv) != 2:
    print("Usage: extr_file FILE")
    sys.exit(1)

for rec in mseed.Input(open(sys.argv[1], "rb")):
    oname = "%s.%s.%s.%s" % (rec.sta, rec.net, rec.loc, rec.cha)
    
    if oname not in open_files:
        postfix = ".D.%04d.%03d.%02d%02d" % (rec.begin_time.year,
            rec.begin_time.timetuple()[7], rec.begin_time.hour,
            rec.begin_time.minute)

        open_files[oname] = open(oname + postfix, "ab")

    ofile = open_files[oname]
    ofile.write(rec.header + rec.data)

for oname in open_files:
    open_files[oname].close()

