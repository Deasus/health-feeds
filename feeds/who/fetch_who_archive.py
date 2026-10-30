#!/usr/bin/env python3
"""WHO Disease Outbreak News, the whole archive → data/archive.json (one compact row per DON, 2004 → today).

The same API as fetch_who.py, 100 at a time ($top is capped at 100; $select trims the payload), walked with a date
cursor rather than $skip: past ~1,100 rows $skip returns the same page again when publication dates tie (2026-10-30),
so each page asks for `$filter=PublicationDate le <oldest date seen>` and new rows are the progress signal. Rows are
keyed by UrlName (the item slug): DonId is not unique, migrated notices share one, so it is kept only for display:
  https://www.who.int/api/news/diseaseoutbreaknews?$orderby=PublicationDate desc&$top=100
      &$select=Id,Title,PublicationDate,UrlName,DonId,Summary[&$filter=PublicationDate le 2015-02-13T00:00:00Z]
3,201 DONs on 2026-10-30 → ~35 requests once, a second apart; after that one request per run picks up new DONs (the
walk stops at the first page with nothing new). Each row keeps the title, date, link, pathogen, countries and the
counts stated in the summary, not the summary itself (WHO terms, PLAN §4.7). Not merged into events.json: this is
history for the Lab, for SITREP baselines and for "when was the last DON on X in Y" (HealthMap's archive goes back
to 2006; the DON API to 2004, with ~1,000 older notices carrying the 2021 date of WHO's site migration).
"""
import os
import time
from collections import Counter
from hf import get_json, read_json, write_if_changed, envelope, parse_date, strip_html, iso, log, fail, HFError, DATA
import events, geocode, pathogens

URL = ('https://www.who.int/api/news/diseaseoutbreaknews?$orderby=PublicationDate%20desc&$top=100'
       '&$select=Id,Title,PublicationDate,UrlName,DonId,Summary')
ITEM_URL = 'https://www.who.int/emergencies/disease-outbreak-news/item/{}'
CREDIT = 'World Health Organization, Disease Outbreak News (archive)'
LICENCE = 'WHO terms of use (public-health use, attribution; no commercial promotion)'
PAGE = 100
MAX_PAGES = 80
SPACING_S = 1.0


def compact(v: dict) -> dict:
    title = (v.get('Title') or '').strip()
    summary = strip_html(v.get('Summary') or '')
    p = pathogens.primary(title) or pathogens.primary(summary)
    countries = geocode.find_countries(title) or geocode.find_countries(summary[:400])
    cases, deaths, _ = events.counts_from_text(summary)
    d = parse_date(v.get('PublicationDate'))
    return {'id': v.get('UrlName') or v.get('Id'), 'don': v.get('DonId'), 'title': title, 'date': iso(d) if d else None,
            'url': ITEM_URL.format(v.get('UrlName') or ''),
            'pathogen': p['slug'] if p else None, 'countries': [c['iso2'] for c in countries], 'cases': cases, 'deaths': deaths}


def main():
    prev = read_json(os.path.join(DATA, 'archive.json')) or {}
    known = {r['id']: r for r in prev.get('items', []) if r.get('id')}
    full = not prev.get('complete')
    rows, pages, ended_short = [], 0, False
    cursor, op, seen = None, 'le', set()
    for page in range(MAX_PAGES):
        url = URL + (f'&$filter=PublicationDate%20{op}%20{cursor}' if cursor else '')
        try:
            d = get_json(url, timeout=40)
        except HFError as e:
            if rows or known:
                log(f'  page {page}: {e}')
                break
            fail(str(e))
        vals = d.get('value') or []
        pages += 1
        new = [compact(v) for v in vals]
        fresh = [r for r in new if r['id'] and r['id'] not in seen]
        seen.update(r['id'] for r in fresh)
        rows.extend(fresh)
        if not vals or len(vals) < PAGE:
            ended_short = True
            break
        if not full and all(r['id'] in known for r in new):
            break
        dates = sorted(r['date'] for r in new if r['date'])
        if not dates:
            break
        oldest = dates[0]
        if not fresh:                       # a whole page of one date already seen: step past that date
            if op == 'lt':
                break
            op = 'lt'
        else:
            op = 'le'
        cursor = oldest
        time.sleep(SPACING_S)
    for r in rows:
        if r.get('id'):
            known[r['id']] = r
    items = sorted(known.values(), key=lambda r: r.get('date') or '', reverse=True)
    if not items:
        fail('WHO DON archive decoded to zero items')
    complete = bool(prev.get('complete')) or ended_short
    by_year = Counter((r.get('date') or '')[:4] for r in items)
    log(f'WHO DON archive: {len(items)} notices ({pages} pages read this run, {len(rows)} rows), complete={complete}')
    write_if_changed('archive.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE, complete=complete,
                                              first=items[-1].get('date'), last=items[0].get('date'), by_year=dict(sorted(by_year.items()))))


if __name__ == '__main__':
    main()
