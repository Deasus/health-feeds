#!/usr/bin/env python3
"""GDELT DOC 2.0 health news → data/articles.json.

Upstream (verified 2026-10-22): api.gdeltproject.org/api/v2/doc/doc. The server asks for one request
every 5 seconds; this fetcher spaces queries 6 s apart and waits 15 s once on a 429. GKG theme filters
work (theme:TAX_DISEASE_*). GEO 2.0 is dead, so articles carry no geography beyond the publisher's
country; countries and pathogens are read from the title. Headlines are outlet-owned: title, domain
and URL only. News events stay unmapped (lat/lng null, INFO) until build_events.py finds a
non-news source for the same pathogen and country.

On a complete failure the previous file is kept (exit 1). On a partial failure the successful
queries are published and the failed ones listed in `failed`.
"""
import os
import re
import time
from datetime import timedelta
from urllib.parse import quote
from hf import get_json, read_json, write_if_changed, envelope, parse_date, log, fail, HFError, now, DATA
import events, geocode, pathogens

CREDIT = 'GDELT Project (headlines © their publishers)'
LICENCE = 'GDELT terms: free use with attribution and a link; headlines remain the publishers’'
# Verified 2026-10-22: a single theme or one keyword OR-group answers in 14–18 s; OR-ing several themes
# is refused as a "larger query" (429) even when spaced out. So: simple queries, three per run, rotating,
# with a rolling 48-hour merge of article rows. Each query runs once an hour (the feed runs every 30 min).
QUERIES = [
    ('theme:TAX_DISEASE_OUTBREAK', 'outbreak-theme'),
    ('(measles OR cholera OR mpox OR monkeypox OR polio OR diphtheria OR meningitis)', 'vaccine-preventable'),
    ('(ebola OR marburg OR "bird flu" OR H5N1 OR "avian influenza" OR hantavirus OR nipah OR lassa OR plague)', 'zoonotic'),
    ('theme:TAX_DISEASE_EPIDEMIC', 'epidemic-theme'),
    ('(dengue OR chikungunya OR "yellow fever" OR salmonella OR listeria OR "E. coli" OR "health emergency" OR "disease outbreak")', 'other'),
    ('theme:HEALTH_PANDEMIC', 'pandemic-theme'),
    ('theme:TAX_DISEASE_OUTBREAK', 'outbreak-theme-translingual'),      # no English filter: other languages for the LLM stage
]
QUERIES_PER_RUN = 3
SPACING_S = 12
KEEP_HOURS = 48
MAX_ARTICLES = 800


def url_for(q: str, label: str = '') -> str:
    lang = '' if label.endswith('translingual') else ' sourcelang:english'
    return (f'https://api.gdeltproject.org/api/v2/doc/doc?query={quote(q + lang)}'
            f'&mode=artlist&maxrecords=250&format=json&timespan=24h&sort=datedesc')


def clean_title(t: str) -> str:
    t = re.sub(r'\s+', ' ', t or '').strip()
    return re.sub(r'\s+([?!.,:;])', r'\1', t)


def decode(articles, fetched_at):
    """articles (deduped dicts) → (events, compact article rows)."""
    evs, rows = [], []
    for a in articles:
        title = clean_title(a.get('title'))
        url = a.get('url')
        if not title or not url:
            continue
        seen = parse_date(a.get('seendate')) or fetched_at
        rows.append({'title': title, 'url': url, 'domain': a.get('domain'), 'language': a.get('language'),
                     'sourcecountry': a.get('sourcecountry'), 'seendate': seen.strftime('%Y-%m-%dT%H:%M:%SZ'), 'queries': a.get('_q', [])})
        pathogen = pathogens.primary(title)
        if not pathogen:
            continue
        if not (pathogens.is_outbreak_language(title) or pathogen['slug'] not in ('influenza', 'covid19', 'tb', 'malaria')):
            continue                                # generic flu/covid/TB/malaria chatter needs outbreak language
        countries = geocode.find_countries(title)
        c = countries[0] if countries else None
        evs.append(events.make(
            source='GDELT', source_tier='news', kind='news', severity=events.INFO, title=title, headline=None, body=None,
            pathogen=pathogen, country_code=c['iso2'] if c else None, countries=[x['iso2'] for x in countries],
            lat=None, lng=None, geo_precision='none', updated=seen, url=url,
            facts=[['Outlet', a.get('domain') or '—'], ['Publisher country', a.get('sourcecountry') or '—']],
            credit=f'{a.get("domain")} via GDELT', licence=LICENCE, tags=a.get('_q', []), source_id=url))
    return evs, rows


def main():
    previous = read_json(os.path.join(DATA, 'articles.json')) or {}
    slot = int(previous.get('next_slot', 0)) % len(QUERIES)
    chosen = [QUERIES[(slot + i) % len(QUERIES)] for i in range(QUERIES_PER_RUN)]
    cutoff = now() - timedelta(hours=KEEP_HOURS)
    # rolling merge: keep the previous run's rows that are still inside the window
    merged = {}
    for row in previous.get('articles', []):
        seen = parse_date(row.get('seendate'))
        if seen and seen >= cutoff and row.get('url'):
            merged[row['url']] = {'url': row['url'], 'title': row.get('title'), 'seendate': row.get('seendate'),
                                  'domain': row.get('domain'), 'language': row.get('language'),
                                  'sourcecountry': row.get('sourcecountry'), '_q': list(row.get('queries', []))}
    failed, ok = [], 0
    for i, (q, label) in enumerate(chosen):
        if i:
            time.sleep(SPACING_S)
        t0 = time.time()
        try:
            data = get_json(url_for(q, label), timeout=60, retries=1, backoff=20, rate_limit_sleep=30)
            ok += 1
        except HFError as e:
            log(f'  {label}: {e} after {time.time() - t0:.1f}s')
            failed.append(label)
            continue
        arts = data.get('articles', []) if isinstance(data, dict) else []
        log(f'  {label}: {len(arts)} articles in {time.time() - t0:.1f}s')
        for a in arts:
            key = a.get('url')
            if not key:
                continue
            if key in merged:
                if label not in merged[key]['_q']:
                    merged[key]['_q'].append(label)
            else:
                a['_q'] = [label]
                merged[key] = a
    if ok == 0 and not merged:
        fail('every GDELT query failed and there is no previous file')
    rows_in = sorted(merged.values(), key=lambda a: a.get('seendate') or '', reverse=True)[:MAX_ARTICLES]
    # rows carry GDELT's raw seendate on a fresh fetch and our normalised one after a merge; parse_date reads both
    evs, rows = decode(rows_in, now())
    log(f'GDELT: slot {slot} ran {[l for _, l in chosen]}; {len(rows)} articles in the last {KEEP_HOURS} h, '
        f'{len(evs)} with a pathogen in the title, failed {failed}')
    write_if_changed('articles.json', envelope(evs, upstream='https://api.gdeltproject.org/api/v2/doc/doc', credit=CREDIT, licence=LICENCE,
                                                failed=failed, ran=[l for _, l in chosen], next_slot=(slot + QUERIES_PER_RUN) % len(QUERIES),
                                                keep_hours=KEEP_HOURS, queries=[q for q, _ in QUERIES], articles=rows),
                     volatile=('generated_at', 'next_slot', 'ran', 'failed'))


if __name__ == '__main__':
    main()
