#!/usr/bin/env python3
"""Google News RSS health headlines in eight languages → data/articles.json (same shape as gdelt/articles.json).

Upstream: https://news.google.com/rss/search?q=<query>&hl=<lang>&gl=<country>&ceid=<country>:<lang> (verified 2026-10-22
for English, 2026-10-30 for es, fr, pt, ar, zh, ru and id: 40–100 items per query). geo-feeds' news pipeline has used
this feed for months. Headlines are outlet-owned: title, outlet and the Google link (provenance only). Queries run 2 s
apart; a rolling 48-hour merge keeps the previous run's rows; a failed run keeps the previous file. A headline becomes an
event when pathogens.py recognises a pathogen in its own language; the Haiku curator (build_events.py) then confirms it is
outbreak news and fills the country, so non-English events need no English country name in the title. News events stay
unmapped until build_events.py corroborates them. HealthMap reads fifteen languages; this is eight (PLAN §11).
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
# language, hl, gl, ceid, queries
EDITIONS = [
    ('English', 'en-US', 'US', 'US:en',
     ['measles outbreak', 'cholera outbreak', 'mpox cases', 'ebola OR marburg', '"bird flu" OR H5N1 OR "avian influenza"',
      'dengue outbreak', 'disease outbreak', 'health emergency outbreak', 'polio case', 'meningitis outbreak',
      'salmonella OR listeria recall', 'hantavirus', '"yellow fever"', '"whooping cough" OR pertussis', 'rabies exposure',
      'norovirus outbreak', 'legionnaires', 'malaria outbreak', 'chikungunya', 'plague case']),
    ('Spanish', 'es-419', 'MX', 'MX:es-419',
     ['brote sarampión', 'brote cólera', 'casos dengue', 'brote enfermedad', 'gripe aviar', 'fiebre amarilla OR tos ferina']),
    ('French', 'fr', 'FR', 'FR:fr',
     ['épidémie choléra', 'épidémie rougeole', 'Ebola OR Marburg cas', 'épidémie', 'grippe aviaire', 'mpox OR dengue épidémie']),
    ('Portuguese', 'pt-BR', 'BR', 'BR:pt-419',
     ['surto sarampo', 'surto dengue', 'surto cólera', 'surto doença', 'gripe aviária', 'febre amarela OR chikungunya']),
    ('Arabic', 'ar', 'EG', 'EG:ar',
     ['تفشي الكوليرا', 'تفشي الحصبة', 'حمى الضنك', 'تفشي وباء', 'إنفلونزا الطيور', 'شلل الأطفال OR جدري القردة']),
    ('Chinese', 'zh-CN', 'CN', 'CN:zh-Hans',
     ['疫情 爆发', '霍乱 疫情', '麻疹 病例', '禽流感', '登革热 病例', '猴痘 OR 埃博拉']),
    ('Russian', 'ru', 'RU', 'RU:ru',
     ['вспышка кори', 'вспышка холеры', 'эпидемия', 'птичий грипп', 'лихорадка денге', 'сибирская язва OR бешенство']),
    ('Indonesian', 'id', 'ID', 'ID:id',
     ['wabah campak', 'wabah kolera', 'kasus demam berdarah', 'wabah penyakit', 'flu burung', 'cacar monyet OR rabies']),
]
SPACING_S = 2
KEEP_HOURS = 48
MAX_ARTICLES = 2000
UA = 'Mozilla/5.0 (compatible; health-feeds/1.0; +https://github.com/Deasus/health-feeds)'


def url_for(q, hl='en-US', gl='US', ceid='US:en'):
    return f'https://news.google.com/rss/search?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}'


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
        lang = a.get('_lang') or a.get('language') or 'English'
        out.append({'title': title, 'url': url, 'domain': a.get('domain'), 'language': lang, 'sourcecountry': None,
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
            updated=seen, url=url, facts=[['Outlet', a.get('domain') or '—'], ['Language', lang]], credit=f'{a.get("domain")} via Google News',
            licence=LICENCE, tags=a.get('_q', []) + ([] if lang == 'English' else [f'lang:{lang.lower()}']), source_id=url))
    return evs, out


def main():
    previous = read_json(os.path.join(DATA, 'articles.json')) or {}
    cutoff = now() - timedelta(hours=KEEP_HOURS)
    merged = {}
    for row in previous.get('articles', []):
        seen = parse_date(row.get('seendate'))
        if seen and seen >= cutoff and row.get('url'):
            merged[row['url']] = {'title': row['title'], 'url': row['url'], 'domain': row.get('domain'), 'seendate': row['seendate'],
                                  '_q': list(row.get('queries', [])), '_lang': row.get('language') or 'English'}
    ok, failed = 0, []
    first = True
    for lang, hl, gl, ceid, queries in EDITIONS:
        per_lang = 0
        for q in queries:
            if not first:
                time.sleep(SPACING_S)
            first = False
            try:
                root = get_xml(url_for(q, hl, gl, ceid), timeout=30, retries=1, ua=UA)
                ok += 1
            except HFError as e:
                log(f'  {lang} · {q}: {e}')
                failed.append(f'{lang}: {q}')
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
                                   'seendate': (d or now()).strftime('%Y-%m-%dT%H:%M:%SZ'), '_q': [q], '_lang': lang}
            per_lang += n
        log(f'  {lang}: {per_lang} items in window from {len(queries)} queries')
    if ok == 0 and not merged:
        fail('every Google News query failed and there is no previous file')
    rows = sorted(merged.values(), key=lambda a: a.get('seendate') or '', reverse=True)[:MAX_ARTICLES]
    evs, out = decode(rows, now())
    by_lang = {}
    for r in out:
        by_lang[r['language']] = by_lang.get(r['language'], 0) + 1
    log(f'Google News: {len(out)} articles in {KEEP_HOURS} h {by_lang}, {len(evs)} with a pathogen in the title, failed {len(failed)}')
    write_if_changed('articles.json', envelope(evs, upstream='https://news.google.com/rss/search', credit=CREDIT, licence=LICENCE,
                                                failed=failed, keep_hours=KEEP_HOURS,
                                                editions=[{'language': e[0], 'hl': e[1], 'gl': e[2], 'queries': e[4]} for e in EDITIONS],
                                                articles_by_language=by_lang, articles=out),
                     volatile=('generated_at', 'failed'))


if __name__ == '__main__':
    main()
