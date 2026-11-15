"""fetch_nhsn_hrd.py on a fixture (no network): 12 real rows of data.cdc.gov ua7e-t2fy (NC, USA, AK and HHS Region 4; the
weeks ending 24 Oct, 31 Oct and 7 Nov 2026), cut verbatim from the response saved on 2026-11-14 22:50 UTC.
Run: python3 -m unittest discover -s tests -q"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'feeds', 'nhsn'))
import fetch_nhsn_hrd as F  # noqa: E402

FIXTURE = os.path.join(HERE, 'fixtures', 'nhsn_hrd_rows.json')


class Decode(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding='utf-8') as f:
            self.rows = json.load(f)
        self.states, self.weeks = F.decode(self.rows)

    def test_regions_dropped(self):
        self.assertEqual(sorted(self.states), ['AK', 'NC', 'USA'], 'HHS regions are dropped; states and USA kept')

    def test_weeks_newest_first(self):
        self.assertEqual(self.weeks, ['2026-11-07', '2026-10-31', '2026-10-24'])
        self.assertEqual([r[0] for r in self.states['NC']], self.weeks)

    def test_values_as_published(self):
        # NC, week ending 2026-11-07, transcribed by hand from the fixture
        self.assertEqual(self.states['NC'][0], ['2026-11-07', 21665, 17356, 80.11, 3144, 2445, 77.77, 117, 109])
        # USA, same week
        self.assertEqual(self.states['USA'][0][4:7], [101038, 72539, 71.79])
        # the percentage is CDC's, not recomputed: 2445 / 3144 = 77.7672…, published 77.77
        self.assertNotEqual(self.states['NC'][0][6], 2445 / 3144 * 100)

    def test_counts_are_integers(self):
        for xs in self.states.values():
            for r in xs:
                for v in (r[1], r[2], r[4], r[5], r[7], r[8]):
                    self.assertTrue(v is None or isinstance(v, int))

    def test_weeks_limit(self):
        states, weeks = F.decode(self.rows, weeks=2)
        self.assertEqual(weeks, ['2026-11-07', '2026-10-31'])
        self.assertEqual(len(states['AK']), 2)

    def test_blank_values(self):
        s, _ = F.decode([{'weekendingdate': '2026-11-07T00:00:00.000', 'jurisdiction': 'GU', 'numicubeds': '', 'pcticubedsocc': None}])
        self.assertEqual(s['GU'][0], ['2026-11-07', None, None, None, None, None, None, None, None])

    def test_query(self):
        u = F.query_url('2026-08-01')
        self.assertTrue(u.startswith(F.BASE + '?'))
        self.assertIn('weekendingdate+%3E%3D+%272026-08-01%27', u)

    def test_refuses_thin_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'hrd.json')
            code = F.main(['--rows', FIXTURE, '--now', '2026-11-14T22:50:00Z', '--out', out])
            self.assertEqual(code, 1, 'three jurisdictions is not a whole file: nothing is written')
            self.assertFalse(os.path.exists(out))

    def test_envelope_shape(self):
        env = F.envelope(self.states, self.weeks, now=F.datetime(2026, 11, 14, 22, 50, tzinfo=F.timezone.utc), upstream='u')
        self.assertEqual(env['newest'], '2026-11-07')
        self.assertEqual(env['fields'][6], 'icuPct')
        self.assertEqual(env['items'], [])
        self.assertEqual(env['licence'], 'Public Domain U.S. Government')


if __name__ == '__main__':
    unittest.main()
