#!/usr/bin/env python3
"""CDC NWSS wastewater → data/nwss.json (state summaries + detection/trend events).

Upstream (verified 2026-10-22): per-sample Socrata datasets on data.cdc.gov — j9g8-acpt SARS-CoV-2,
ymmh-divb Influenza A, xpxn-rzgz Mpox, 45cq-cw4i RSV, akvg-8vrb Measles. Weekly (Fridays), ~9-day lag.
Rows with source = WastewaterSCAN are CC BY-NC 4.0 and are EXCLUDED in the query; the rest is public
domain. This feed computes its own trends from a 28-day window: per site, the mean log10
concentration of the last 7 days of data vs the 7–21 days before.
"""
import math
import os
from collections import defaultdict
from datetime import timedelta
from hf import get_json, write_if_changed, envelope, log, fail, HFError, now, parse_date
import events, geocode, pathogens

DATASETS = {
    'j9g8-acpt': 'covid19',
    'ymmh-divb': 'influenza',
    'xpxn-rzgz': 'mpox',
    '45cq-cw4i': 'rsv',
    'akvg-8vrb': 'measles',
}
CREDIT = 'CDC National Wastewater Surveillance System (NWSS)'
LICENCE = 'US Government work, public domain (WastewaterSCAN rows excluded: CC BY-NC 4.0)'
WINDOW_DAYS = 28
SELECT = 'site,state_territory,source,county_fips,counties_served,population_served,sample_collect_date,pcr_target,pcr_target_avg_conc,pcr_target_detect'


def headers():
    tok = os.environ.get('SOCRATA_APP_TOKEN')
    return {'X-App-Token': tok} if tok else {}


def fetch(dataset, since):
    url = (f'https://data.cdc.gov/resource/{dataset}.json?$select={SELECT}'
           f"&$where=sample_collect_date%20%3E=%20'{since}'%20AND%20source%20!=%20'WastewaterSCAN'&$limit=50000")
    return get_json(url, headers=headers(), timeout=180)


def summarize(rows):
    """Per-site trend + per-state summary from raw sample rows."""
    sites = defaultdict(list)
    for r in rows:
        if (r.get('source') or '') == 'WastewaterSCAN':
            continue
        d = parse_date(r.get('sample_collect_date'))
        if not d:
            continue
        try:
            conc = float(r['pcr_target_avg_conc']) if r.get('pcr_target_avg_conc') not in (None, '') else None
        except ValueError:
            conc = None
        sites[(r.get('state_territory') or '').upper(), r.get('site')].append(
            {'date': d, 'conc': conc, 'detect': (r.get('pcr_target_detect') or '').lower() == 'yes',
             'county': r.get('counties_served'), 'fips': r.get('county_fips'),
             'pop': int(float(r['population_served'])) if r.get('population_served') not in (None, '') else None})
    per_site = []
    for (st, site), samples in sites.items():
        samples.sort(key=lambda s: s['date'])
        latest = samples[-1]['date']
        recent = [s for s in samples if s['date'] > latest - timedelta(days=7)]
        prior = [s for s in samples if latest - timedelta(days=21) <= s['date'] <= latest - timedelta(days=7)]
        def mean_log(ss):
            vals = [math.log10(s['conc']) for s in ss if s['conc'] and s['conc'] > 0]
            return sum(vals) / len(vals) if vals else None
        a, b = mean_log(recent), mean_log(prior)
        delta = (a - b) if (a is not None and b is not None) else None
        trend = 'rising' if delta is not None and delta >= 0.3 else 'falling' if delta is not None and delta <= -0.3 else \
                'steady' if delta is not None else 'unknown'
        detect14 = any(s['detect'] for s in samples if s['date'] > latest - timedelta(days=14))
        per_site.append({'state': st, 'site': site, 'county': samples[-1]['county'], 'fips': samples[-1]['fips'],
                         'population': samples[-1]['pop'], 'latest': latest.strftime('%Y-%m-%d'), 'samples': len(samples),
                         'detect14': detect14, 'trend': trend, 'log10_delta': round(delta, 2) if delta is not None else None})
    by_state = defaultdict(lambda: {'sites': 0, 'detecting': 0, 'rising': 0, 'falling': 0, 'steady': 0, 'population': 0, 'latest': ''})
    for s in per_site:
        b = by_state[s['state']]
        b['sites'] += 1
        b['detecting'] += 1 if s['detect14'] else 0
        if s['trend'] in ('rising', 'falling', 'steady'):
            b[s['trend']] += 1
        b['population'] += s['population'] or 0
        b['latest'] = max(b['latest'], s['latest'])
    for st, b in by_state.items():
        b['detecting_pct'] = round(100 * b['detecting'] / b['sites']) if b['sites'] else None
        known = b['rising'] + b['falling'] + b['steady']
        b['rising_pct'] = round(100 * b['rising'] / known) if known else None
    return per_site, dict(by_state)


def to_events(slug, per_site, by_state, fetched_at):
    p = pathogens.BY_SLUG[slug]
    out = []
    if slug in ('measles', 'mpox'):
        for s in per_site:
            if not s['detect14']:
                continue
            st = geocode.state(s['state'], 'US')
            where = f"{s['county']} County, {s['state']}" if s['county'] else s['state']
            out.append(events.make(
                source='CDC NWSS', source_tier='official', kind='surveillance',
                severity=events.MODERATE if slug == 'measles' else events.MINOR,
                title=f'{p["name"]} detected in wastewater: {where}',
                headline=f'A sewershed serving about {s["population"]:,} people had a detection in the 14 days to {s["latest"]}.' if s['population']
                         else f'Detection in the 14 days to {s["latest"]}.',
                pathogen=p, country_code='US', admin1=st['name'] if st else s['state'], admin1_code=st['code'] if st else None,
                area=where, updated=parse_date(s['latest']), onset=parse_date(s['latest']), url='https://data.cdc.gov/d/' + [k for k, v in DATASETS.items() if v == slug][0],
                facts=[['Latest sample', s['latest']], ['County', s['county'] or '—'], ['Population served', f'{s["population"]:,}' if s['population'] else '—'],
                       ['Trend', s['trend']]], credit=CREDIT, licence=LICENCE, tags=['wastewater-detection'],
                source_id=f'{slug}|{s["state"]}|{s["site"]}|{s["latest"]}'))
    else:
        for st_code, b in by_state.items():
            if b['sites'] >= 3 and b['rising_pct'] is not None and b['rising_pct'] >= 50:
                st = geocode.state(st_code, 'US')
                out.append(events.make(
                    source='CDC NWSS', source_tier='official', kind='surveillance', severity=events.MINOR,
                    title=f'{p["name"]} rising in wastewater across {st["name"] if st else st_code}',
                    headline=f'{b["rising"]} of {b["sites"]} sewersheds show rising concentrations; latest samples {b["latest"]}.',
                    pathogen=p, country_code='US', admin1=st['name'] if st else st_code, admin1_code=st['code'] if st else None,
                    updated=parse_date(b['latest']), url='https://data.cdc.gov/d/' + [k for k, v in DATASETS.items() if v == slug][0],
                    facts=[['Sites', str(b['sites'])], ['Rising', f'{b["rising_pct"]}%'], ['Detecting', f'{b["detecting_pct"]}%'], ['Latest', b['latest']]],
                    credit=CREDIT, licence=LICENCE, tags=['wastewater-trend'], source_id=f'{slug}|{st_code}|{b["latest"]}|rising'))
    return out


def main():
    since = (now() - timedelta(days=WINDOW_DAYS)).strftime('%Y-%m-%d')
    items, summaries, failures = [], {}, []
    for dataset, slug in DATASETS.items():
        try:
            rows = fetch(dataset, since)
        except HFError as e:
            log(f'  {slug} ({dataset}): {e}')
            failures.append(slug)
            continue
        per_site, by_state = summarize(rows)
        summaries[slug] = by_state
        ev = to_events(slug, per_site, by_state, now())
        items.extend(ev)
        log(f'  {slug}: {len(rows)} samples, {len(per_site)} sites, {len(by_state)} states, {len(ev)} events')
    if len(failures) == len(DATASETS):
        fail('every NWSS dataset failed')
    write_if_changed('nwss.json', envelope(items, upstream='https://data.cdc.gov (NWSS per-sample datasets)', credit=CREDIT,
                                            licence=LICENCE, window_days=WINDOW_DAYS, since=since, failed=failures, summaries=summaries))


if __name__ == '__main__':
    main()
