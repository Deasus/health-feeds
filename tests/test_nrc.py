"""The `nrc` feed (feeds/nrc/fetch_nrc.py) on a fixture, no network. Run: python3 -m unittest discover -s tests -q

tests/fixtures/nrc_fixture.xlsx: 14 real reports from the NRC's CY26.xlsx (read 2026-11-13), chosen to exercise each rule,
written back as a small .xlsx by PULSE Web's draft builder. Changed from the source: CALLS keeps only SEQNOS,
DATE_TIME_RECEIVED and CALLTYPE (the responsible-party columns can name a private person); report 1477690's description is
SYNTHETIC (a made-up person and a 555 number, to test that such descriptions are left out); and LOCATION_ADDRESS, the street
lines and the ZIP code are blank (2026-11-25: the feed never reads them, and two were private homes). The rest is verbatim.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS = os.path.join(os.path.dirname(HERE), 'feeds')
for d in ('nrc', '_shared'):
    sys.path.insert(0, os.path.join(FEEDS, d))
import fetch_nrc as F  # noqa: E402

FIXTURE = os.path.join(HERE, 'fixtures', 'nrc_fixture.xlsx')
CITIES = os.path.join(FEEDS, '_shared', 'cities.json')             # the pipeline's GeoNames gazetteer
NOW = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)


class Rules(unittest.TestCase):
    def test_dms(self):
        self.assertEqual(F.dms('27', '44', '44', 'N', 'lat'), 27.74556)            # 27 + 44/60 + 44/3600
        self.assertEqual(F.dms('97', '51', '23', 'W', 'lng'), -97.85639)
        self.assertEqual(F.dms('33', '0', '0', 'S', 'lat'), -33.0)
        self.assertIsNone(F.dms('32', '10', '6', None, 'lat'), 'no quadrant: not guessed')
        self.assertIsNone(F.dms('32', '60', '0', 'N', 'lat'), 'minutes must be under 60')
        self.assertIsNone(F.dms('32', '10', '60', 'N', 'lat'), 'seconds must be under 60')
        self.assertIsNone(F.dms('91', '0', '0', 'N', 'lat'), 'latitude within 90')
        self.assertIsNone(F.dms('181', '0', '0', 'W', 'lng'), 'longitude within 180')
        self.assertIsNone(F.dms('', '', '', '', 'lat'))
        self.assertIsNone(F.dms('32', '1', '1', 'E', 'lat'), 'a longitude quadrant on a latitude is refused')

    def test_times(self):
        self.assertEqual(F.excel_time('46023.009027777778'), '2026-01-01T00:13')
        self.assertEqual(F.text_time('11/8/2026 18:50'), '2026-11-08T18:50')
        self.assertEqual(F.text_time('1/2/2026'), '2026-01-02T00:00')
        self.assertIsNone(F.excel_time(None))

    def test_cas(self):
        self.assertEqual(F.cas('007664-41-7'), '7664-41-7')
        self.assertIsNone(F.cas('000000-00-0'), 'the file\'s placeholder is no CAS number')
        self.assertIsNone(F.cas('N/A'))

    def test_prune(self):
        self.assertEqual(F.prune({'a': None, 'b': {'c': None}, 'lat': None, 'm': [{'x': 1, 'y': None}]}), {'lat': None, 'm': [{'x': 1}]})


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, 'rb') as f:
            cls.book = F.read_workbook(f.read())
        cls.items, cls.counts = F.decode(cls.book, NOW, F.Places(None, CITIES))
        cls.by = {i['id']: i for i in cls.items}

    def test_counts(self):
        c = self.counts
        self.assertEqual((c['reports'], c['in_window'], c['non_release'], c['kept']), (14, 13, 1, 12))
        self.assertEqual(c['precision'], {'reported': 2, 'county': 8, 'city': 1, 'none': 1})
        self.assertEqual(c['descriptions_left_out'], 1)
        self.assertEqual(c['invalid_dms'], 1)

    def test_window_and_exclusions(self):
        self.assertNotIn('1475341', self.by, 'received 5 Oct: outside the 30 days before 13 Nov')
        self.assertNotIn('1476564', self.by, 'a railroad non-release report is not a release')
        self.assertEqual([i['received'] for i in self.items], sorted((i['received'] for i in self.items), reverse=True), 'newest first')

    def test_reported_point(self):
        i = self.by['1478109']
        self.assertEqual((i['lat'], i['lng'], i['precision']), (27.74556, -97.85639, 'reported'))
        self.assertEqual(i['dms'], '27 44 44 N, 97 51 23 W', 'the source fields, verbatim')
        self.assertEqual(i['materials'][0], {'name': 'NATURAL GAS', 'chris': 'ONG', 'amount': 9300, 'unit': 'MCF', 'reached_water': 'NO'})
        self.assertEqual(i['release'], {'secured': 'Y', 'duration': 17, 'duration_unit': 'MINUTE'})

    def test_far_from_its_county(self):
        self.assertGreater(self.by['1478096']['county_check_km'], 100, 'a reported point far from the stated county is kept and the distance recorded')

    def test_county_centroid(self):
        i = self.by['1478092']
        self.assertEqual((i['precision'], i['lat'], i['lng'], i['radius_km']), ('county', 39.4309, -87.391, 16.3), 'Vigo County IN, Census internal point')
        self.assertEqual(i['materials'][0]['amount'], 2)
        self.assertEqual(i['materials'][0]['unit'], 'TON(S)')
        self.assertEqual(i['materials'][0]['cas'], '7664-41-7')
        self.assertEqual(self.by['1477995']['precision'], 'county', 'Washington DC: its one county-equivalent')

    def test_bad_dms_falls_back(self):
        i = self.by['1477359']
        self.assertEqual(i['precision'], 'county', 'no quadrant stated → the county, not a guess')
        self.assertEqual(i['dms'], '32 10 6, 93 0 33')

    def test_city_and_none(self):
        self.assertEqual(self.by['1477684']['precision'], 'city')
        n = self.by['1477831']
        self.assertEqual((n['precision'], n['lat'], n['lng']), ('none', None, None), 'a Connecticut county the 2025 gazetteer no longer lists, and a town not in the city list: unmapped')

    def test_reported_rate_and_duration(self):
        self.assertEqual(self.by['1477733']['release'], {'secured': 'N', 'rate': 4, 'rate_unit': 'POUND(S)', 'rate_per': 'MINUTE'})
        self.assertEqual(self.by['1477690']['release'], {'secured': 'N', 'duration': 20, 'duration_unit': 'HOUR', 'rate': 30.27, 'rate_unit': 'POUND(S)', 'rate_per': 'HOUR'})

    def test_people(self):
        self.assertEqual(self.by['1478067']['evacuations'], {'any': 'Y', 'n': 225, 'who': 'EVERYONE'})
        self.assertEqual(self.by['1478129']['injuries'], {'any': 'Y', 'injured': 1, 'hospitalized': 1})
        self.assertNotIn('description', self.by['1477690'], 'a description naming a person or a phone number is left out')
        for i in self.items:
            for k in i:
                self.assertNotIn('RESPONSIBLE', k.upper(), 'no responsible-party field')

    def test_multiple_materials(self):
        self.assertEqual([m['name'] for m in self.by['1477915']['materials']], ['UNKNOWN OIL', 'ETHYLENE GLYCOL ACETATE', 'OIL, MISC: MOTOR'])

    def test_envelope_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'incidents.json')
            self.assertEqual(F.main(['--xlsx', FIXTURE, '--now', '2026-11-13T21:00:00Z', '--out', out, '--cities', CITIES]), 0)
            with open(out, encoding='utf-8') as f:
                env = json.load(f)
        self.assertEqual(env['count'], 12)
        self.assertEqual(env['kind'], 'REPORTED')
        self.assertIn('not been validated or investigated', env['licence'])
        self.assertIn('not validated or investigated', env['caveat'])
        self.assertEqual(env['window'], {'from': '2026-10-14T21:00', 'to': '2026-11-13T21:00'})
        self.assertEqual(env['newest_received'], env['items'][0]['received'])
        self.assertEqual(len(env['sources'][0]['sha256']), 64)

    def test_no_street_address(self):
        """2026-11-25: LOCATION_ADDRESS is never read and a stated location that begins with a house number is left out."""
        self.assertEqual(self.by['1478129']['location'], 'MELISSA DRIVE & RANGE LINE STREET')
        self.assertEqual(self.by['1478096']['location'], 'PACIFIC OCEAN')
        self.assertNotIn('location', self.by['1477995'], 'a house under renovation: no address')
        self.assertIsNone(F.location_text({'INCIDENT_LOCATION': '6826 5TH STREET NW'}))
        self.assertIsNone(F.location_text({'LOCATION_ADDRESS': '76 THAMES TER'}))
        for i in self.items:
            self.assertFalse(F.HOUSE_NUMBER.match(i.get('location') or ''), i['id'])


class Pipeline(unittest.TestCase):
    """How a conductor run behaves: at most one download per workbook, and a failure writes nothing (the published
    file is kept). The download is replaced; nothing here reaches the network."""

    def setUp(self):
        self.saved = F.download
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, 'incidents.json')
        with open(FIXTURE, 'rb') as f:
            self.blob = f.read()
        self.calls = []

    def tearDown(self):
        F.download = self.saved
        self.tmp.cleanup()

    def run_main(self, now='2026-11-13T21:00:00Z', days=None):
        return F.main(['--now', now, '--out', self.out, '--cities', CITIES] + (['--days', str(days)] if days else []))

    def test_downloads_once_then_not_modified_keeps_the_file(self):
        def dl(url, since=None):
            self.calls.append((url, since))
            return (None, since) if since else (self.blob, 'Mon, 09 Nov 2026 17:28:02 GMT')
        F.download = dl
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.calls, [('https://nrc.uscg.mil/FOIAFiles/CY26.xlsx', None)])
        with open(self.out, encoding='utf-8') as f:
            first = f.read()
        self.assertEqual(json.loads(first)['sources'][0]['last_modified'], 'Mon, 09 Nov 2026 17:28:02 GMT')
        self.assertEqual(self.run_main(), 0)                                   # the next day: 304, nothing rewritten
        self.assertEqual(self.calls[1], ('https://nrc.uscg.mil/FOIAFiles/CY26.xlsx', 'Mon, 09 Nov 2026 17:28:02 GMT'))
        with open(self.out, encoding='utf-8') as f:
            self.assertEqual(f.read(), first)

    def test_failures_keep_the_previous_file(self):
        with open(self.out, 'w', encoding='utf-8') as f:
            f.write('{"previous": true}')

        def broken(url, since=None):
            raise OSError('HTTP Error 503: Service Unavailable')
        F.download = broken
        self.assertEqual(self.run_main(), 1)
        F.download = lambda url, since=None: (b'not a workbook', None)
        self.assertEqual(self.run_main(), 1)
        F.download = lambda url, since=None: (self.blob, None)
        self.assertEqual(self.run_main('2027-06-01T00:00:00Z'), 1)              # nothing received in the window: refuse
        with open(self.out, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'previous': True})

    def test_january_reads_both_years(self):
        """In January the window reaches last year's workbook. If one changed and the other did not, both are read whole."""
        def dl(url, since=None):
            self.calls.append((url[-9:], since))
            if since and url.endswith('CY26.xlsx'):
                return None, since
            return self.blob, 'LM ' + url[-9:]
        F.download = dl
        self.assertEqual(self.run_main('2027-01-10T00:00:00Z', days=90), 0)        # a 90-day window reaches the fixture's November
        self.assertEqual(self.calls, [('CY26.xlsx', None), ('CY27.xlsx', None)])
        self.calls.clear()
        self.assertEqual(self.run_main('2027-01-11T00:00:00Z', days=90), 0)
        self.assertEqual(self.calls, [('CY26.xlsx', 'LM CY26.xlsx'), ('CY27.xlsx', 'LM CY27.xlsx'), ('CY26.xlsx', None)])


if __name__ == '__main__':
    unittest.main()
