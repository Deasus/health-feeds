"""Haiku extraction over news headlines (PLAN §4.6): the pipeline's curator.

For each headline the model returns the canonical pathogen (or null), the country (ISO2 or null), a city or place
name when one is stated, case/death counts ONLY when stated verbatim (with the quoted span), and whether the item is
outbreak news at all. Results are cached by headline hash for 72 hours in data/llm_cache.json so a headline is paid for
once. Runs only when ANTHROPIC_API_KEY is set; otherwise the merge proceeds without it. Cost guard: at most
LLM_MAX_PER_RUN new headlines per run (default 300), batches of 25. Haiku 5.5 thinks by default and can spend the
whole budget thinking, so thinking is disabled (CINDER's finding).
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta
from hf import log, now, iso, parse_date, read_json, DATA
import pathogens

MODEL = os.environ.get('LLM_MODEL', 'claude-haiku-5-5')
MAX_PER_RUN = int(os.environ.get('LLM_MAX_PER_RUN', '300'))
BATCH = 25
CACHE_HOURS = 72
CACHE_FILE = os.path.join(DATA, 'llm_cache.json')
SLUGS = [p[0] for p in pathogens.PATHOGENS]

SYSTEM = (
    "You extract structured facts from news headlines about infectious disease and public health. Answer with JSON only: "
    "an array with one object per headline, in order, with keys: i (the headline number), outbreak (true if the headline "
    "reports or updates a disease outbreak, cluster, case, recall for contamination, or public-health alert; false for "
    "opinion, history, fiction, business, politics, vaccines in general, misinformation stories), pathogen (one slug from the "
    "list, or null), country (ISO 3166-1 alpha-2 of the place where the cases are, or null; never the outlet's country), "
    "place (a city, town, district or state named in the headline where the cases are, or null), cases (integer only if the "
    "headline states a case count, else null), deaths (integer only if stated, else null), quote (the exact words the counts "
    "came from, or null). Headlines may be in any language; answer in the schema regardless. Never guess counts or places."
)


def _key(title):
    return hashlib.sha1((title or '').strip().lower().encode('utf-8')).hexdigest()[:20]


def _call(api_key, headlines):
    body = {
        'model': MODEL, 'max_tokens': 2500, 'thinking': {'type': 'disabled'}, 'system': SYSTEM,     # no temperature: deprecated on Haiku 5.5
        'messages': [{'role': 'user', 'content': 'Pathogen slugs: ' + ', '.join(SLUGS) + '\n\nHeadlines:\n'
                      + '\n'.join(f'{h["i"]}. {h["title"]}' for h in headlines) + '\n\nJSON array:'}],
    }
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=json.dumps(body).encode(), method='POST',
                                 headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
    text = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M).strip()
    arr = json.loads(text)
    usage = data.get('usage', {})
    return arr, usage


def _validate(item):
    out = {'outbreak': bool(item.get('outbreak')), 'pathogen': None, 'country': None, 'place': None, 'cases': None, 'deaths': None, 'quote': None}
    slug = item.get('pathogen')
    if isinstance(slug, str) and slug in SLUGS:
        out['pathogen'] = slug
    cc = item.get('country')
    if isinstance(cc, str) and re.fullmatch(r'[A-Za-z]{2}', cc):
        out['country'] = cc.upper()
    place = item.get('place')
    if isinstance(place, str) and 2 <= len(place.strip()) <= 60:
        out['place'] = place.strip()
    for k in ('cases', 'deaths'):
        v = item.get(k)
        if isinstance(v, int) and 0 <= v < 10_000_000 and item.get('quote'):
            out[k] = v
    q = item.get('quote')
    if isinstance(q, str) and (out['cases'] is not None or out['deaths'] is not None):
        out['quote'] = q[:160]
    return out


def enrich(titles, api_key=None, call=None):
    """titles: [str] → {title: result}. Uses the cache; calls the model (or `call`, for tests) for the rest."""
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    cache = read_json(CACHE_FILE, {}) or {}
    cutoff = now() - timedelta(hours=CACHE_HOURS)
    cache = {k: v for k, v in cache.items() if parse_date(v.get('ts')) and parse_date(v['ts']) >= cutoff}
    results, todo = {}, []
    for t in titles:
        k = _key(t)
        if k in cache:
            results[t] = cache[k]['r']
        elif t.strip():
            todo.append(t)
    todo = todo[:MAX_PER_RUN]
    if todo and not (api_key or call):
        log(f'  llm: {len(todo)} headlines need extraction but ANTHROPIC_API_KEY is not set; skipping')
        todo = []
    spent_in = spent_out = 0
    for b in range(0, len(todo), BATCH):
        batch = [{'i': n + 1, 'title': t} for n, t in enumerate(todo[b:b + BATCH])]
        try:
            arr, usage = (call or (lambda hs: _call(api_key, hs)))(batch)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            log(f'  llm batch failed: {str(e)[:160]}')
            time.sleep(5)
            continue
        spent_in += usage.get('input_tokens', 0); spent_out += usage.get('output_tokens', 0)
        by_i = {int(x.get('i', 0)): x for x in arr if isinstance(x, dict)}
        for h in batch:
            r = _validate(by_i.get(h['i'], {}))
            results[h['title']] = r
            cache[_key(h['title'])] = {'ts': iso(now()), 'r': r}
    if todo:
        log(f'  llm: {len(todo)} headlines extracted, ~{spent_in} in / {spent_out} out tokens')
    os.makedirs(DATA, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
    return results
