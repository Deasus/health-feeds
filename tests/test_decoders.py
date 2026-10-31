"""Decoder tests on captured fixtures (no network). Run: python3 -m unittest discover -s tests -q"""
import importlib
import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDS = os.path.join(ROOT, 'feeds')
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
for d in ['_shared', 'who', 'cdc', 'travel', 'ecdc', 'hdx', 'gdelt', 'gnews', 'fda', 'reliefweb', 'paho', 'wahis', 'aphis', 'whispers', 'extract']:
    sys.path.insert(0, os.path.join(FEEDS, d))
os.environ['HF_OFFLINE'] = '1'

hf = importlib.import_module('hf')
geocode = importlib.import_module('geocode')
pathogens = importlib.import_module('pathogens')
events = importlib.import_module('events')
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def fixture(name):
    return os.path.join(FIX, name)


def load_json(name):
    with open(fixture(name), encoding='utf-8') as f:
        return json.load(f)


class GeocodeTests(unittest.TestCase):
    def test_titles(self):
        cases = {
            'Middle East respiratory syndrome coronavirus - Kingdom of Saudi Arabia': ['SA'],
            'Level 1 - Ebola Bundibugyo Virus Disease in Kenya': ['KE'],
            'Cholera in the Democratic Republic of the Congo and Uganda': ['CD', 'UG'],
            'Level 2 - Chikungunya in French Guiana': ['GF'],
            'Malaria in Mayotte': ['YT'],
            'Measles outbreak in Georgia reported by CDC in Atlanta': [],
            'Bird flu found in Niger and Nigeria': ['NE', 'NG'],
            'Yellow fever - Global': [],
        }
        for text, want in cases.items():
            self.assertEqual([c['iso2'] for c in geocode.find_countries(text)], want, text)

    def test_codes_and_area(self):
        self.assertEqual(geocode.country('COD')['iso2'], 'CD')
        self.assertEqual(geocode.country('us')['name'], 'United States')
        self.assertEqual(geocode.state('MI')['name'], 'Michigan')
        self.assertEqual(geocode.area_of('SA'), 'middleEast')
        self.assertEqual(geocode.area_of('KE'), 'africa')
        self.assertEqual(geocode.area_of('US'), 'unitedStates')
        self.assertEqual(geocode.country('SO')['name'], 'Somalia')          # not Somaliland, which shares the code in Natural Earth
        self.assertEqual(geocode.country('CY')['name'], 'Cyprus')


class PathogenTests(unittest.TestCase):
    def test_find(self):
        self.assertEqual(pathogens.primary('Ebola disease caused by Bundibugyo virus')['slug'], 'ebola')
        self.assertEqual(pathogens.primary('Pneumonia of undetermined etiology - the Russian Federation')['slug'], 'unknown')
        self.assertEqual(pathogens.primary('Chapare haemorrhagic fever - Bolivia')['slug'], 'chapare')
        self.assertEqual([p['slug'] for p in pathogens.find('bird flu and seasonal flu')], ['h5n1', 'influenza'])
        self.assertIsNone(pathogens.primary('Traffic safety in France'))

    def test_counts(self):
        cases, deaths, quote = events.counts_from_text(
            'Between 1 January and 5 October 2026, Saudi Arabia reported eight laboratory-confirmed cases of MERS-CoV, including one death.')
        self.assertEqual((cases, deaths), (8, 1))
        cases, deaths, _ = events.counts_from_text('a total of 3,605 confirmed cases including 1587 deaths; 1 new case was reported')
        self.assertEqual((cases, deaths), (3605, 1587))
        self.assertEqual(events.counts_from_text('no figures here'), (None, None, None))


class WHOTests(unittest.TestCase):
    def test_decode(self):
        who = importlib.import_module('fetch_who')
        items = who.decode(load_json('who_don.json'))
        self.assertEqual(len(items), 2)
        mers = items[0]
        self.assertEqual((mers['pathogen'], mers['country_code'], mers['severity_label'], mers['kind']), ('mers', 'SA', 'SEVERE', 'outbreak'))
        self.assertEqual((mers['cases'], mers['deaths']), (8, 1))
        self.assertTrue(mers['url'].startswith('https://www.who.int/emergencies/disease-outbreak-news/item/'))
        self.assertEqual(mers['geo_precision'], 'country')
        self.assertIsNotNone(mers['lat'])


class CDCTravelTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_cdc_travel')
        items = mod.decode(ET.parse(fixture('cdc_travel_notices.xml')).getroot())
        self.assertGreaterEqual(len(items), 20)
        kenya = next(i for i in items if 'Kenya' in i['title'])
        self.assertEqual((kenya['pathogen'], kenya['country_code'], kenya['severity_label']), ('ebola', 'KE', 'MINOR'))
        self.assertEqual(kenya['facts'][0], ['Level', '1'])


class StateDeptTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_state')
        advisories, items = mod.decode(ET.parse(fixture('state_advisories.xml')).getroot())
        self.assertGreaterEqual(len(advisories), 12)
        kenya = next(a for a in advisories if a['country'] == 'Kenya')
        self.assertEqual((kenya['iso2'], kenya['level'], kenya['health']), ('KE', 3, True))
        france = next(a for a in advisories if a['country'] == 'France')
        self.assertFalse(france['health'])
        ev = next(i for i in items if i['country_code'] == 'KE')
        self.assertEqual(ev['severity_label'], 'MODERATE')
        self.assertIn('travel to Kenya', ev['headline'])           # strip_html keeps the space across <p>


class ECDCTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_ecdc')
        items = mod.decode(ET.parse(fixture('ecdc_cdtr.xml')).getroot())
        self.assertGreaterEqual(len(items), 5)
        wk42 = items[0]
        self.assertIn('ebola', wk42['tags'])
        self.assertIn('mpox', wk42['tags'])
        self.assertGreaterEqual(len([f for f in wk42['facts'] if f[0] == 'Topic']), 5)
        self.assertEqual(wk42['severity_label'], 'INFO')


class GDELTTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_gdelt')
        arts = load_json('gdelt_articles.json')['articles']
        for a in arts:
            a['_q'] = ['outbreak-theme']
        evs, rows = mod.decode(arts, NOW)
        self.assertEqual(len(rows), len(arts))
        self.assertTrue(all(e['source_tier'] == 'news' and e['lat'] is None and e['severity'] == 0 for e in evs))
        self.assertEqual(mod.clean_title('Where are Kenyans seeing  misleading Ebola information ? '), 'Where are Kenyans seeing misleading Ebola information?')
        flu_only, _ = mod.decode([{'title': 'Flu season tips for families', 'url': 'u1', 'seendate': '20261022T150000Z'}], NOW)
        self.assertEqual(flu_only, [])
        flu_outbreak, _ = mod.decode([{'title': 'Flu outbreak closes three schools in Ohio', 'url': 'u2', 'seendate': '20261022T150000Z'}], NOW)
        self.assertEqual(flu_outbreak[0]['pathogen'], 'influenza')


class FDATests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_fda')
        items = mod.decode(load_json('fda_enforcement.json')['results'], NOW)
        self.assertEqual(len(items), 1)                    # the allergen recall is not an event
        ev = items[0]
        self.assertEqual((ev['pathogen'], ev['severity_label'], ev['admin1_code'], ev['country_code']), ('listeria', 'MODERATE', 'MI', 'US'))
        self.assertEqual(ev['geo_precision'], 'admin1')


class HDXTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_hdx')
        res = load_json('hdx_search.json')['result']['results']
        items = mod.decode(res, NOW - timedelta(days=3650))
        titles = [i['title'] for i in items]
        self.assertTrue(any('Yemen' in t for t in titles))
        self.assertFalse(any('vaccine' in t for t in titles))
        yemen = next(i for i in items if 'Yemen' in i['title'])
        self.assertEqual((yemen['pathogen'], yemen['country_code'], yemen['severity_label']), ('cholera', 'YE', 'INFO'))


class NNDSSTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_cdc_nndss')
        rows = load_json('nndss_week.json')
        items, table = mod.decode(rows, '2026', 41, NOW)
        self.assertTrue(table)
        self.assertTrue(all(i['tags'] == ['new-52-week-high'] for i in items))
        self.assertTrue(all('chronic' not in i['title'].lower() for i in items))
        self.assertTrue(all(i['admin1'] or i['country_code'] for i in items))
        self.assertFalse(any(i['area'] in ('U.S. Residents', 'New England') for i in items))
        synthetic = [{'states': 'Michigan', 'label': 'Measles, Confirmed', 'm1': '12', 'm2': '3', 'm3': '40', 'm4': '2',
                      'geocode': {'type': 'Point', 'coordinates': [-84.55, 42.73]}},
                     {'states': 'New England', 'label': 'Measles, Confirmed', 'm1': '12', 'm2': '3'},
                     {'states': 'Ohio', 'label': 'Hepatitis C, chronic, Probable', 'm1': '99', 'm2': '1'}]
        items, _ = mod.decode(synthetic, '2026', 41, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['pathogen'], items[0]['severity_label'], items[0]['admin1_code'], items[0]['lat']), ('measles', 'MODERATE', 'MI', 42.73))


class NWSSTests(unittest.TestCase):
    def test_summarize_and_events(self):
        mod = importlib.import_module('fetch_cdc_nwss')
        rows = load_json('nwss_measles.json')
        per_site, by_state = mod.summarize(rows)
        self.assertTrue(per_site)
        self.assertTrue(any(s['detect14'] for s in per_site))
        evs = mod.to_events('measles', per_site, by_state, NOW)
        self.assertTrue(evs)
        self.assertTrue(all(e['severity_label'] == 'MODERATE' and e['admin1_code'] for e in evs))
        self.assertFalse(any(r.get('source') == 'WastewaterSCAN' for r in rows))

    def test_trend(self):
        mod = importlib.import_module('fetch_cdc_nwss')
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = []
        for d in range(0, 21, 3):
            conc = 1000 if d < 14 else 10000           # ten-fold jump in the last week
            rows.append({'site': 'A', 'state_territory': 'mi', 'source': 'State_Territory', 'county_fips': '26081', 'counties_served': 'Kent',
                         'population_served': '100000', 'sample_collect_date': (base + timedelta(days=d)).strftime('%Y-%m-%d'),
                         'pcr_target': 'sars-cov-2', 'pcr_target_avg_conc': str(conc), 'pcr_target_detect': 'yes'})
        per_site, by_state = mod.summarize(rows)
        self.assertEqual(per_site[0]['trend'], 'rising')
        self.assertEqual(by_state['MI']['rising'], 1)


class ExtractTests(unittest.TestCase):
    def test_merge_rules(self):
        mod = importlib.import_module('build_events')
        who = events.make(source='WHO DON', source_tier='official', kind='outbreak', title='Ebola - DRC', updated=NOW - timedelta(days=2),
                          pathogen='ebola', country_code='CD', severity=events.SEVERE, source_id='don1')
        news_corr = events.make(source='GDELT', source_tier='news', kind='news', title='Ebola spreads in Congo', updated=NOW - timedelta(days=1),
                                pathogen='ebola', country_code='CD', lat=None, lng=None, geo_precision='none', source_id='n1')
        news_alone = events.make(source='GDELT', source_tier='news', kind='news', title='Measles case in Peru', updated=NOW,
                                 pathogen='measles', country_code='PE', lat=None, lng=None, geo_precision='none', source_id='n2')
        old = events.make(source='WHO DON', source_tier='official', kind='outbreak', title='Old cholera DON', updated=NOW - timedelta(days=200),
                          pathogen='cholera', country_code='YE', source_id='don0')
        merged = mod.merge([who, news_corr, news_alone, old], NOW)
        by_id = {e['id']: e for e in merged}
        self.assertNotIn(old['id'], by_id)
        self.assertEqual(by_id[news_corr['id']]['corroboration'], 1)
        self.assertEqual(by_id[news_corr['id']]['severity_label'], 'MINOR')
        self.assertIsNotNone(by_id[news_corr['id']]['lat'])
        self.assertEqual(by_id[news_alone['id']]['corroboration'], 0)
        self.assertIsNone(by_id[news_alone['id']]['lat'])
        self.assertEqual(merged[0]['id'], who['id'])                 # SEVERE first
        self.assertTrue(all('cluster' in e for e in merged))


if __name__ == '__main__':
    unittest.main()


class StateDeptCodeTests(unittest.TestCase):
    def test_fips_tag_is_not_iso(self):
        mod = importlib.import_module('fetch_state')
        import html as _html
        desc = _html.escape('Exercise increased caution<p>in <b>China</b> due to <b>health</b> risks.</p>')   # the feed escapes its HTML
        rss = ('<rss><channel><item><title>China - Level 2: Exercise Increased Caution</title><link>https://x/china</link>'
               f'<description>{desc}</description>'
               '<category domain="Threat-Level">Level 2: Exercise Increased Caution</category>'
               '<category domain="Country-Tag">CH</category><guid>https://x/china</guid></item></channel></rss>')
        advisories, items = mod.decode(ET.fromstring(rss))
        self.assertEqual(advisories[0]['iso2'], 'CN')                   # CH is FIPS for China, not Switzerland
        self.assertEqual(items[0]['country_code'], 'CN')
        self.assertEqual(geocode.country_from_fips('SZ')['iso2'], 'CH')  # and FIPS SZ is Switzerland


class RadiusAndDatesTests(unittest.TestCase):
    def test_radius(self):
        ev = events.make(source='WHO DON', source_tier='official', kind='outbreak', title='Ebola - DRC', updated=NOW, pathogen='ebola', country_code='CD', source_id='r1')
        self.assertGreater(ev['geo_radius_km'], 800)
        mi = events.make(source='CDC NWSS', source_tier='official', kind='surveillance', title='x', updated=NOW, pathogen='measles',
                         country_code='US', admin1='Michigan', admin1_code='MI', source_id='r2')
        self.assertTrue(100 < mi['geo_radius_km'] < 500)
        pt = events.make(source='CDC NNDSS', source_tier='official', kind='surveillance', title='x', updated=NOW, pathogen='measles',
                         lat=42.7, lng=-84.5, source_id='r3')
        self.assertEqual(pt['geo_radius_km'], 0.0)

    def test_mmwr_week_end(self):
        mod = importlib.import_module('fetch_cdc_nndss')
        self.assertEqual(mod.mmwr_week_end('2026', 41).strftime('%Y-%m-%d %a'), '2026-10-17 Sat')   # 2026 week 1 ends 10 Jan
        self.assertEqual(mod.mmwr_week_end('2026', 1).strftime('%Y-%m-%d %a'), '2026-01-10 Sat')


class GazetteerTests(unittest.TestCase):
    def test_find_places(self):
        gazetteer = importlib.import_module('gazetteer')
        hits = gazetteer.find_places('Ebola cases rise in Bunia and Goma', 'CD')
        self.assertEqual([h['name'] for h in hits], ['Bunia', 'Goma'])
        self.assertEqual(gazetteer.find_places('Mobile phone app tracks outbreaks'), [])          # stop-word without a hint
        lansing = gazetteer.find_places('Measles outbreak in Lansing schools', 'US')[0]
        self.assertEqual((lansing['country'], lansing['admin1']), ('US', 'MI'))
        self.assertEqual(gazetteer.city('Nairobi')['country'], 'KE')


class GoogleNewsTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_gnews')
        root = ET.parse(fixture('gnews_measles.xml')).getroot()
        rows = []
        for it in hf.rss_items(root):
            outlet = it['extra'].get('source') or ''
            rows.append({'title': mod.clean_title(it['title'], outlet), 'url': it['link'], 'domain': outlet, 'seendate': it['pubDate'], '_q': ['measles outbreak']})
        evs, out = mod.decode(rows, NOW)
        self.assertEqual(len(out), 12)
        self.assertTrue(all(not r['title'].endswith(' - ' + (r['domain'] or '')) for r in out))
        self.assertTrue(evs and all(e['source'] == 'Google News' and e['pathogen'] == 'measles' and e['lat'] is None for e in evs))


class CuratorTests(unittest.TestCase):
    def fake_call(self, batch):
        arr = []
        for h in batch:
            t = h['title'].lower()
            if 'opinion' in t:
                arr.append({'i': h['i'], 'outbreak': False})
            elif 'pennsylvania' in t:
                arr.append({'i': h['i'], 'outbreak': True, 'pathogen': 'measles', 'country': 'US', 'place': 'Pennsylvania', 'cases': 1317, 'deaths': 5, 'quote': '1,317 Measles Cases, 5 Deaths'})
            elif 'bunia' in t:
                arr.append({'i': h['i'], 'outbreak': True, 'pathogen': 'ebola', 'country': 'CD', 'place': 'Bunia', 'cases': 12, 'deaths': None, 'quote': '12 new cases'})
            else:
                arr.append({'i': h['i'], 'outbreak': True, 'pathogen': None, 'country': None, 'place': None, 'cases': None, 'deaths': None, 'quote': None})
        return arr, {'input_tokens': 10, 'output_tokens': 10}

    def test_enrich_and_merge(self):
        import os, tempfile
        llm = importlib.import_module('llm_extract'); be = importlib.import_module('build_events')
        tmp = tempfile.mkdtemp(); llm.CACHE_FILE = os.path.join(tmp, 'llm_cache.json')
        who = events.make(source='WHO DON', source_tier='official', kind='outbreak', title='Ebola - DRC', updated=NOW - timedelta(days=1),
                          pathogen='ebola', country_code='CD', severity=events.SEVERE, source_id='w1',
                          body='Cases were reported from Bunia health zone in Ituri province.')
        news = events.make(source='GDELT', source_tier='news', kind='news', title='Unknown illness: 12 new cases in Bunia', updated=NOW,
                           pathogen='unknown', lat=None, lng=None, geo_precision='none', source_id='n1')
        opinion = events.make(source='GDELT', source_tier='news', kind='news', title='Opinion: measles and the politics of fear', updated=NOW,
                              pathogen='measles', country_code='US', lat=None, lng=None, geo_precision='none', source_id='n2')
        nndss = events.make(source='CDC NNDSS', source_tier='official', kind='surveillance', title='Measles - Pennsylvania', updated=NOW,
                            pathogen='measles', country_code='US', admin1='Pennsylvania', admin1_code='PA', source_id='s1')
        pa_news = events.make(source='Google News', source_tier='news', kind='news', title='Pennsylvania Reports 1,317 Measles Cases, 5 Deaths', updated=NOW,
                              pathogen='measles', country_code='US', lat=None, lng=None, geo_precision='none', source_id='n3')
        items = be.enrich_news([who, news, opinion, nndss, pa_news], NOW, call=self.fake_call)
        self.assertEqual(len(items), 4)                                   # the opinion piece is dropped
        pa = next(e for e in items if e['source'] == 'Google News')
        self.assertEqual((pa['admin1_code'], pa['cases'], pa['deaths']), ('PA', 1317, 5))
        n = next(e for e in items if e['source'] == 'GDELT')
        self.assertEqual((n['pathogen'], n['country_code'], n['cases']), ('ebola', 'CD', 12))
        self.assertIn('llm-extracted', n['tags'])
        items = be.upgrade_official_places(items)
        w = next(e for e in items if e['source'] == 'WHO DON')
        self.assertEqual((w['geo_precision'], w['geo_radius_km']), ('point', 15.0))      # DON placed at Bunia from its text
        merged = be.merge(items, NOW)
        n = next(e for e in merged if e['source'] == 'GDELT')
        self.assertEqual(n['corroboration'], 1)
        self.assertEqual(n['geo_precision'], 'point')                     # corroborated news lands on the stated city
        self.assertTrue(all('_city' not in e and '_state' not in e for e in merged))
        self.assertAlmostEqual(n['lat'], 1.559, places=2)
        pa = next(e for e in merged if e['source'] == 'Google News')
        self.assertEqual(pa['geo_precision'], 'admin1')                   # state named by the curator, corroborated by NNDSS
        self.assertTrue(100 < pa['geo_radius_km'] < 500)
        self.assertNotIn('_city', n)
        # second call hits the cache: no model call needed
        r2 = llm.enrich(['Unknown illness: 12 new cases in Bunia'], call=lambda b: (_ for _ in ()).throw(AssertionError('should be cached')))
        self.assertEqual(r2['Unknown illness: 12 new cases in Bunia']['pathogen'], 'ebola')


# ── 2026-10-30: the HealthMap delta (PLAN §11) ─────────────────────────────────

class WAHISTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_wahis')
        lst = load_json('wahis_list.json')['list']
        rep = load_json('wahis_report.json')                       # Germany, HPAI in poultry, immediate notification
        reports = {str(rep['report']['reportId']): mod.compact(rep)}
        germany = dict(lst[0])
        germany.update(reportId=rep['report']['reportId'], eventId=rep['event']['eventId'], country='Germany', reportType='IN',
                       reportNumber=0, disease=rep['event']['disease']['name'], submissionDate=rep['report']['reportedOn'])
        evs = mod.decode([germany] + lst, reports, datetime(2026, 10, 30, 12, tzinfo=timezone.utc))
        self.assertTrue(evs and all('animal' in e['tags'] and e['source'] == 'WOAH WAHIS' and e['cases'] is None for e in evs))
        self.assertFalse(any(re.search(r'lumpy|swine|typhoid', e['title'], re.I) for e in evs))     # animal-only diseases and fowl typhoid stay out
        de = [e for e in evs if e['country_code'] == 'DE'][0]
        self.assertEqual((de['pathogen'], de['severity_label']), ('h5n1', 'MINOR'))
        # two farms ~190 km apart: the point is their midpoint, the ring covers both, so the precision drops to admin1
        self.assertEqual(de['geo_precision'], 'point' if de['geo_radius_km'] <= 60 else 'admin1')
        self.assertTrue(53.0 < de['lat'] < 55.0 and de['geo_radius_km'] >= 10)
        self.assertTrue('Immediate notification' in de['headline'] and 'poultry' in de['title'] and '2 outbreaks' in de['headline'])
        self.assertTrue(any(e['pathogen'] == 'screwworm' and e['country_code'] == 'US' for e in evs))
        self.assertTrue(any(e['pathogen'] == 'west_nile' and e['country_code'] == 'BE' for e in evs))
        self.assertTrue(any(e['pathogen'] == 'anthrax' and e['country_code'] == 'GR' for e in evs))
        lv = [e for e in evs if e['country_code'] == 'LV' and e['pathogen'] == 'h5n1']
        self.assertTrue(lv and all(e['geo_precision'] == 'country' for e in lv))     # no detail cached yet → country centroid
        ceuta = dict(lst[0], reportId=999001, eventId=999001, country='Ceuta', disease='Rabies virus (Inf. with)', reportType='IN',
                     submissionDate='2026-10-29T10:00:00Z')
        ce = mod.decode([ceuta], {}, datetime(2026, 10, 30, 12, tzinfo=timezone.utc))[0]
        self.assertEqual((ce['country_code'], ce['geo_precision'], ce['pathogen']), ('ES', 'point', 'rabies'))
        self.assertTrue(35 < ce['lat'] < 36 and -6 < ce['lng'] < -5 and 'Ceuta' in ce['title'])
        self.assertIsNone(mod.pathogen_for('Fowl typhoid'))
        self.assertIsNone(mod.pathogen_for('African swine fever virus (Inf. with)'))
        self.assertEqual(mod.pathogen_for('Influenza A viruses of high pathogenicity (Inf. with) (non-poultry including wild birds) (2017-)')['slug'], 'h5n1')

    def test_time_budget(self):
        """A slow API: once the budget is spent no detail is fetched, events still publish at country precision, the cache is saved."""
        mod = importlib.import_module('fetch_wahis')
        lst = load_json('wahis_list.json')['list']
        rep = load_json('wahis_report.json')
        calls = []

        def fake_get_json(url, **kw):
            calls.append(url)
            return rep
        names = ('fetch_list', 'get_json', 'DATA', 'TIME_BUDGET_S', 'MAX_REPORT_FETCHES', 'SPACING_S', 'now')
        saved = {n: getattr(mod, n) for n in names}
        saved_hf_data = hf.DATA
        try:
            with tempfile.TemporaryDirectory() as tmp:
                mod.fetch_list = lambda ref: lst
                mod.get_json = fake_get_json
                mod.DATA = hf.DATA = tmp
                mod.SPACING_S = 0
                mod.now = lambda: datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
                mod.TIME_BUDGET_S = -1                                   # budget already spent
                mod.main()
                self.assertEqual(calls, [])
                with open(os.path.join(tmp, 'events.json'), encoding='utf-8') as f:
                    out = json.load(f)
                self.assertTrue(out['items'] and all(e['geo_precision'] == 'country' for e in out['items']))
                self.assertEqual((out['details_fetched'], out['details_cached']), (0, 0))
                self.assertTrue(os.path.exists(os.path.join(tmp, 'reports.json')))
                mod.TIME_BUDGET_S = 600
                mod.MAX_REPORT_FETCHES = 2                                 # budget open: the count cap applies
                mod.main()
                self.assertEqual(len(calls), 2)
                with open(os.path.join(tmp, 'events.json'), encoding='utf-8') as f:
                    out = json.load(f)
                self.assertEqual((out['details_fetched'], out['details_cached']), (2, 2))
        finally:
            for n, v in saved.items():
                setattr(mod, n, v)
            hf.DATA = saved_hf_data


class APHISTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_aphis')
        ref = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with open(fixture('aphis_wild_birds.csv'), encoding='utf-8') as f:
            wild = mod.decode('wild_birds', mod.rows_of(f.read()), ref)
        self.assertGreater(len(wild), 20)
        self.assertTrue(all(e['pathogen'] == 'h5n1' and e['country_code'] == 'US' and 'wild-bird' in e['tags'] and e['cases'] is None for e in wild))
        pts = [e for e in wild if e['geo_precision'] == 'point']
        self.assertGreater(len(pts) / len(wild), 0.9)                 # nearly every county resolves in the Census gazetteer
        self.assertTrue(all(e['geo_radius_km'] and e['geo_radius_km'] > 0 and e['admin1_code'] for e in pts))
        self.assertTrue(all(int(e['facts'][0][1]) >= 1 for e in wild))
        with open(fixture('aphis_mammals.csv'), encoding='utf-8') as f:
            rows = mod.rows_of(f.read())
        mam = mod.decode('mammals', rows, ref)
        self.assertTrue(mam and all('mammal' in e['tags'] and e['title'].startswith('HPAI in mammals: ') for e in mam))
        self.assertEqual(mod.decode('mammals', rows, datetime(2027, 6, 1, tzinfo=timezone.utc)), [])     # nothing in the window


class WHISPersTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_whispers')
        res = load_json('whispers_events.json')['results']
        evs = mod.decode(res, datetime(2026, 10, 30, tzinfo=timezone.utc))
        ids = {e['url'].rsplit('/', 1)[1] for e in evs}
        self.assertIn('205473', ids)            # HPAI suspect, Calumet County WI
        self.assertIn('205497', ids)            # 409 coots and teal, diagnosis pending → undiagnosed die-off
        self.assertNotIn('205304', ids)         # duck plague: wildlife-only (and older than the window)
        self.assertNotIn('15312', ids)          # no start date
        self.assertNotIn('205437', ids)         # West Nile on 19 Aug: outside 45 days of 30 Oct
        hp = [e for e in evs if e['url'].endswith('/205473')][0]
        self.assertEqual((hp['pathogen'], hp['severity_label'], hp['geo_precision'], hp['source']), ('h5n1', 'MINOR', 'point', 'USGS WHISPers'))
        self.assertTrue('suspect' in hp['tags'] and 'Calumet County' in hp['area'] and hp['geo_radius_km'] > 0 and hp['cases'] is None)
        die = [e for e in evs if e['url'].endswith('/205497')][0]
        self.assertEqual(die['pathogen'], 'unknown')
        self.assertTrue('undiagnosed' in die['tags'] and die['severity_label'] == 'INFO' and die['title'].startswith('Wildlife die-off'))
        self.assertIn(die['geo_precision'], ('point', 'admin1'))
        wnv = [e for e in mod.decode(res, datetime(2026, 9, 20, tzinfo=timezone.utc)) if e['pathogen'] == 'west_nile']
        self.assertTrue(wnv and wnv[0]['country_code'] == 'US')


class PAHOTests(unittest.TestCase):
    def test_decode(self):
        mod = importlib.import_module('fetch_paho')
        with open(fixture('paho_alerts.html'), encoding='utf-8') as f:
            evs = mod.decode(f.read())
        self.assertGreaterEqual(len(evs), 10)
        my = [e for e in evs if 'Myiasis' in e['title']][0]
        self.assertEqual((my['pathogen'], my['severity_label'], my['kind'], my['source']), ('screwworm', 'MODERATE', 'alert', 'PAHO'))
        self.assertTrue('regional' in my['tags'] and my['lat'] is None and my['updated'].startswith('2026-10-16'))
        eb = [e for e in evs if 'Bundibugyo' in e['title'] and e['country_code'] == 'CD']
        self.assertTrue(eb and eb[0]['pathogen'] == 'ebola' and eb[0]['lat'] is not None)
        self.assertTrue(all(e['body'] is None for e in evs))                     # no PAHO text is reproduced
        upd = [e for e in evs if e['facts'][0][1] == 'Epidemiological Update']
        self.assertTrue(upd and all(e['severity_label'] == 'MINOR' for e in upd))


class WHOArchiveTests(unittest.TestCase):
    def test_compact(self):
        mod = importlib.import_module('fetch_who_archive')
        rows = [mod.compact(v) for v in load_json('who_archive_page.json')['value']]
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r['id'] for r in rows}), 100)              # UrlName is the identity; DonId repeats across migrated notices
        self.assertTrue(all(r['id'] and r['title'] and r['date'] and r['url'].startswith('https://www.who.int/') for r in rows))
        self.assertGreater(sum(1 for r in rows if r['pathogen']), 80)
        self.assertGreater(sum(1 for r in rows if r['countries']), 70)
        self.assertTrue(all('summary' not in r and 'body' not in r for r in rows))


class MultilingualNewsTests(unittest.TestCase):
    def test_spanish_headlines_become_events(self):
        mod = importlib.import_module('fetch_gnews')
        root = ET.parse(fixture('gnews_es.xml')).getroot()
        rows = []
        for it in hf.rss_items(root):
            outlet = it['extra'].get('source') or ''
            rows.append({'title': mod.clean_title(it['title'], outlet), 'url': it['link'], 'domain': outlet, 'seendate': it['pubDate'],
                         '_q': ['brote sarampión'], '_lang': 'Spanish'})
        evs, out = mod.decode(rows, NOW)
        self.assertEqual(len(out), 12)
        self.assertTrue(all(r['language'] == 'Spanish' for r in out))
        self.assertTrue(evs and {e['pathogen'] for e in evs} & {'measles', 'dengue', 'cholera'})
        self.assertTrue(all('lang:spanish' in e['tags'] and e['lat'] is None and e['facts'][1] == ['Language', 'Spanish'] for e in evs))

    def test_aliases(self):
        cases = {'تفشي الكوليرا في السودان': 'cholera', '麻疹疫情爆发 北京': 'measles', 'вспышка кори в Москве': 'measles',
                 'Surto de dengue em São Paulo': 'dengue', 'Épidémie de choléra au Tchad': 'cholera', 'wabah campak di Jawa': 'measles',
                 'Gusano barrenador: nuevo caso en Nuevo León': 'screwworm', 'الحصبة الألمانية': 'rubella', '德国麻疹 病例': 'rubella',
                 'Crimean Congo haemorrhagic fever': 'cchf', 'Highly Pathogenic Avian Influenza (AI virus H5N1) suspect': 'h5n1'}
        for text, slug in cases.items():
            self.assertEqual(pathogens.primary(text)['slug'], slug, text)
        self.assertTrue(pathogens.is_outbreak_language('疫情 爆发') and pathogens.is_outbreak_language('brote de dengue'))
        self.assertTrue(pathogens.is_outbreak_language('тысячи случаев заболевания'))
        self.assertFalse(pathogens.is_outbreak_language('new hospital opens'))
