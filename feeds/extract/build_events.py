#!/usr/bin/env python3
"""Merge every feed into data/events.json, the one file the app reads for the map, board and SITREP.

Reads each sibling feed's output (../<feed>/data/<file> on the same runner, else the published
branch), then:
  1. keeps events updated within WINDOW_DAYS (WHO DONs can be months old; the map wants recent);
  2. counts corroboration: distinct non-news sources naming the same pathogen in the same country;
  3. applies the news rule: a GDELT headline is mapped (country centroid) and lifted to MINOR only
     when at least one non-news source corroborates it; otherwise it stays INFO and unmapped;
  4. assigns a cluster key (pathogen | country | ISO week) so the app can collapse duplicates;
  5. sorts by severity, then recency.

Before step 2 the curator pass runs (llm_extract.py, Haiku, cached 72 h, only with ANTHROPIC_API_KEY): headlines
that are not outbreak news are dropped, pathogen/country/counts are filled where stated, and a stated city becomes a
point (gazetteer.py, GeoNames) once the item is corroborated. Official country-level events whose text names a city
are placed at that city too.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from hf import sibling_data, write_if_changed, envelope, parse_date, log, fail, now
import events, geocode, gazetteer, pathogens
import llm_extract

SOURCES = [
    ('who', 'don.json'), ('cdc', 'travel_notices.json'), ('cdc', 'nndss.json'), ('cdc', 'nwss.json'),
    ('travel', 'state.json'), ('ecdc', 'cdtr.json'), ('hdx', 'datasets.json'), ('fda', 'recalls.json'),
    ('reliefweb', 'disasters.json'), ('gdelt', 'articles.json'), ('gnews', 'articles.json'),
    ('paho', 'alerts.json'), ('wahis', 'events.json'), ('aphis', 'hpai.json'), ('whispers', 'events.json'),
]
WINDOW_DAYS = 45
CORROBORATION_DAYS = 21


def load():
    items, loaded, missing = [], [], []
    for feed, name in SOURCES:
        d = sibling_data(feed, name)
        if not d:
            missing.append(f'{feed}/{name}')
            continue
        loaded.append({'feed': feed, 'file': name, 'generated_at': d.get('generated_at'), 'count': d.get('count', len(d.get('items', [])))})
        items.extend(d.get('items', []))
    return items, loaded, missing


def enrich_news(items, ref, call=None):
    """The curator pass (PLAN §4.6): Haiku reads every news headline once; the result fills pathogen, country and a
    city-level point (via the gazetteer) where the headline states them, and drops items that are not outbreak news."""
    news = [e for e in items if e['source_tier'] == 'news']
    if not news:
        return items
    results = llm_extract.enrich([e['title'] for e in news], call=call)
    kept = []
    dropped = filled = placed = 0
    for e in items:
        if e['source_tier'] != 'news':
            kept.append(e)
            continue
        r = results.get(e['title'])
        if r is None:
            kept.append(e)
            continue
        if not r['outbreak']:
            dropped += 1
            continue
        if r['pathogen'] and e['pathogen'] == 'unknown':
            p = pathogens.BY_SLUG[r['pathogen']]
            e['pathogen'], e['pathogen_name'], e['pathogen_class'] = p['slug'], p['name'], p['cls']
            filled += 1
        if r['country'] and not e.get('country_code') and geocode.country(r['country']):
            e['country_code'] = r['country']
            e['countries'] = [r['country']]
            e['region'] = geocode.area_of(r['country'])
            e['area'] = geocode.country(r['country'])['name']
            filled += 1
        if r['place']:
            hit = gazetteer.city(r['place'], e.get('country_code')) or (gazetteer.find_places(r['place'], e.get('country_code')) or [None])[0]
            if hit and (not e.get('country_code') or hit['country'] == e['country_code']):
                e['_city'] = hit                              # applied only once corroborated (merge step)
                placed += 1
            elif e.get('country_code') in ('US', 'CA', 'AU'):
                st = geocode.state(r['place'], e['country_code'])   # "Pennsylvania", "Ontario": a state, not a city
                if st:
                    e['admin1'], e['admin1_code'] = st['name'], st['code']
                    e['_state'] = st
                    placed += 1
        if r['cases'] is not None and e.get('cases') is None:
            e['cases'], e['deaths'], e['cases_quote'] = r['cases'], r['deaths'], r['quote']
        e['tags'] = sorted(set(e.get('tags', [])) | {'llm-extracted'})
        kept.append(e)
    log(f'  curator: {len(news)} headlines, {dropped} not outbreak news, {filled} fields filled, {placed} city places found')
    return kept


def upgrade_official_places(items):
    """Official country-level events whose text names a city get a city point (WHO DON summaries name the district)."""
    n = 0
    for e in items:
        if (e['source_tier'] == 'official' and e.get('kind') in ('outbreak', 'alert') and e.get('geo_precision') == 'country'
                and e.get('country_code') and (e.get('body') or e.get('headline'))):   # DONs name districts; advisories name embassies
            hits = gazetteer.find_places(f"{e.get('headline') or ''} {e.get('body') or ''}", e['country_code'], limit=1)
            if hits:
                h = hits[0]
                e['lat'], e['lng'], e['geo_precision'], e['geo_radius_km'] = h['lat'], h['lng'], 'point', gazetteer.CITY_RADIUS_KM
                e['area'] = f"{h['name']}, {e.get('area') or geocode.country(e['country_code'])['name']}"
                e['tags'] = sorted(set(e.get('tags', [])) | {'city-from-text'})
                n += 1
    if n:
        log(f'  places: {n} official events placed at a named city')
    return items


def merge(items, ref):
    cutoff = ref - timedelta(days=WINDOW_DAYS)
    kept = []
    for ev in items:
        when = parse_date(ev.get('updated'))
        if when and when < cutoff:
            continue
        kept.append(ev)
    # corroboration index over non-news events
    index = defaultdict(set)          # (pathogen, country) -> {source}
    stamps = defaultdict(list)
    for ev in kept:
        if ev['source_tier'] == 'news' or ev['pathogen'] == 'unknown' or not ev.get('country_code'):
            continue
        if 'animal' in (ev.get('tags') or []) and ev['pathogen'] != 'h5n1':
            continue            # an animal rabies or anthrax report is its own layer; only HPAI in animals corroborates "bird flu" headlines
        key = (ev['pathogen'], ev['country_code'])
        index[key].add(ev['source'])
        stamps[key].append(parse_date(ev.get('updated')) or ref)
    for ev in kept:
        key = (ev['pathogen'], ev.get('country_code'))
        others = {s for s in index.get(key, set()) if s != ev['source']} if key[1] else set()
        recent = any(abs(((parse_date(ev.get('updated')) or ref) - t).days) <= CORROBORATION_DAYS for t in stamps.get(key, []))
        ev['corroboration'] = len(others) if recent else 0
        if ev['source_tier'] == 'news':
            city = ev.pop('_city', None)
            state = ev.pop('_state', None)
            if ev['corroboration'] >= 1 and ev.get('country_code'):
                c = geocode.country(ev['country_code'])
                if city:
                    ev['lat'], ev['lng'], ev['geo_precision'], ev['geo_radius_km'] = city['lat'], city['lng'], 'point', gazetteer.CITY_RADIUS_KM
                    ev['area'] = f"{city['name']}, {c['name'] if c else ev['country_code']}"
                elif state:
                    ev['lat'], ev['lng'], ev['geo_precision'], ev['geo_radius_km'] = state['lat'], state['lng'], 'admin1', geocode.radius_km(state['bbox'])
                    ev['area'] = f"{state['name']}, {c['name'] if c else ev['country_code']}"
                elif c:
                    ev['lat'], ev['lng'], ev['geo_precision'] = c['lat'], c['lng'], 'country'
                    ev['geo_radius_km'] = geocode.radius_km(c.get('bbox'))
                if c:
                    ev['severity'] = max(ev['severity'], events.MINOR)
                    ev['severity_label'] = events.SEVERITY_LABEL[ev['severity']]
                    ev['tags'] = sorted(set(ev.get('tags', [])) | {'corroborated'})
            else:
                ev['lat'] = ev['lng'] = None
                ev['geo_precision'] = 'none'
                ev['geo_radius_km'] = None
        ev['cluster'] = events.dedupe_key(ev)
    kept.sort(key=lambda e: e.get('updated') or '', reverse=True)     # newest first …
    kept.sort(key=lambda e: -e['severity'])                              # … within severity, high to low (stable)
    return kept


def main():
    items, loaded, missing = load()
    if not items:
        fail(f'no sibling data at all (missing: {", ".join(missing)})')
    items = enrich_news(items, now())
    items = upgrade_official_places(items)
    merged = merge(items, now())
    by_source = Counter(e['source'] for e in merged)
    by_tier = Counter(e['source_tier'] for e in merged)
    by_sev = Counter(e['severity_label'] for e in merged)
    mapped = sum(1 for e in merged if e.get('lat') is not None)
    log(f'events: {len(merged)} in the last {WINDOW_DAYS} days ({mapped} mapped); sources {dict(by_source)}; missing {missing}')
    write_if_changed('events.json', envelope(
        merged, upstream='health-feeds (every feed in this repository)', credit='see each event', licence='see each event',
        window_days=WINDOW_DAYS, mapped=mapped, by_source=dict(by_source), by_tier=dict(by_tier), by_severity=dict(by_sev),
        feeds=loaded, missing=missing))


if __name__ == '__main__':
    main()
