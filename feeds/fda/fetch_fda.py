#!/usr/bin/env python3
"""openFDA food enforcement reports → data/recalls.json.

Upstream (verified 2026-10-22): https://api.fda.gov/food/enforcement.json — weekly; fields include
classification (Class I/II/III), reason_for_recall, city, state, country, recall_initiation_date,
report_date, recalling_firm, product_description, distribution_pattern. 1,000 requests/day per IP
without a key, 120,000 with OPENFDA_API_KEY. Public domain.

Only recalls with a pathogen (or microbial contamination language) become events: allergen
labelling recalls are not health-intel. Class I + pathogen → MODERATE; Class I → MINOR; else INFO.
"""
import os
import re
from hf import get_json, write_if_changed, envelope, parse_date, log, fail, HFError, now, first_sentence
import events, geocode, pathogens

BASE = 'https://api.fda.gov/food/enforcement.json'
CREDIT = 'US FDA, Enforcement Reports (openFDA)'
LICENCE = 'US Government work, public domain'
MICROBIAL = re.compile(r'contaminat|bacteri|pathogen|microbial|mold|mould|toxin|clostridium|staphylococc|hepatitis', re.I)


def fetch(skip):
    key = os.environ.get('OPENFDA_API_KEY')
    url = f'{BASE}?search=status:%22Ongoing%22&limit=100&skip={skip}&sort=report_date:desc'
    if key:
        url += f'&api_key={key}'
    return get_json(url, timeout=40)


def decode(results, fetched_at) -> list:
    out = []
    for r in results:
        reason = r.get('reason_for_recall') or ''
        pathogen = pathogens.primary(reason)
        cls = r.get('classification') or ''
        if not pathogen and not (cls == 'Class I' and MICROBIAL.search(reason)):
            continue
        state = geocode.state(r.get('state') or '', 'US') if (r.get('country') or 'United States') == 'United States' else None
        country = 'US' if (r.get('country') or 'United States') == 'United States' else (geocode.find_countries(r.get('country') or '') or [{}])[0].get('iso2')
        sev = events.MODERATE if (cls == 'Class I' and pathogen) else events.MINOR if cls == 'Class I' else events.INFO
        initiated = parse_date(r.get('recall_initiation_date'))
        reported = parse_date(r.get('report_date'))
        product = re.sub(r'\s+', ' ', r.get('product_description') or '')[:160]
        firm = r.get('recalling_firm') or 'Unnamed firm'
        title = f'{(pathogen or {}).get("name", "Contamination")} recall: {product[:70]}{"…" if len(product) > 70 else ""}'
        out.append(events.make(
            source='FDA recalls', source_tier='official', kind='recall', severity=sev, title=title,
            headline=f'{firm} ({r.get("city") or ""}{", " if r.get("city") and r.get("state") else ""}{r.get("state") or ""}): {first_sentence(reason, 200)}',
            body=events.clip(f'{product}\n{reason}\nDistribution: {r.get("distribution_pattern") or "—"}', 600),
            pathogen=pathogen or pathogens.BY_SLUG['unknown'], country_code=country, admin1=state['name'] if state else None,
            admin1_code=state['code'] if state else None, area=f'{r.get("city")}, {r.get("state")}' if r.get('city') and r.get('state') else None,
            updated=reported or fetched_at, onset=initiated, url=f'https://www.accessdata.fda.gov/scripts/ires/index.cfm?Product={r.get("recall_number") or ""}',
            facts=[['Class', cls or '—'], ['Firm', firm], ['Initiated', initiated.strftime('%d %b %Y') if initiated else '—'],
                   ['Reported', reported.strftime('%d %b %Y') if reported else '—'], ['Distribution', (r.get('distribution_pattern') or '—')[:120]]],
            credit=CREDIT, licence=LICENCE, tags=['food-recall'], source_id=r.get('recall_number') or r.get('event_id')))
    return out


def main():
    results = []
    try:
        for skip in (0, 100):
            d = fetch(skip)
            results.extend(d.get('results', []))
            if len(d.get('results', [])) < 100:
                break
    except HFError as e:
        if not results:
            fail(str(e))
        log(f'  second page failed ({e}); continuing with {len(results)} records')
    items = decode(results, now())
    log(f'openFDA: {len(results)} ongoing recalls, {len(items)} with a pathogen or microbial contamination')
    write_if_changed('recalls.json', envelope(items, upstream=BASE, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
