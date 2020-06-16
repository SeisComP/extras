#!/usr/bin/env python

###############################################################################
# Copyright (C) 2020 Helmholtz-Zentrum Potsdam - Deutsches
#    GeoForschungsZentrum GFZ
#
# License: GPL Affero General Public License (GNU AGPL) version 3.0
# Author:  Peter L. Evans
# E-mail:  <pevans@gfz-potsdam.de>
#
###############################################################################

from __future__ import print_function

from nettab.tab import Tab
import json
import os
import sys
import tempfile
import unittest

# Just to dump XML output??:
try:
    import seiscomp.io as IO
except ImportError:
    print('Failed to import seiscomp.io module, trying seiscomp3.IO instead')
    from seiscomp3 import IO


# Just to examine the output XML:
import xml.etree.ElementTree as ET


def xmlparse(filename):
    parser = ET.XMLParser()
    try:
        parser.feed(open(filename).read())
    except Exception:
        raise
    elem = parser.close()
    ns = '{http://geofon.gfz-potsdam.de/ns/seiscomp3-schema/0.11}'
    return (elem, ns)


class TestTab(unittest.TestCase):
    simpleTab = '''
Nw: QQ 2020-04-01
Na: Description="Atlantis Seismic Network"
Sl: AA01  "Pillars of Hercules/Atlantis"    Q330/N%xxxx    STS-2/N%yyyy    100/20    ZNE 30.0  -15.0  -900  2.0  2020-04-02
'''

    tabWithPid = '''
Nw: QQ 2020-04-01
Na: Description="Atlantis Seismic Network"
Na: Pid="doi:10.1234/xyz"
Sl: AA01  "Pillars of Hercules/Atlantis"    Q330/N%xxxx    STS-2/N%yyyy    100/20    ZNE 30.0  -15.0  -900  2.0  2020-04-02
'''

    instFile = 'small-inst.db'

    templateTab = '''
Nw: {nwline}
Na: {naline}
Sl: {slline}
'''

    def _writeTempTab(self, tabText):
        '''Put a nettab formatted string into a temporary file,
        returning the file name.
        '''
        with tempfile.NamedTemporaryFile(delete=False) as tab:
            print(tabText, file=tab)
        tab.close()
        return tab.name

    def _writeInvXML(self, inv, filename='something.xml'):
        '''Copied from tab2inv.py'''
        ar = IO.XMLArchive()
        print("Generating file: %s" % filename,
              file=sys.stderr)
        ar.create(filename)
        ar.setFormattedOutput(True)
        ar.setCompression(False)
        ar.writeObject(inv)
        ar.close()

    def _writeNewInvXML(self, sc3inv, filename):
        try:
            os.unlink(filename)
        except OSError:  # Python3: Catch FileNotFoundError instead.
            pass
        self._writeInvXML(sc3inv, filename)

    def test_1(self):
        '''Create object'''
        t = Tab()
        print('Expect: "Warning, not filter folder supplied."',
              file=sys.stderr)

    def test_2_filter(self):
        '''Provide a (trivial, non-useful) filter folder'''
        t = Tab(None, None, '.', None, None)

    def test_2_defaults_warning(self):
        '''Provide and load a defaults file'''
        defaults = tempfile.NamedTemporaryFile(delete=False)
        print('''
Nw: QQ 2001/001
        ''', file=defaults)
        defaultsFile = defaults.name
        defaults.close()
        t = Tab(None, defaultsFile, '.', None, None)
        os.unlink(defaultsFile)
        print("Expect: 'Warning: Defaults file can only contain attributes'",
              file=sys.stderr)

    def test_2_defaults_attributes(self):
        '''Provide and load a defaults file'''
        defaults = tempfile.NamedTemporaryFile(delete=False)
        print('''
Na: Foo=bar
Sa: StationFoo=bla * *
Ia: InstrumentFoo=blu *
        ''', file=defaults)
        defaultsFile = defaults.name
        defaults.close()
        t = Tab(None, defaultsFile, '.', None, None)
        os.unlink(defaultsFile)

    def test_3_digest(self):
        tabFile = self._writeTempTab(self.simpleTab)

        t = Tab(None, None, '.', None, None)
        t.digest(tabFile)
        os.unlink(tabFile)

    def SKIPtest_3_digest_check(self):
        tabFile = self._writeTempTab(self.simpleTab)

        t = Tab(None, None, 'filters', None, None)
        t.digest(tabFile)
        t.digest(self.instFile)
        t.check()
        os.unlink(tabFile)

    def test_4_digest_twice(self):
        '''Exception is raised by digesting twice.'''
        tabFile = self._writeTempTab(self.simpleTab)

        t = Tab(None, None, '.', None, None)
        t.digest(tabFile)
        with self.assertRaises(Exception):
            t.digest(tabFile)
        # print('Expect: "Warning: File {name}  is already digested."')

        os.unlink(tabFile)

    def test_5_na_after_sa(self):
        '''Not allowed to provide Na lines after a Sl line'''
        s = '\n'.join([self.simpleTab, 'Na: Pid=10.123/xyz'])
        tabFile = self._writeTempTab(s)

        with self.assertRaises(Exception):
            t.digest(tabFile)
        # print('Expect "No Na lines after a Sl line.',
        #       'Network has already been defined."')
        os.unlink(tabFile)

    def test_6_network_pid(self):
        '''Key 'Pid' is an allowed network attribute'''
        tabString = '''
Nw: QQ 2001/001
Na: Region=Atlantis
Na: Pid=10.123/xyz
'''
        tabFile = self._writeTempTab(tabString)

        t = Tab(None, None, '.', None, None)
        t.digest(tabFile)
        os.unlink(tabFile)

    def test_6_network_pid_check(self):
        '''No problem to define extra unhandled attributes'''
        tabString = '''
Nw: QQ 2001/001
Na: Region=Atlantis
Na: Pid=10.123/xyz
Na: Foo=bar
'''
        tabFile = self._writeTempTab(tabString)

        t = Tab(None, None, '.', None, None)
        t.digest(tabFile)
        t.check()
        os.unlink(tabFile)

    def test_7_sc3Obj(self):
        '''Call sc3Obj with a trivial t'''
        t = Tab(None, None, '.', None, None)
        sc3inv = t.sc3Obj()

    def test_8_network_sc3Obj(self):
        '''Call sc3Obj with an actual network, write XML'''
        tabFile = self._writeTempTab(self.simpleTab)

        t = Tab(None, None, 'filters', None, None)
        t.digest(tabFile)
        t.digest(self.instFile)
        sc3inv = t.sc3Obj()
        # Returns ok, but reports inst.db errors and warnings to stdout.
        self.assertTrue(sc3inv)
        if sc3inv is None:
            assert('scinv is None')
        sc3inv
        outFile = '/tmp/testTabInv.xml'

        try:
            os.unlink(outFile)
        except OSError:  # Python3: Catch FileNotFoundError instead.
            pass

        self._writeInvXML(sc3inv, filename=outFile)
        self.assertTrue(os.path.exists(outFile))
        # Further checks: that the file contains a network, etc.

    def test_9_network_pid_sc3Obj(self):
        '''Load a network with PID, write XML, confirm PID is there.
        Older nettabs reported 'ignoring attribute Pid'.
        '''
        tabFile = self._writeTempTab(self.tabWithPid)

        t = Tab(None, None, 'filters', None, None)
        t.digest(tabFile)
        t.digest(self.instFile)
        sc3inv = t.sc3Obj()
        self.assertTrue(sc3inv)

        outFile = '/tmp/testTabInvPid.xml'
        self._writeNewInvXML(sc3inv, outFile)
        self.assertTrue(os.path.exists(outFile))

        # Check that the file contains exactly one network comment
        # which is a JSON string with PID.
        # e.g. '{"type": "DOI", "value": "10.1234/xsdfa"}'
        (elem, ns) = xmlparse(outFile)
        for e in elem:
            for f in e:
                if f.tag == ns + 'network':
                    g = f.findall(ns + 'comment')
                    self.assertTrue(len(g) == 1)
                    t = g[0].findall(ns + 'text')
                    text = t[0].text
                    j = json.loads(t[0].text)
                    self.assertEqual(j['type'], 'DOI')
                    self.assertEqual(j['value'], '10.1234/xyz')
                    ### self.assertEqual(t[0].text, 'doi:10.1234/xyz')

    def test_10_network_comment(self):
        tabString = '''
Nw: NN 2020/092
Na: Region=Atlantis
Na: Comment="This is commentary"
Na: Remark="Remarkable!"
Sl: AA01  "Zeus"  Q330/N%xxxx  STS-2/N%yyyy  20  Z  30 -15  -2  2.0 2020/093
'''
        tabFile = self._writeTempTab(tabString)
        t = Tab(None, None, 'filters', None, None)
        t.digest(tabFile)
        t.digest(self.instFile)
        t.check()
        os.unlink(tabFile)

        sc3inv = t.sc3Obj()
        self.assertTrue(sc3inv)
        outFile = '/tmp/testTabInvComment.xml'
        self._writeNewInvXML(sc3inv, '/tmp/testTabInvComment.xml')
        self.assertTrue(os.path.exists(outFile))

        # Further checks: that the file contains a network with PID. TODO
        (elem, ns) = xmlparse(outFile)
        for e in elem:
            for f in e:
                if f.tag == ns + 'network':
                    g = f.findall(ns + 'comment')
                    self.assertTrue(len(g) == 1)
                    # DEBUG print('DEBUG Network comment found:',
                    #       g[0].findall(ns + 'text')[0].text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
