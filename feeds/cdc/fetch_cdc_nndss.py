#!/usr/bin/env python3
"""CDC NNDSS weekly provisional counts → data/nndss.json.

Upstream (verified 2026-10-22): Socrata dataset x9gk-5huc on data.cdc.gov. Columns: states (state,
territory, region or aggregate), year, week, label (condition), m1 (current week), m2 (previous
52-week maximum), m3 (cumulative YTD), m4 (cumulative previous year), geocode (point, states only).
Public domain. Provisional: counts change as reports arrive.

Events: a state × condition row is a "surveillance" event when this week's count is a new 52-week
high (m1 >= 3 and m1 > m2). Chronic-hepatitis labels are skipped (reporting artefacts, not outbreaks).
The full state table for the week (rows with m1 > 0) ships alongside for the app's tables.
"""
import os
from hf import get_json, write_if_changed, envelope, log, fail, HFError, now
import events, geocode, pathogens

BASE = 'https://data.cdc.gov/resource/x9gk-5huc.json'
PAGE = 'https://data.cdc.gov/d/x9gk-5huc'
CREDIT = 'CDC National Notifiable Diseases Surveillance System (NNDSS), weekly provisional data'
LICENCE = 'US Government work, public domain'
PRIORITY = {'measles', 'polio', 'cholera', 'h5n1', 'ebola', 'marburg', 'mpox', 'diphtheria', 'plague', 'anthrax', 'botulism',
            'meningitis', 'pertussis', 'typhoid', 'dengue', 'malaria', 'yellow_fever', 'rabies', 'hantavirus', 'tularemia', 'legionella'}
AGGREGATES = {'Total', 'U.S. Residents', 'Non-U.S. Residents', 'U.S. Territories', 'New England', 'Middle Atlantic', 'East North Central',
              'West North Central', 'South Atlantic', 'East South Central', 'West South Central', 'Mountain', 'Pacific'}


def headers():
    tok = os.environ.get('SOCRATA_APP_TOKEN')
    return {'X-App-Token': tok} if tok else {}


def latest_week():
    rows = get_json(f'{BASE}?$select=year,week&$order=year%20DESC,week%20DESC&$limit=1', headers=headers(), timeout=60)
    if not rows:
        raise HFError('NNDSS: no rows')
    return rows[0]['year'], int(float(rows[0]['week']))


def fetch_week(year, week):
    url = (f"{BASE}?$select=states,label,m1,m2,m3,m4,geocode&$where=year='{year}'%20AND%20week={week}&$limit=50000")
    return get_json(url, headers=headers(), timeout=120)


def place(name):
    """(country_code, admin1, admin1_code) for an NNDSS location name, or None for aggregates/regions."""
    if name in AGGREGATES:
        return None
    if name == 'New York City':
        return ('US', 'New York City', 'NY')
    s = geocode.state(name, 'US')
    if s:
        return ('US', s['name'], s['code'])
    c = geocode.country(name)             # Puerto Rico, Guam, American Samoa, U.S. Virgin Islands, CNMI
    if c:
        return (c['iso2'], None, None)
    return None


def decode(rows, year, week, fetched_at):
    table, items = [], []
    for r in rows:
        if 'm1' not in r:
            continue
        m1 = float(r['m1'])
        if m1 <= 0:
            continue
        m2 = float(r['m2']) if r.get('m2') not in (None, '') else None
        m3 = float(r['m3']) if r.get('m3') not in (None, '') else None
        m4 = float(r['m4']) if r.get('m4') not in (None, '') else None
        label = r.get('label') or ''
        loc = r.get('states') or ''
        geo = (r.get('geocode') or {}).get('coordinates')
        table.append({'location': loc, 'condition': label, 'week': int(m1), 'max52': m2, 'ytd': m3, 'prev_ytd': m4})
        if 'chronic' in label.lower():
            continue
        p = place(loc)
        if p is None:
            continue
        if not (m1 >= 3 and (m2 is None or m1 > m2)):
            continue
        pathogen = pathogens.primary(label)
        slug = pathogen['slug'] if pathogen else None
        severity = events.MODERATE if (m1 >= 10 or slug in PRIORITY) else events.MINOR
        cc, admin1, code = p
        where = admin1 or geocode.country(cc)['name']
        title = f'{label.split(",")[0]}: {int(m1)} this week in {where}'
        headline = (f'{int(m1)} reported in week {week} of {year}; the previous 52-week weekly maximum was '
                    f'{int(m2) if m2 is not None else 0}.')
        facts = [['This week', str(int(m1))], ['52-week max', str(int(m2)) if m2 is not None else '—'],
                 ['Year to date', str(int(m3)) if m3 is not None else '—'], ['Same point last year', str(int(m4)) if m4 is not None else '—'],
                 ['Week', f'{year}-W{week:02d}']]
        items.append(events.make(
            source='CDC NNDSS', source_tier='official', kind='surveillance', severity=severity, title=title, headline=headline,
            pathogen=pathogen, country_code=cc, admin1=admin1, admin1_code=code,
            lat=geo[1] if geo else None, lng=geo[0] if geo else None, geo_precision='admin1' if geo or code else 'country',
            cases=int(m1), updated=fetched_at, url=PAGE, facts=facts, credit=CREDIT, licence=LICENCE,
            tags=['new-52-week-high'], source_id=f'{year}-W{week}|{loc}|{label}'))
    return items, table


def main():
    try:
        year, week = latest_week()
        rows = fetch_week(year, week)
    except HFError as e:
        fail(str(e))
    items, table = decode(rows, year, week, now())
    if not table:
        fail('NNDSS week decoded to zero rows')
    log(f'NNDSS {year}-W{week:02d}: {len(rows)} rows, {len(table)} with counts, {len(items)} new 52-week highs')
    write_if_changed('nndss.json', envelope(items, upstream=BASE, credit=CREDIT, licence=LICENCE,
                                             week=f'{year}-W{week:02d}', table=table))


if __name__ == '__main__':
    main()
