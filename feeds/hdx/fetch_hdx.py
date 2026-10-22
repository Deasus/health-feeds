#!/usr/bin/env python3
"""HDX (Humanitarian Data Exchange) outbreak datasets → data/datasets.json.

Upstream (verified 2026-10-22): CKAN package_search on data.humdata.org. Title search per disease
("q=title:cholera") is far cleaner than free text. Each dataset: title, organization, groups (ISO3
country codes), metadata_modified, license_title. Licence is per dataset and carried on each event.
A recently modified outbreak dataset is a signal that a response is live; events are INFO.
"""
import re
import time
from datetime import timedelta
from hf import get_json, write_if_changed, envelope, parse_date, log, fail, HFError, now, first_sentence
import events, geocode, pathogens

BASE = 'https://data.humdata.org/api/3/action/package_search'
CREDIT = 'OCHA Humanitarian Data Exchange (HDX); dataset credit per item'
DISEASES = ['cholera', 'ebola', 'mpox', 'measles', 'marburg', 'dengue', 'polio', 'meningitis', 'yellow fever', 'lassa',
            'malaria', 'diphtheria', 'hepatitis', 'plague', 'anthrax', 'chikungunya', 'avian influenza', 'outbreak']
MAX_AGE_DAYS = 120
EXCLUDE = re.compile(r'vaccin|immuni[sz]ation|coverage|survey|\bKAP\b|indicator|population|census|boundar|admin', re.I)


def decode(results, since) -> list:
    out = []
    for p in results:
        modified = parse_date(p.get('metadata_modified'))
        if not modified or modified < since:
            continue
        title = p.get('title') or p.get('name') or ''
        if EXCLUDE.search(title):
            continue
        pathogen = pathogens.primary(title) or pathogens.primary(p.get('notes') or '')
        iso3s = [g.get('name', '').upper() for g in p.get('groups', [])]
        countries = [c for c in (geocode.country(i) for i in iso3s) if c]
        org = (p.get('organization') or {}).get('title') or p.get('dataset_source') or 'HDX contributor'
        licence = p.get('license_title') or 'see dataset'
        url = f"https://data.humdata.org/dataset/{p.get('name')}"
        freq = p.get('data_update_frequency')
        facts = [['Organization', org], ['Updated', modified.strftime('%d %b %Y')], ['Licence', licence],
                 ['Countries', str(len(countries))]]
        targets = countries if 0 < len(countries) <= 3 else [None]
        for c in targets:
            out.append(events.make(
                source='HDX', source_tier='aggregator', kind='surveillance', severity=events.INFO, title=title,
                headline=first_sentence(events.clip(p.get('notes') or '', 400)) if p.get('notes') else None,
                pathogen=pathogen, country_code=c['iso2'] if c else None, countries=[x['iso2'] for x in countries],
                updated=modified, url=url, facts=facts, credit=org, licence=licence,
                tags=['dataset'] + ([f'freq:{freq}'] if freq is not None else []),
                source_id=f"{p.get('id')}|{c['iso2'] if c else ''}"))
    return out


def main():
    since = now() - timedelta(days=MAX_AGE_DAYS)
    seen, items, ok = set(), [], 0
    for d in DISEASES:
        url = f'{BASE}?q=title:{d.replace(" ", "%20")}&rows=15&sort=metadata_modified%20desc'
        try:
            res = get_json(url, timeout=40)
            ok += 1
        except HFError as e:
            log(f'  {d}: {e}')
            continue
        for ev in decode(res.get('result', {}).get('results', []), since):
            if ev['id'] not in seen:
                seen.add(ev['id'])
                items.append(ev)
        time.sleep(0.5)
    if ok == 0:
        fail('every HDX query failed')
    log(f'HDX: {len(items)} dataset events from {ok} queries')
    write_if_changed('datasets.json', envelope(items, upstream=BASE, credit=CREDIT, licence='per dataset', max_age_days=MAX_AGE_DAYS))


if __name__ == '__main__':
    main()
