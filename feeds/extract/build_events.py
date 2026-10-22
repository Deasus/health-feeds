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

P4 hook: an LLM extraction pass over GDELT headlines (place + counts) slots in before step 2;
it is not wired yet (needs ANTHROPIC_API_KEY and the fixtures-as-tests described in PLAN §4.6).
"""
from collections import Counter, defaultdict
from datetime import timedelta
from hf import sibling_data, write_if_changed, envelope, parse_date, log, fail, now
import events, geocode

SOURCES = [
    ('who', 'don.json'), ('cdc', 'travel_notices.json'), ('cdc', 'nndss.json'), ('cdc', 'nwss.json'),
    ('travel', 'state.json'), ('ecdc', 'cdtr.json'), ('hdx', 'datasets.json'), ('fda', 'recalls.json'),
    ('reliefweb', 'disasters.json'), ('gdelt', 'articles.json'),
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
        key = (ev['pathogen'], ev['country_code'])
        index[key].add(ev['source'])
        stamps[key].append(parse_date(ev.get('updated')) or ref)
    for ev in kept:
        key = (ev['pathogen'], ev.get('country_code'))
        others = {s for s in index.get(key, set()) if s != ev['source']} if key[1] else set()
        recent = any(abs(((parse_date(ev.get('updated')) or ref) - t).days) <= CORROBORATION_DAYS for t in stamps.get(key, []))
        ev['corroboration'] = len(others) if recent else 0
        if ev['source_tier'] == 'news':
            if ev['corroboration'] >= 1 and ev.get('country_code'):
                c = geocode.country(ev['country_code'])
                if c:
                    ev['lat'], ev['lng'], ev['geo_precision'] = c['lat'], c['lng'], 'country'
                    ev['severity'] = max(ev['severity'], events.MINOR)
                    ev['severity_label'] = events.SEVERITY_LABEL[ev['severity']]
                    ev['tags'] = sorted(set(ev.get('tags', [])) | {'corroborated'})
            else:
                ev['lat'] = ev['lng'] = None
                ev['geo_precision'] = 'none'
        ev['cluster'] = events.dedupe_key(ev)
    kept.sort(key=lambda e: e.get('updated') or '', reverse=True)     # newest first …
    kept.sort(key=lambda e: -e['severity'])                              # … within severity, high to low (stable)
    return kept


def main():
    items, loaded, missing = load()
    if not items:
        fail(f'no sibling data at all (missing: {", ".join(missing)})')
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
