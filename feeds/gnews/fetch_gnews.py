#!/usr/bin/env python3
"""Google News RSS health headlines → data/articles.json (same shape as gdelt/articles.json).

Upstream: https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en (verified 2026-10-22: 100 items per
query). geo-feeds' news pipeline has used this feed for months. Headlines are outlet-owned: title, outlet and the
Google link (provenance only). Queries run 2 s apart; a rolling 48-hour merge keeps the previous run's rows; a
failed run keeps the previous file. News events stay unmapped until build_events.py corroborates them.
"""
import os
import re
import time
from datetime import timedelta
from urllib.parse import quote
from hf import get_xml, rss_items, read_json, write_if_changed, envelope, parse_date, log, fail, HFError, now, DATA
import events, geocode, pathogens

CREDIT = 'Google News RSS (headlines © their publishers)'
LICENCE = 'Headlines remain the publishers’; Google News RSS used for discovery only'
QUERIES = ['measles outbreak', 'cholera outbreak', 'mpox cases', 'ebola OR marburg', '"bird flu" OR H5N1 OR "avian influenza"',
           'dengue outbreak', 'disease outbreak', 'health emergency outbreak', 'polio case', 'meningitis outbreak',
           'salmonella OR listeria recall', 'hantavirus', '"yellow fever"', '"whooping cough" OR pertussis', 'rabies exposure',
           'norovirus outbreak', 'legionnaires', 'malaria outbreak', 'chikungunya', 'plague case']
SPACING_S = 2
KEEP_HOURS = 48
MAX_ARTICLES = 800


def url_for(q):
    return f'https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en'


def clean_title(t, outlet):
    t = re.sub(r'\s+', ' ', t or '').strip()
    if outlet and t.endswith(' - ' + outlet):
        t = t[: -len(outlet) - 3].rstrip()
    return t


def decode(rows, fetched_at):
    evs, out = [], []
    for a in rows:
        title, url = a.get('title'), a.get('url')
        if not title or not url:
            continue
        seen = parse_date(a.get('seendate')) or fetched_at
        out.append({'title': title, 'url': url, 'domain': a.get('domain'), 'language': 'English', 'sourcecountry': None,
                    'seendate': seen.strftime('%Y-%m-%dT%H:%M:%SZ'), 'queries': a.get('_q', [])})
        pathogen = pathogens.primary(title)
        if not pathogen:
            continue
        if not (pathogens.is_outbreak_language(title) or pathogen['slug'] not in ('influenza', 'covid19', 'tb', 'malaria')):
            continue
        countries = geocode.find_countries(title)
        c = countries[0] if countries else None
        evs.append(events.make(
            source='Google News', source_tier='news', kind='news', severity=events.INFO, title=title, pathogen=pathogen,
            country_code=c['iso2'] if c else None, countries=[x['iso2'] for x in countries], lat=None, lng=None, geo_precision='none',
            updated=seen, url=url, facts=[['Outlet', a.get('domain') or '—']], credit=f'{a.get("domain")} via Google News',
            licence=LICENCE, tags=a.get('_q', []), source_id=url))
    return evs, out


def main():
    previous = read_json(os.path.join(DATA, 'articles.json')) or {}
    cutoff = now() - timedelta(hours=KEEP_HOURS)
    merged = {}
    for row in previous.get('articles', []):
        seen = parse_date(row.get('seendate'))
        if seen and seen >= cutoff and row.get('url'):
            merged[row['url']] = {'title': row['title'], 'url': row['url'], 'domain': row.get('domain'), 'seendate': row['seendate'],
                                  '_q': list(row.get('queries', []))}
    ok, failed = 0, []
    for i, q in enumerate(QUERIES):
        if i:
            time.sleep(SPACING_S)
        try:
            root = get_xml(url_for(q), timeout=30, retries=1, ua='Mozilla/5.0 (compatible; health-feeds/1.0; +https://github.com/Deasus/health-feeds)')
            ok += 1
        except HFError as e:
            log(f'  {q}: {e}')
            failed.append(q)
            continue
        n = 0
        for it in rss_items(root):
            outlet = it['extra'].get('source') or ''
            url = it['link'] or it['guid']
            if not url:
                continue
            title = clean_title(it['title'], outlet)
            d = parse_date(it['pubDate'])
            if d and d < cutoff:
                continue
            n += 1
            if url in merged:
                if q not in merged[url]['_q']:
                    merged[url]['_q'].append(q)
            else:
                merged[url] = {'title': title, 'url': url, 'domain': outlet or None,
                               'seendate': (d or now()).strftime('%Y-%m-%dT%H:%M:%SZ'), '_q': [q]}
        log(f'  {q}: {n} items in window')
    if ok == 0 and not merged:
        fail('every Google News query failed and there is no previous file')
    rows = sorted(merged.values(), key=lambda a: a.get('seendate') or '', reverse=True)[:MAX_ARTICLES]
    evs, out = decode(rows, now())
    log(f'Google News: {len(out)} articles in {KEEP_HOURS} h, {len(evs)} with a pathogen in the title, failed {failed}')
    write_if_changed('articles.json', envelope(evs, upstream='https://news.google.com/rss/search', credit=CREDIT, licence=LICENCE,
                                                failed=failed, keep_hours=KEEP_HOURS, queries=QUERIES, articles=out),
                     volatile=('generated_at', 'failed'))


if __name__ == '__main__':
    main()
