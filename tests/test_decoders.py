"""Decoder tests on captured fixtures (no network). Run: python3 -m unittest discover -s tests -q"""
import importlib
import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDS = os.path.join(ROOT, 'feeds')
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
for d in ['_shared', 'who', 'cdc', 'travel', 'ecdc', 'hdx', 'gdelt', 'fda', 'reliefweb', 'extract']:
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
