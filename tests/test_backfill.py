"""Backfill tests on captured fixtures (no network: every upstream call is a fake, and the real HTTP helpers are
replaced by ones that fail the test). Run: python3 -B -m unittest discover -s tests -q

Fixtures: tests/fixtures/backfill_* were captured on 2026-11-14 (WAHIS list, PAHO page 3, HDX search with a date filter,
HDX activity history; publisher user fields removed). The live decoders' fixtures (paho_alerts.html, hdx_search.json,
wahis_report.json) are reused. Payloads built in code below are SYNTHETIC and say so.
"""
import copy
import gzip
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Inside health-feeds the live feeds sit next to feeds/backfill; in the PULSE Web drafts folder they are read (never
# written) from the health-feeds checkout.
HF_ROOT = ROOT if os.path.isdir(os.path.join(ROOT, 'feeds', '_shared')) else \
    os.environ.get('HEALTH_FEEDS_ROOT', os.path.expanduser('~/PULSE/pipelines/health-feeds'))
for d in ['_shared', 'gdelt', 'wahis', 'paho', 'hdx', 'reliefweb']:
    sys.path.insert(0, os.path.join(HF_ROOT, 'feeds', d))
sys.path.insert(0, os.path.join(ROOT, 'feeds', 'backfill'))
os.environ['HF_OFFLINE'] = '1'
FIXTURE_DIRS = [os.path.join(HERE, 'fixtures'), os.path.join(HF_ROOT, 'tests', 'fixtures')]

hf = importlib.import_module('hf')
bf_common = importlib.import_module('bf_common')
bf_paho = importlib.import_module('bf_paho')
bf_hdx = importlib.import_module('bf_hdx')
bf_wahis = importlib.import_module('bf_wahis')
bf_reliefweb = importlib.import_module('bf_reliefweb')
bf_gdelt = importlib.import_module('bf_gdelt')
backfill = importlib.import_module('backfill')
pathogens = importlib.import_module('pathogens')
UTC = timezone.utc


def fixture(name):
    for d in FIXTURE_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(name)


def load_json(name):
    with open(fixture(name), encoding='utf-8') as f:
        return json.load(f)


def read_text(name):
    with open(fixture(name), encoding='utf-8') as f:
        return f.read()


def no_network(*a, **k):
    raise AssertionError('a test reached the network')


class Sandbox(unittest.TestCase):
    """Each test writes into its own temporary data/ and can never reach the network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = [(bf_common, 'SOURCES_DIR', bf_common.SOURCES_DIR), (backfill, 'DATA', backfill.DATA)]
        bf_common.SOURCES_DIR = os.path.join(self.tmp.name, 'sources')
        backfill.DATA = self.tmp.name
        for mod in (hf, bf_paho, bf_hdx, bf_wahis, bf_gdelt):
            for name in ('get', 'get_json', 'get_text', 'post_json'):
                if hasattr(mod, name):
                    self.saved.append((mod, name, getattr(mod, name)))
                    setattr(mod, name, no_network)

    def tearDown(self):
        for mod, name, v in self.saved:
            setattr(mod, name, v)
        self.tmp.cleanup()


class CommonTests(Sandbox):
    def test_intervals_items_budget(self):
        iv = bf_common.merge_intervals([['2025-01-05T00:00:00Z', '2025-01-10T00:00:00Z'], ['2025-01-01T00:00:00Z', '2025-01-05T00:00:00Z'],
                                        ['2025-02-01T00:00:00Z', '2025-02-02T00:00:00Z']])
        self.assertEqual(iv, [['2025-01-01T00:00:00Z', '2025-01-10T00:00:00Z'], ['2025-02-01T00:00:00Z', '2025-02-02T00:00:00Z']])
        a = bf_common.finalise({'source': 'X', 'updated': 'fetched-now', 'tags': ['q1']}, source_date='2025-01-02T00:00:00Z', field='f', key='k')
        b = bf_common.finalise({'source': 'X', 'updated': None, 'tags': ['q2']}, source_date='2025-01-01T00:00:00Z', field='f', key='k')
        self.assertEqual(a['id'], b['id'])
        store = {}
        bf_common.keep_earliest(store, a)
        bf_common.keep_earliest(store, b)
        self.assertEqual((store[a['id']]['updated'], store[a['id']]['tags']), ('2025-01-01T00:00:00Z', ['q1', 'q2']))
        t = [0.0]
        bud = bf_common.Budget(seconds=10, max_requests=2, clock=lambda: t[0])
        self.assertFalse(bud.spent())
        bud.count(2)
        self.assertTrue(bud.spent())
        bud2 = bf_common.Budget(seconds=10, clock=lambda: t[0])
        t[0] = 11
        self.assertTrue(bud2.spent())


class PAHOBackfillTests(Sandbox):
    SINCE = datetime(2024, 8, 1, tzinfo=UTC)
    UNTIL = datetime(2026, 11, 14, tzinfo=UTC)

    def pages(self, fail_on=None):
        calls = []

        def get(url, timeout=None):
            calls.append(url)
            if fail_on is not None and url == bf_paho.page_url(fail_on):
                raise hf.HFError('HTTP 503')
            if url == bf_paho.page_url(0):
                return read_text('paho_alerts.html')                 # the live fixture (2026-02-18 → 2026-10-20)
            if url == bf_paho.page_url(1):
                return read_text('backfill_paho_page3.html')         # captured page 3 (2024-07-17 → 2024-11-18), served as page 1
            return ''
        return get, calls

    def test_walks_until_a_page_is_older_than_since(self):
        get, calls = self.pages()
        st, items = bf_paho.run(self.SINCE, self.UNTIL, get=get, sleep=lambda s: None)
        self.assertEqual(calls, [bf_paho.page_url(0), bf_paho.page_url(1)])
        self.assertEqual((st['status'], st['intervals']), ('complete', [['2024-08-01T00:00:00Z', '2026-11-14T00:00:00Z']]))
        evs = list(items.values())
        self.assertTrue(evs and all(e['source'] == 'PAHO' and e['bf']['date_field'] == 'list date' for e in evs))
        self.assertTrue(all('2024-08-01' <= e['updated'] <= '2026-11-14' for e in evs))           # nothing before `since`
        self.assertTrue(any(e['updated'].startswith('2024-11-18') for e in evs))                   # from the older page
        self.assertFalse(any(e['updated'] < '2024-08-01' for e in evs))
        self.assertTrue(all(e['body'] is None for e in evs))                                       # no PAHO text reproduced
        regional = [e for e in evs if e['country_code'] is None]
        self.assertTrue(regional and all('regional' in e['tags'] for e in regional))              # "in the Americas Region": no country
        self.assertTrue(os.path.exists(os.path.join(bf_common.SOURCES_DIR, 'paho', 'items.json.gz')))

    def test_a_failed_page_keeps_what_was_covered(self):
        get, _ = self.pages(fail_on=1)
        st, items = bf_paho.run(self.SINCE, self.UNTIL, get=get, sleep=lambda s: None)
        self.assertEqual(st['status'], 'partial')
        self.assertEqual(st['intervals'][0][1], '2026-11-14T00:00:00Z')
        self.assertTrue(st['intervals'][0][0].startswith('2026-02-18'))      # the oldest item on the last good page
        self.assertTrue(st['resumable'] and st['failures'] == 1)


class HDXBackfillTests(Sandbox):
    SINCE = datetime(2026, 11, 1, tzinfo=UTC)
    UNTIL = datetime(2026, 11, 14, tzinfo=UTC)

    def yemen(self):
        return next(p for p in load_json('hdx_search.json')['result']['results'] if p['name'] == 'yemen-cholera-outbreak-daily-epidemiology-update')

    def fake(self, history_fails=False):
        calls, yemen = [], self.yemen()

        def get(url, timeout=None):
            calls.append(url)
            if url.startswith(bf_hdx.SEARCH):
                return {'result': {'count': 1, 'results': [yemen]}} if 'title:cholera&' in url else {'result': {'count': 0, 'results': []}}
            if url.startswith(bf_hdx.ACTIVITY):
                if history_fails:
                    raise hf.HFError('HTTP 403')
                return load_json('backfill_hdx_activity.json')        # 5 activities, 2026-11-09 → 2026-11-13
            raise AssertionError(url)
        return get, calls

    def test_every_change_is_a_dated_item(self):
        get, calls = self.fake()
        st, items = bf_hdx.run(self.SINCE, self.UNTIL, get=get, sleep=lambda s: None)
        searches = [c for c in calls if c.startswith(bf_hdx.SEARCH)]
        self.assertEqual(len(searches), len(importlib.import_module('fetch_hdx').DISEASES))
        self.assertIn('fq=metadata_modified:%5B2026-11-01T00:00:00Z%20TO%20*%5D', searches[0])
        self.assertEqual((st['status'], st['datasets'], st['histories_complete']), ('complete', 1, 1))
        self.assertEqual(st['intervals'], [['2026-11-01T00:00:00Z', '2026-11-14T00:00:00Z']])
        evs = sorted(items.values(), key=lambda e: e['updated'])
        self.assertEqual([e['updated'][:10] for e in evs], ['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13'])
        self.assertTrue(all((e['source'], e['source_tier'], e['pathogen'], e['country_code']) == ('HDX', 'aggregator', 'cholera', 'YE') for e in evs))
        self.assertTrue(all(e['bf']['via'] == 'activity' and e['title'] == 'Yemen: Cholera Outbreak Epidemiology Update Data' for e in evs))
        self.assertEqual(len({e['id'] for e in evs}), 5)

    def test_unreadable_history_falls_back_and_claims_no_coverage(self):
        get, _ = self.fake(history_fails=True)
        st, items = bf_hdx.run(datetime(2017, 1, 1, tzinfo=UTC), self.UNTIL, get=get, sleep=lambda s: None)
        self.assertEqual((st['status'], st['intervals'], st['histories_incomplete']), ('partial', [], ['yemen-cholera-outbreak-daily-epidemiology-update']))
        days = sorted(e['updated'][:10] for e in items.values())
        self.assertEqual(days, ['2017-06-22', '2026-10-22'])                 # creation and the latest change, both true dates

    def test_dated_search_fixture_decodes(self):
        fetch_hdx = importlib.import_module('fetch_hdx')
        res = load_json('backfill_hdx_search_fq.json')['result']
        self.assertEqual(res['count'], 9)
        evs = fetch_hdx.decode(res['results'], datetime(2024, 4, 1, tzinfo=UTC))
        self.assertTrue(evs and all(e['pathogen'] == 'cholera' for e in evs))
        many = [e for e in evs if e['country_code'] is None]
        self.assertTrue(many and all(len(e['countries']) > 3 for e in many))     # > 3 countries: one event naming them all


class WAHISBackfillTests(Sandbox):
    SINCE = datetime(2025, 1, 1, tzinfo=UTC)
    UNTIL = datetime(2025, 1, 16, tzinfo=UTC)
    # SYNTHETIC report chains for the five events of the captured 2025-01 list page. The latest report of each matches
    # the list's own reportId and submissionDate; the earlier reports are made up.
    CHAINS = {
        171242: (2, 170900, '2025-01-13T17:05:06.632+00:00', 'MRT'), 170900: (1, 170500, '2024-12-20T09:00:00.000+00:00', 'MRT'),
        171491: (0, None, '2025-01-13T14:34:57.498+00:00', 'ARM'),
        171489: (3, 171300, '2025-01-13T13:10:36.696+00:00', 'SVN'), 171300: (2, 171100, '2025-01-06T10:00:00.000+00:00', 'SVN'),
        171100: (1, 171000, '2025-01-02T10:00:00.000+00:00', 'SVN'), 171000: (0, None, '2025-01-01T05:00:00.000+00:00', 'SVN'),
        171447: (5, 170000, '2025-01-10T09:13:46.011+00:00', 'EST'), 170000: (4, 169000, '2024-10-01T09:00:00.000+00:00', 'EST'),
    }

    def fake(self):
        lst = load_json('backfill_wahis_list_2025-01.json')['list']
        base = load_json('wahis_report.json')
        calls = {'post': 0, 'get': []}

        def post(url, body, timeout=None):
            calls['post'] += 1
            self.assertEqual(url, importlib.import_module('fetch_wahis').LIST_URL)
            self.assertEqual(body['submissionDate'], {'from': '2025-01-01T00:00:00.000Z', 'to': '2025-01-16T00:00:00.000Z'})
            return {'list': lst, 'totalSize': len(lst)} if body['pageNumber'] == 0 else {'list': [], 'totalSize': len(lst)}

        def get(url, timeout=None, retries=None):
            rid = int(url.split('/report/')[1].split('/')[0])
            calls['get'].append(rid)
            n, prev, when, iso3 = self.CHAINS[rid]
            d = copy.deepcopy(base)
            d['report'].update(reportId=rid, reportNumber=n, previousReportId=prev, reportedOn=when)
            d['event']['country']['isoCode'] = iso3
            d['outbreaks'] = []
            return d
        return post, get, calls

    def test_walks_each_event_back_to_its_first_report(self):
        post, get, calls = self.fake()
        st, items = bf_wahis.run(self.SINCE, self.UNTIL, post=post, get=get, sleep=lambda s: None)
        self.assertEqual(calls['post'], 1)
        self.assertNotIn(171538, calls['get'])                               # African swine fever: not a PULSE pathogen, no detail
        self.assertEqual(sorted(calls['get']), sorted(self.CHAINS))
        self.assertEqual((st['status'], st['intervals']), ('complete', [['2025-01-01T00:00:00Z', '2025-01-16T00:00:00Z']]))
        self.assertEqual((st['events_on_pulse_pathogens'], st['other_disease_events'], st['items']), (4, 1, 9))
        self.assertEqual((st['checks']['compared'], st['checks']['latest_date_diff_s']), (4, 0))
        evs = list(items.values())
        self.assertTrue(all(e['source'] == 'WOAH WAHIS' and 'animal' in e['tags'] and e['bf']['date_field'] == 'report.reportedOn' for e in evs))
        by_country = {}
        for e in evs:
            by_country.setdefault(e['country_code'], []).append(e)
        self.assertEqual({k: len(v) for k, v in by_country.items()}, {'MR': 2, 'AM': 1, 'SI': 4, 'EE': 2})
        si = sorted(by_country['SI'], key=lambda e: e['updated'])
        self.assertEqual(si[0]['updated'], '2025-01-01T05:00:00Z')
        self.assertTrue(si[0]['headline'].startswith('Immediate notification') and si[0]['pathogen'] == 'h5n1')
        self.assertEqual(by_country['AM'][0]['pathogen'], 'rabies')
        self.assertEqual(sorted(e['updated'][:10] for e in by_country['EE']), ['2024-10-01', '2025-01-10'])   # stops once before `since`
        self.assertEqual(len({e['id'] for e in evs}), 9)                    # one item per report, not per event

    def test_budget_then_resume(self):
        post, get, calls = self.fake()
        st, items = bf_wahis.run(self.SINCE, self.UNTIL, budget=bf_common.Budget(max_requests=4), post=post, get=get, sleep=lambda s: None)
        self.assertEqual((st['status'], st['resumable'], st['intervals']), ('partial', True, []))
        st, items = bf_wahis.run(self.SINCE, self.UNTIL, post=post, get=get, sleep=lambda s: None)
        self.assertEqual((st['status'], st['items'], st['requests']), ('complete', 9, 10))
        self.assertEqual(calls['post'], 1)                                    # the list chunk is not fetched again
        self.assertEqual(sorted(calls['get']), sorted(self.CHAINS))           # no report fetched twice


class ReliefWebBackfillTests(Sandbox):
    SINCE = datetime(2024, 4, 1, tzinfo=UTC)

    def test_no_app_name_no_request(self):
        st, items = bf_reliefweb.run(self.SINCE, datetime(2026, 11, 14, tzinfo=UTC), post_json=no_network, appname='')
        self.assertEqual((st['status'], st['intervals'], items, st['requests']), ('skipped', [], {}, 0))
        self.assertIn('decision 9', st['reason'])

    def test_dated_by_creation(self):
        # SYNTHETIC payload in the documented v2 shape (the live decoder has not seen a real one yet)
        payload = {'totalCount': 1, 'count': 1, 'data': [{'id': 99, 'fields': {
            'name': 'Uganda: Ebola Outbreak - Jan 2025', 'glide': 'EP-2025-000001-UGA', 'status': 'ongoing',
            'date': {'created': '2025-01-30T00:00:00+00:00', 'changed': '2026-03-01T00:00:00+00:00'},
            'country': [{'name': 'Uganda', 'iso3': 'uga'}], 'primary_country': {'iso3': 'uga'}, 'type': [{'name': 'Epidemic'}],
            'url': 'https://reliefweb.int/disaster/ep-2025-000001-uga', 'description': 'Ebola outbreak declared.'}}]}
        bodies = []

        def post(url, body):
            self.assertIn('appname=test-app', url)
            bodies.append(body)
            return payload
        st, items = bf_reliefweb.run(self.SINCE, datetime(2026, 11, 14, tzinfo=UTC), post_json=post, appname='test-app')
        self.assertEqual(bodies[0]['filter']['conditions'][1], {'field': 'date.created', 'value': {'from': '2024-04-01T00:00:00Z'}})
        self.assertEqual(st['status'], 'complete')
        ev = list(items.values())[0]
        self.assertEqual((ev['updated'], ev['bf']['changed'][:10], ev['pathogen'], ev['country_code']), ('2025-01-30T00:00:00Z', '2026-03-01', 'ebola', 'UG'))


class GDELTBackfillTests(Sandbox):
    UNTIL = datetime(2026, 11, 14, tzinfo=UTC)
    SINCE = datetime(2026, 11, 1, tzinfo=UTC)

    def setUp(self):
        super().setUp()
        self.saved.append((bf_gdelt, 'QUERIES', bf_gdelt.QUERIES))
        bf_gdelt.QUERIES = [q for q in bf_gdelt.QUERIES if q[0] in ('cholera', 'outbreak-theme')]

    def test_bursts_start_only_at_the_offsets(self):
        t = lambda h, m, s=0: datetime(2026, 11, 14, h, m, s, tzinfo=UTC)
        self.assertEqual(bf_gdelt.SLOT_MINUTES, (5, 35))
        self.assertEqual(bf_gdelt.burst_window(t(10, 5)), (t(10, 5), t(10, 25)))
        self.assertIsNotNone(bf_gdelt.burst_window(t(10, 24, 59)))
        self.assertIsNone(bf_gdelt.burst_window(t(10, 25)))
        self.assertIsNone(bf_gdelt.burst_window(t(10, 4, 59)))
        self.assertEqual(bf_gdelt.next_burst(t(10, 30)), t(10, 35))
        self.assertEqual(bf_gdelt.next_burst(t(10, 59)), t(11, 5))
        self.assertGreaterEqual(bf_gdelt.SPACING_S, 6.0)

    def test_every_pathogen_has_one_query(self):
        bf_gdelt.QUERIES = self.saved[-1][2]
        slugs = [p[0] for p in pathogens.PATHOGENS if p[0] != 'unknown']
        targeted = [s for q in bf_gdelt.QUERIES if q[1] == 'pathogen' for s in q[2]]
        self.assertEqual(sorted(targeted), sorted(slugs))
        qs = bf_gdelt.make_queries({'TAX_DISEASE_CHOLERA', 'TAX_DISEASE_OUTBREAK'})
        chol = next(q for q in qs if q['label'] == 'cholera')
        self.assertEqual((chol['form'], chol['query']), ('theme', 'theme:TAX_DISEASE_CHOLERA sourcelang:english'))
        meas = next(q for q in qs if q['label'] == 'measles')
        self.assertEqual((meas['form'], meas['query']), ('keywords', '(measles OR rubeola) sourcelang:english'))
        self.assertEqual(next(q for q in qs if q['label'] == 'outbreak-theme-translingual')['query'], 'theme:TAX_DISEASE_OUTBREAK')

    def harness(self, rate_limits_at=(), first_slice_full=True):
        """A fake clock that sleep() advances, and a fake GDELT that records when each request was sent (SYNTHETIC articles)."""
        clock = {'t': datetime(2026, 11, 14, 9, 50, tzinfo=UTC)}
        sent, self.answered = [], []

        def wall():
            return clock['t']

        def sleep(s):
            clock['t'] += timedelta(seconds=s)

        def art(title, url, when):
            return {'title': title, 'url': url, 'seendate': when.strftime('%Y%m%dT%H%M%SZ'), 'domain': 'example.org',
                    'language': 'English', 'sourcecountry': 'Sudan'}

        def fetch_json(url):
            sent.append(clock['t'])
            n = len(sent)
            clock['t'] += timedelta(seconds=15)                             # GDELT answers in 14–18 s
            self.answered.append(clock['t'])
            if n in rate_limits_at:
                raise bf_gdelt.RateLimited('HTTP 429')
            q = url.split('query=')[1].split('&')[0]
            a = datetime.strptime(url.split('startdatetime=')[1][:14], '%Y%m%d%H%M%S').replace(tzinfo=UTC)
            b = datetime.strptime(url.split('enddatetime=')[1][:14], '%Y%m%d%H%M%S').replace(tzinfo=UTC)
            if 'TAX_DISEASE_OUTBREAK' in q and (b - a) == timedelta(days=1):          # the probe
                back = (self.UNTIL - b).days + 1
                return {'articles': [art('x', f'p{back}', a)]} if back <= 92 else {}
            if 'CHOLERA' in q or 'cholera' in q:
                if first_slice_full and a == self.SINCE and (b - a) == timedelta(days=2):
                    return {'articles': [art(f'Item {i}', f'u{i}', a) for i in range(250)]}
                return {'articles': [art('Cholera outbreak in Sudan kills 12', f'https://example.org/c/{a:%Y%m%d%H}', a + timedelta(hours=3)),
                                     art('Cholera cases rise in Yemen', 'https://example.org/shared', a + timedelta(hours=1))]}
            return {'articles': [art('Cholera cases rise in Yemen', 'https://example.org/shared', a + timedelta(hours=5))]} if a == self.SINCE else {}

        def fetch_text(url):
            sent.append(clock['t'])
            clock['t'] += timedelta(seconds=2)
            self.answered.append(clock['t'])
            self.assertEqual(url, bf_gdelt.THEMES_LOOKUP)
            return 'TAX_DISEASE_CHOLERA\t1000\nTAX_DISEASE_OUTBREAK\t9999\n'
        return clock, sent, wall, sleep, fetch_json, fetch_text

    def test_probe_slices_split_and_pacing(self):
        clock, sent, wall, sleep, fetch_json, fetch_text = self.harness(rate_limits_at=(6, 7, 8))
        st, items = bf_gdelt.run(self.SINCE, self.UNTIL, budget=bf_common.Budget(seconds=10 ** 9, max_requests=500, clock=lambda: 0),
                                 fetch_json=fetch_json, fetch_text=fetch_text, wall=wall, sleep=sleep)
        # probe: 85 and 92 days back answer, 100 is empty → reach 92 days back
        self.assertEqual([(p['days_back'], bool(p['articles'])) for p in st['probe']], [(85, True), (92, True), (100, False)])
        self.assertEqual(st['reach'], '2026-08-14T00:00:00Z')
        # every request inside a burst that starts at :05 or :35, and at least 6 s after the previous answer
        self.assertTrue(all(bf_gdelt.burst_window(t) is not None for t in sent))
        self.assertEqual(len(self.answered), len(sent))
        gaps = [nxt - done for done, nxt in zip(self.answered, sent[1:])]
        self.assertGreaterEqual(min(gaps), timedelta(seconds=6))            # never sooner than 6 s after the last answer
        # three 429s in a row end the burst; the work resumes at the next offset
        self.assertEqual(st['rate_limited'], 3)
        self.assertGreaterEqual(st['bursts'], 2)
        after = [t for t in sent[8:]]
        self.assertTrue(after and bf_gdelt.burst_window(after[0])[0] > bf_gdelt.burst_window(sent[7])[0])
        # the full first slice was split; its halves answered
        chol = next(q for q in st['queries'] if q['label'] == 'cholera')
        self.assertEqual(chol['form'], 'theme')
        self.assertEqual(chol['slices']['capped'], 0)
        self.assertEqual(chol['intervals'], [['2026-11-01T00:00:00Z', '2026-11-14T00:00:00Z']])
        self.assertEqual(st['status'], 'complete')
        evs = list(items.values())
        self.assertFalse(any(e['title'].startswith('Item ') for e in evs))   # the full slice's rows were not kept, only its halves'
        sudan = [e for e in evs if e['country_code'] == 'SD']
        self.assertTrue(sudan and all(e['pathogen'] == 'cholera' and e['bf']['date_field'] == 'seendate' for e in sudan))
        shared = [e for e in evs if e['url'] == 'https://example.org/shared']
        self.assertEqual(len(shared), 1)                                      # one item per article
        self.assertEqual(shared[0]['updated'], '2026-11-01T01:00:00Z')       # its earliest seendate
        self.assertEqual(shared[0]['tags'], ['cholera', 'outbreak-theme'])    # both queries returned it

    def test_budget_then_resume(self):
        clock, sent, wall, sleep, fetch_json, fetch_text = self.harness(first_slice_full=False)
        bud = lambda n: bf_common.Budget(seconds=10 ** 9, max_requests=n, clock=lambda: 0)
        st, items = bf_gdelt.run(self.SINCE, self.UNTIL, budget=bud(6), fetch_json=fetch_json, fetch_text=fetch_text, wall=wall, sleep=sleep)
        self.assertEqual((st['status'], st['resumable']), ('partial', True))
        n1 = len(sent)
        st, items = bf_gdelt.run(self.SINCE, self.UNTIL, budget=bud(500), fetch_json=fetch_json, fetch_text=fetch_text, wall=wall, sleep=sleep)
        self.assertEqual(st['status'], 'complete')
        self.assertEqual(len(sent) - n1, st['planned_slices'] - 2)            # the 2 slices done in leg 1 are not asked again
        self.assertEqual(len(st['probe']), 3)                                 # the probe is not repeated


class MergeTests(Sandbox):
    def test_one_file_and_a_manifest(self):
        get = lambda url, timeout=None: read_text('paho_alerts.html') if url == bf_paho.page_url(0) else ''
        bf_paho.run(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 11, 14, tzinfo=UTC), get=get, sleep=lambda s: None)
        bf_reliefweb.run(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 11, 14, tzinfo=UTC), post_json=no_network, appname='')
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(backfill.merge(leg=1), 0)
        self.assertEqual(buf.getvalue().strip().splitlines()[-1], 'UNFINISHED=gdelt,wahis,hdx')
        with gzip.open(os.path.join(self.tmp.name, 'events.json.gz'), 'rt', encoding='utf-8') as f:
            env = json.load(f)
        self.assertEqual((env['schema'], env['count'], set(env['by_source'])), ('health-feeds.backfill/1', len(env['items']), {'PAHO'}))
        self.assertTrue(all(e['updated'] and e['bf']['date_field'] for e in env['items']))
        with open(os.path.join(self.tmp.name, 'manifest.json'), encoding='utf-8') as f:
            man = json.load(f)
        self.assertEqual(man['schema'], 'health-feeds.backfill.manifest/1')
        self.assertEqual({k: v['status'] for k, v in man['sources'].items()},
                         {'PAHO': 'complete', 'ReliefWeb': 'skipped', 'GDELT': 'not_run', 'WOAH WAHIS': 'not_run', 'HDX': 'not_run'})
        self.assertEqual(man['sources']['ReliefWeb']['intervals'], [])
        self.assertEqual(man['legs'][0]['leg'], 1)


if __name__ == '__main__':
    unittest.main()
