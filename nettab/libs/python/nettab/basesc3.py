from __future__ import print_function
import seiscomp.datamodel, seiscomp.core, seiscomp.config
from .helpers import parsers
import datetime
import sys


class sc3(object):
    def _fillSc3(self, obj, att):
        commentNum = 0
        for (k, p) in att.items():
            try:
                if k == 'Comment':
                    # print('DEBUG: Adding comment', p)
                    if p.startswith('Grant'):
                        # 2020: These belong in DOI metadata, not here.
                        continue

                    c = seiscomp.datamodel.Comment()
                    c.setText(p)
                    c.setId(str(commentNum))
                    commentNum += 1
                    obj.add(c)
                    continue

                if k == 'Pid':
                    # print('DEBUG: Adding Pid as comment', p)
                    c = seiscomp.datamodel.Comment()
                    (typ, val) = p.split(':', 1)
                    s = '{"type":"%s", "value":"%s"}' % (typ.upper(), val)
                    c.setText(s)
                    c.setId('FDSNXML:Identifier/' + str(commentNum))
                    commentNum += 1
                    obj.add(c)
                    continue

                w = 'set' + k
                p = self.sc3Valid['attributes'][k]['validator'](p)
                getattr(obj, w)(p)
            except Exception as e:
                print("[Error] %s = %s (%s)" % (k, p, e),
                      file=sys.stderr)

    @staticmethod
    def getBool(val):
        if val == "True" or val == 1:
            return True
        elif val == "False" or val == 0:
            return False
        else:
            raise Exception("Invalid Boolean Value")

    @staticmethod
    def getString(data):
        return data.strip()
    
    @staticmethod
    def getRealArray(data):
        RA = seiscomp.datamodel.RealArray()
        for r in map(float, data):
            RA.content().push_back(r)
        return RA

    @staticmethod
    def getComplexArray(data):
        CA = seiscomp.datamodel.ComplexArray()
        for (r,i) in data:
            CA.content().push_back(complex(float(r),float(i)))
        return CA

    @staticmethod
    def getDate(value):
        if isinstance(value, datetime.datetime):
            return seiscomp.core.Time(*(value.timetuple()[:6]))
        elif isinstance(value, str):
            value = parsers.parseDate(value)
            return seiscomp.core.Time(*(value.timetuple()[:6]))
        return value

    @staticmethod
    def getBlob(value):
        b = seiscomp.datamodel.Blob()
        b.setContent(value)
        return b

    @staticmethod
    def getStationGroupType(val):
        if val == "ARRAY":
            return seiscomp.datamodel.ARRAY
        elif val == "DEPLOYMENT":
            return seiscomp.datamodel.DEPLOYMENT
        else:
            raise Exception("Invalid station group type")

    @staticmethod    
    def _findValidOnes(mode):
        valid = {
        'dataloggerCalibration': {
            'creator': seiscomp.datamodel.DataloggerCalibration,
            'attributes': {
                'SerialNumber':           { 'validator': sc3.getString },
                'Channel':                { 'validator': int },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Gain':                   { 'validator': float },
                'GainFrequency':          { 'validator': float },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        },
        'sensorCalibration': {
            'creator': seiscomp.datamodel.SensorCalibration,
            'attributes': {
                'SerialNumber':           { 'validator': sc3.getString },
                'Channel':                { 'validator': int },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Gain':                   { 'validator': float },
                'GainFrequency':          { 'validator': float },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        },
        'channel': {
            'creator': seiscomp.datamodel.Stream_Create,
            'attributes': {
                'Code':                   { 'validator': sc3.getString },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Datalogger':             { 'validator': sc3.getString },
                'DataloggerSerialNumber': { 'validator': sc3.getString },
                'DataloggerChannel':      { 'validator': int },
                'Sensor':                 { 'validator': sc3.getString },
                'SensorSerialNumber':     { 'validator': sc3.getString },
                'SensorChannel':          { 'validator': int },
                'ClockSerialNumber':      { 'validator': sc3.getString },
                'SampleRateNumerator':    { 'validator': int },
                'SampleRateDenominator':  { 'validator': int },
                'Depth':                  { 'validator': float },
                'Azimuth':                { 'validator': float },
                'Dip':                    { 'validator': float },
                'Gain':                   { 'validator': float },
                'GainFrequency':          { 'validator': float },
                'GainUnit':               { 'validator': sc3.getString },
                'Format':                 { 'validator': sc3.getString },
                'Flags':                  { 'validator': sc3.getString },
                'Restricted':             { 'validator': sc3.getBool },
                'Shared':                 { 'validator': sc3.getBool }
            } 
        },
        'location': {
            'creator': seiscomp.datamodel.SensorLocation_Create,
            'attributes': {
                'Code':                   { 'validator': sc3.getString },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                "Latitude":               { 'validator': float },
                "Longitude":              { 'validator': float },
                "Elevation":              { 'validator': float }
                }
        },
        'station': {
            'creator': seiscomp.datamodel.Station_Create,
            'attributes': {
                'Code':                   { 'validator': sc3.getString },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Description':            { 'validator': sc3.getString },
                'Latitude':               { 'validator': float },
                'Longitude':              { 'validator': float },
                'Elevation':              { 'validator': float },
                'Place':                  { 'validator': sc3.getString },
                'Country':                { 'validator': sc3.getString },
                'Affiliation':            { 'validator': sc3.getString },
                'Type':                   { 'validator': sc3.getString },
                'ArchiveNetworkCode':     { 'validator': sc3.getString },
                'Archive':                { 'validator': sc3.getString },
                'Restricted':             { 'validator': sc3.getBool },
                'Shared':                 { 'validator': sc3.getBool },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        },
        'network': {
            'creator': seiscomp.datamodel.Network_Create,
            'attributes': {
                'Code':                   { 'validator': sc3.getString },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Description':            { 'validator': sc3.getString },
                'Institutions':           { 'validator': sc3.getString },
                'Region':                 { 'validator': sc3.getString },
                'Type':                   { 'validator': sc3.getString },
                'NetClass':               { 'validator': sc3.getString },
                'Archive':                { 'validator': sc3.getString },
                'Comment':                { 'validator': sc3.getString },
                'Pid':                    { 'validator': sc3.getBlob },
                'Restricted':             { 'validator': sc3.getBool },
                'Shared':                 { 'validator': sc3.getBool },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        },
        'stationGroup': {
            'creator': seiscomp.datamodel.StationGroup_Create,
            'attributes': {
                'Code':                   { 'validator': sc3.getString },
                'Start':                  { 'validator': sc3.getDate },
                'End':                    { 'validator': sc3.getDate },
                'Description':            { 'validator': sc3.getString },
                'Type':                   { 'validator': sc3.getStationGroupType },
                'Latitude':               { 'validator': float },
                'Longitude':              { 'validator': float },
                'Elevation':              { 'validator': float },
            }
        },
        'stationReference': {
            'creator': seiscomp.datamodel.StationReference,
            'attributes': {
                'StationID':              { 'validator': sc3.getString },
            }
        },
        'datalogger': {
            'creator': seiscomp.datamodel.Datalogger_Create,
            'attributes': {
                'Name':                   { 'validator': sc3.getString },
                'Description':            { 'validator': sc3.getString },
                'DigitizerModel':         { 'validator': sc3.getString },
                'DigitizerManufacturer':  { 'validator': sc3.getString },
                'RecorderModel':          { 'validator': sc3.getString },
                'RecorderManufacturer':   { 'validator': sc3.getString },
                'ClockModel':             { 'validator': sc3.getString },
                'ClockManufacturer':      { 'validator': sc3.getString },
                'ClockType':              { 'validator': sc3.getString },
                'Gain':                   { 'validator': float },
                'MaxClockDrift':          { 'validator': float },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        }, 
        'decimation': {
            'creator': seiscomp.datamodel.Decimation,
            'attributes': {
                'SampleRateNumerator':    { 'validator': int },
                'SampleRateDenominator':  { 'validator': int },
                'AnalogueFilterChain':    { 'validator': sc3.getBlob },
                'DigitalFilterChain':     { 'validator': sc3.getBlob }
            }
        },
        'fir': {
            'creator': seiscomp.datamodel.ResponseFIR_Create,
            'attributes': {
                "Name":                   { 'validator': sc3.getString },
                "Gain":                   { 'validator': float },
                "DecimationFactor":       { 'validator': int },
                "Delay":                  { 'validator': float },
                "Correction":             { 'validator': float },
                "NumberOfCoefficients":   { 'validator': int },
                "Symmetry":               { 'validator': sc3.getString },
                "Coefficients":           { 'validator': sc3.getRealArray },
                "Remarks":                { 'validator': sc3.getBlob }
            }
        },
        'paz': {
            'creator': seiscomp.datamodel.ResponsePAZ_Create,
            'attributes': {
                'Name':                   { 'validator': sc3.getString },
                'Description':            { 'validator': sc3.getString },
                'Type':                   { 'validator': sc3.getString },
                'Gain':                   { 'validator': float },
                'GainFrequency':          { 'validator': float },
                'NormalizationFactor':    { 'validator': float },
                'NormalizationFrequency': { 'validator': float },
                'NumberOfZeros':          { 'validator': int },
                'NumberOfPoles':          { 'validator': int },
                'Zeros':                  { 'validator': sc3.getComplexArray },
                'Poles':                  { 'validator': sc3.getComplexArray },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        },
        'sensor': {
            'creator': seiscomp.datamodel.Sensor_Create,
            'attributes': {
                'Name':                   { 'validator': sc3.getString },
                'Description':            { 'validator': sc3.getString },
                'Model':                  { 'validator': sc3.getString },
                'Manufacturer':           { 'validator': sc3.getString },
                'Type':                   { 'validator': sc3.getString },
                'Unit':                   { 'validator': sc3.getString },
                'LowFrequency':           { 'validator': float },
                'HighFrequency':          { 'validator': float },
                'Response':               { 'validator': sc3.getString },
                'Remark':                 { 'validator': sc3.getBlob }
            }
        }
        }

        return(valid.get(mode))

    def __init__(self, mode, child=[]):
        self.sc3Mode = mode
        self.sc3obj = None
        self.sc3Valid = sc3._findValidOnes(mode)
        self._sc3Childs = child

    def _create(self):
        if not self.sc3Valid:
            raise Exception("Class without a type defined.")
        return self.sc3Valid['creator']()
            
    def sc3Att(self):
        """
        This is the heart. You should return an dictionary of attributes to be
        setted on the sc3 object. This dictionary will be used by the _fillSc3
        method.
        """
        raise Exception("Not Implemented !")

    def sc3ValidKey(self, key):
        if not self.sc3Valid:
            raise Exception("Class without a type defined.")
        return (key in self.sc3Valid['attributes'])

    def sc3Resolv(self, inventory):
        """
        In this method you should be able to resolv all the references in your
        self object.
        """
        pass
        
    def sc3Derived(self, inventory):
        """
        This method should generate and collect all the derived objects
        (child on the inventory sense) that should be attributed to the self
        object. By default on this virtual method is returns an empty array.
        """
        objs = []
        for obj in self._sc3Childs:
            objs.append(obj.sc3Obj(inventory))
        return objs

    def sc3ID(self, inventory):
        obj = self.sc3Obj(inventory)
        return obj.publicID()

    def sc3Obj(self, inventory):
        if not self.sc3obj:
            # Get a new object
            obj = self._create()

            # try to resolve REFERENCES to PUBLIC ID
            self.sc3Resolv(inventory)

            # Add the derived objects in
            for dobj in self.sc3Derived(inventory):
                obj.add(dobj)

            # Fill the Attributes in
            self._fillSc3(obj, self.sc3Att())
            # # Only want to see Networks:
            # if (('Code' in self.sc3Att().keys())
            #     and ('ArchiveNetworkCode' not in self.sc3Att().keys())
            #     and ('Azimuth' not in self.sc3Att().keys())
            #     ):
            #     print('DEBUG basesc3.py: sc3Obj:', self, self.sc3Att())

            # Set as created
            self.sc3obj = obj

        # return the obj
        return self.sc3obj
