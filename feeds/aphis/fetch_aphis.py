#!/usr/bin/env python3
"""USDA APHIS HPAI detections in wild birds and mammals → data/hpai.json (one event per county in the window).

Upstream (verified 2026-10-30; US Government work, public domain):
  https://www.aphis.usda.gov/sites/default/files/hpai-wild-birds.csv   State, County, Collection Date, Date Detected, HPAI Strain,
      Bird Species, WOAH Classification, Sampling Method, Submitting Agency   (~20,000 rows since Jan 2022, current to the week)
  https://www.aphis.usda.gov/sites/default/files/hpai-mammals.csv      State, County, Date Collected, Date Detected, HPAI Strain, Species
aphis.usda.gov serves these files to the pipeline's own User-Agent and 403s browser-like ones (Akamai), so hf.UA stays as it is.
The poultry-flock and livestock tables are rendered in the browser only (the CSVs behind them stopped in June 2025 and June
2024), so poultry and dairy outbreaks come in through WOAH WAHIS instead (the US notifies them, with coordinates).

Counties are placed with the Census gazetteer (geocode.county); detections are aggregated per county over WINDOW_DAYS so a
busy flyway county is one event, not fifty. Tagged `animal` + `wildlife`/`mammal`; animal counts live in facts, not `cases`.
"""
import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hf import get_text, write_if_changed, envelope, log, fail, HFError, now
import events, geocode

URLS = {'wild_birds': 'https://www.aphis.usda.gov/sites/default/files/hpai-wild-birds.csv',
        'mammals': 'https://www.aphis.usda.gov/sites/default/files/hpai-mammals.csv'}
CREDIT = 'USDA Animal and Plant Health Inspection Service (APHIS), HPAI detections'
LICENCE = 'US Government work, public domain'
WINDOW_DAYS = 45
DOMESTIC = re.compile(r'\b(cat|dog|swine|pig|cattle|cow|goat|sheep|alpaca|horse|ferret)s?\b', re.I)   # closer to people than a fox


def _date(s):
    s = (s or '').strip()
    for fmt in ('%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def rows_of(text: str) -> list:
    return list(csv.DictReader(io.StringIO(text.lstrip('﻿'))))


def decode(layer: str, rows: list, ref) -> list:
    cutoff = ref - timedelta(days=WINDOW_DAYS)
    groups = defaultdict(list)
    for r in rows:
        d = _date(r.get('Date Detected'))
        if not d or d < cutoff or d > ref + timedelta(days=2):
            continue
        groups[((r.get('State') or '').strip(), (r.get('County') or '').strip())].append((d, r))
    out = []
    for (state_name, county_name), items in groups.items():
        st = geocode.state(state_name, 'US')
        if not st:
            continue
        items.sort(key=lambda x: x[0])
        first, last = items[0][0], items[-1][0]
        species = Counter((r.get('Bird Species') or r.get('Species') or '').strip() for _, r in items)
        strains = sorted({(r.get('HPAI Strain') or '').strip() for _, r in items} - {''})
        n = len(items)
        c = geocode.county(county_name, st['code'])
        area = f"{c['name'] if c else county_name + ' County'}, {st['code']}"
        top = ', '.join(f'{s} ({k})' if k > 1 else s for s, k in species.most_common(3))
        plural = 's' if n != 1 else ''
        if layer == 'wild_birds':
            title = f'HPAI in wild birds: {area}'
            headline = f'{n} detection{plural} since {first.strftime("%d %b")}; {top}.'
            severity, tags = events.MINOR, ['animal', 'wildlife', 'wild-bird']
        else:
            domestic = any(DOMESTIC.search(s) for s in species)
            title = f'HPAI in mammals: {area}'
            headline = f'{n} detection{plural} since {first.strftime("%d %b")}: {top}.'
            severity = events.MODERATE if domestic else events.MINOR
            tags = ['animal', 'mammal', 'domestic' if domestic else 'wildlife']
        facts = [['Detections', str(n)], ['Latest detection', last.strftime('%d %b %Y')], ['Strain', ', '.join(strains) or '—'],
                 ['County', c['name'] if c else county_name], ['State', st['name']]]
        facts += [['Species', f'{s}: {k}'] for s, k in species.most_common(5)]
        if layer == 'wild_birds':
            methods = Counter((r.get('Sampling Method') or '').strip() for _, r in items)
            facts.append(['Sampling', ', '.join(f'{m} ({k})' for m, k in methods.most_common(3) if m)])
        ev = events.make(source='USDA APHIS', source_tier='official', kind='surveillance', severity=severity, title=title, headline=headline,
                         pathogen='h5n1', country_code='US', admin1=st['name'], admin1_code=st['code'],
                         lat=c['lat'] if c else None, lng=c['lng'] if c else None, geo_precision='point' if c else 'admin1', area=area,
                         updated=last, onset=first, url=URLS[layer], facts=facts, credit=CREDIT, licence=LICENCE, tags=tags,
                         source_id=f'{layer}|{st["code"]}|{county_name}')
        if c:
            ev['geo_radius_km'] = c['radius_km']
        out.append(ev)
    out.sort(key=lambda e: e['updated'] or '', reverse=True)
    return out


def main():
    ref = now()
    items, counts = [], {}
    for layer, url in URLS.items():
        try:
            rows = rows_of(get_text(url, timeout=90))
        except HFError as e:
            fail(f'{layer}: {e}')
        evs = decode(layer, rows, ref)
        counts[layer] = {'rows': len(rows), 'counties_in_window': len(evs),
                         'detections_in_window': sum(int(e['facts'][0][1]) for e in evs)}
        items.extend(evs)
        log(f'  {layer}: {len(rows)} rows, {len(evs)} counties with detections in {WINDOW_DAYS} days')
    if not items and all(c['rows'] for c in counts.values()):
        log('APHIS: no detections in the window (quiet season)')
    by_state = Counter()
    for e in items:
        by_state[e['admin1_code']] += int(e['facts'][0][1])
    write_if_changed('hpai.json', envelope(items, upstream=' · '.join(URLS.values()), credit=CREDIT, licence=LICENCE, window_days=WINDOW_DAYS,
                                           layers=counts, detections_by_state=dict(by_state.most_common())))


if __name__ == '__main__':
    main()
