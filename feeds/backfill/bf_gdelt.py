"""GDELT DOC 2.0, dated range → data/sources/gdelt/.

RUNS ONLY INSIDE THE PIPELINE (the one-off GitHub Actions job in this public repository). Never from a Mac, a browser, a
Worker or a test: PULSE's rules forbid any other GDELT caller (PULSE Web CLAUDE.md rule 5).

How it treats GDELT (README "Known sensitivities", and the live fetcher's own rules):
- Requests go out only in bursts that START at the fixed minute offsets GDELT_SLOT_MINUTES (default :05 and :35) and
  last at most GDELT_BURST_MINUTES (default 20). Between bursts it sends nothing.
- One request at a time, at least SPACING_S = 6 s after the previous answer (GDELT asks for one request per 5 s).
- On 429 (or GDELT's "limit requests" text) it waits 15, 30, then 60 s; after three in a row it ends the burst and waits
  for the next offset. Every answer is checkpointed, so nothing already fetched is lost: it keeps what it has.
- At most GDELT_MAX_REQUESTS (default 1,000) per leg, inside the leg's time budget. A later leg resumes.

What it asks:
- First a **probe** (PLAN EV "How far back M1 can go"): one 1-day query for theme:TAX_DISEASE_OUTBREAK at 85, 92, 100,
  120, 180, 365 and 730 days back, stopping at the first empty answer. The article search is documented as a rolling
  three months; the probe records what it actually reaches (manifest `probe`, `reach`).
- Then, once, GDELT's own theme list (LOOKUP-GKGTHEMES.TXT): a per-pathogen query uses its GKG theme when the theme
  exists there, and its keywords otherwise (the form is recorded per query).
- Then every query in QUERIES over [max(since, reach + 2 days), run start], in dated slices, OLDEST FIRST (the oldest
  days are the ones that leave GDELT's window), sort=dateasc, maxrecords=250. A slice that comes back full is split in
  two, down to MIN_SLICE (12 h); a full slice at that size is kept (its earliest 250 articles) and counted as capped.

Every article goes through fetch_gdelt.decode() (the live rules: a pathogen in the title, outbreak language for
flu/COVID/TB/malaria, countries from the title) and is dated by its own `seendate`. Coverage (manifest `queries[].intervals`)
is the union of the slices that were answered; a pathogen is covered by the queries that target it.
"""
import json
import os
import time
from datetime import timedelta
from urllib.parse import quote
from hf import get, HFError, parse_date
import fetch_gdelt
from bf_common import Budget, finalise, keep_earliest, load_state, save_state, load_items, save_items, merge_intervals, day_floor, iso, now, log

SLUG = 'gdelt'
SOURCE = 'GDELT'
API = 'https://api.gdeltproject.org/api/v2/doc/doc'
THEMES_LOOKUP = 'http://data.gdeltproject.org/api/v2/guides/LOOKUP-GKGTHEMES.TXT'
SLOT_MINUTES = tuple(sorted(int(x) for x in os.environ.get('GDELT_SLOT_MINUTES', '5,35').split(',') if x.strip()))
BURST_S = int(os.environ.get('GDELT_BURST_MINUTES', '20')) * 60
SPACING_S = max(6.0, float(os.environ.get('GDELT_SPACING_S', '6')))
MAX_REQUESTS = int(os.environ.get('GDELT_MAX_REQUESTS', '1000'))
USE_THEMES = os.environ.get('GDELT_THEMES', '1') == '1'
MAX_RECORDS = 250
MIN_SLICE = timedelta(hours=12)
MARGIN = timedelta(days=2)
PROBE_DAYS = (85, 92, 100, 120, 180, 365, 730)
PROBE_QUERY = 'theme:TAX_DISEASE_OUTBREAK sourcelang:english'
MAX_ATTEMPTS = 3
BACKOFF_429 = (15, 30, 60)
SAVE_EVERY = 10


class RateLimited(Exception):
    pass


# label, kind, targets (pathogens.py slugs), English keywords, GKG theme candidates (kept only if GDELT lists them),
# first slice in days, split when full. Every slug in pathogens.py except `unknown` is targeted by exactly one query.
QUERIES = [
    ('measles', 'pathogen', ['measles'], '(measles OR rubeola)', ['TAX_DISEASE_MEASLES'], 2, True),
    ('cholera', 'pathogen', ['cholera'], '(cholera OR "acute watery diarrhoea" OR "acute watery diarrhea")', ['TAX_DISEASE_CHOLERA'], 2, True),
    ('dengue', 'pathogen', ['dengue'], 'dengue', ['TAX_DISEASE_DENGUE', 'TAX_DISEASE_DENGUE_FEVER'], 2, True),
    ('mpox', 'pathogen', ['mpox'], '(mpox OR monkeypox)', ['TAX_DISEASE_MONKEYPOX', 'TAX_DISEASE_MPOX'], 2, True),
    ('avian-flu', 'pathogen', ['h5n1'], '("bird flu" OR "avian influenza" OR "avian flu" OR H5N1 OR H5N5 OR H7N9 OR H9N2)',
     ['TAX_DISEASE_AVIAN_INFLUENZA', 'TAX_DISEASE_BIRD_FLU', 'TAX_DISEASE_H5N1'], 2, True),
    ('ebola', 'pathogen', ['ebola'], '(ebola OR bundibugyo OR "sudan virus")', ['TAX_DISEASE_EBOLA'], 2, True),
    ('flu-covid-outbreaks', 'pathogen', ['influenza', 'covid19'],
     '("flu outbreak" OR "influenza outbreak" OR "covid outbreak" OR "covid-19 outbreak" OR "new covid variant")', [], 2, True),
    ('foodborne', 'pathogen', ['salmonella', 'listeria', 'ecoli', 'botulism', 'norovirus', 'cyclospora', 'campylobacter', 'shigella', 'cronobacter'],
     '(salmonella OR listeria OR "E. coli" OR botulism OR norovirus OR cyclospora OR campylobacter OR shigella OR cronobacter)', [], 2, True),
    ('polio', 'pathogen', ['polio'], '(polio OR poliovirus OR cVDPV2)', ['TAX_DISEASE_POLIO'], 7, True),
    ('vhf', 'pathogen', ['marburg', 'lassa', 'nipah', 'hendra', 'chapare'], '(marburg OR lassa OR nipah OR hendra OR chapare)', [], 7, True),
    ('zoonoses', 'pathogen', ['hantavirus', 'plague', 'anthrax', 'tularemia', 'glanders', 'melioidosis', 'brucellosis', 'q_fever'],
     '(hantavirus OR plague OR anthrax OR tularemia OR glanders OR melioidosis OR brucellosis OR "Q fever")', [], 7, True),
    ('arbo-aedes', 'pathogen', ['yellow_fever', 'chikungunya', 'zika', 'oropouche'], '("yellow fever" OR chikungunya OR zika OR oropouche)', [], 7, True),
    ('arbo-other', 'pathogen', ['west_nile', 'jev', 'eee', 'rvf', 'cchf'],
     '("west nile" OR "japanese encephalitis" OR "eastern equine encephalitis" OR "rift valley fever" OR "crimean-congo" OR CCHF)', [], 7, True),
    ('mers', 'pathogen', ['mers'], '(MERS OR "middle east respiratory syndrome")', ['TAX_DISEASE_MERS'], 7, True),
    ('diphtheria-pertussis', 'pathogen', ['diphtheria', 'pertussis'], '(diphtheria OR pertussis OR "whooping cough")', [], 7, True),
    ('meningitis', 'pathogen', ['meningitis'], '(meningitis OR meningococcal)', ['TAX_DISEASE_MENINGITIS'], 7, True),
    ('enteric-hepatitis', 'pathogen', ['typhoid', 'hepatitis_a', 'hepatitis_e', 'hepatitis_b'], '(typhoid OR "hepatitis A" OR "hepatitis E" OR "hepatitis B")', [], 7, True),
    ('bacterial-other', 'pathogen', ['legionella', 'candida_auris', 'strep_a', 'mycoplasma'],
     '(legionnaires OR legionella OR "candida auris" OR "group A strep" OR "scarlet fever" OR mycoplasma)', [], 7, True),
    ('animal-vector', 'pathogen', ['rabies', 'screwworm', 'leishmaniasis', 'leptospirosis'], '(rabies OR screwworm OR leishmaniasis OR leptospirosis)', [], 7, True),
    ('childhood', 'pathogen', ['mumps', 'rubella', 'varicella'], '(mumps OR rubella OR chickenpox OR varicella)', [], 7, True),
    ('endemic-outbreaks', 'pathogen', ['malaria', 'tb', 'rsv', 'rotavirus', 'scabies', 'vibrio'],
     '("malaria outbreak" OR "tuberculosis outbreak" OR "TB outbreak" OR "RSV outbreak" OR rotavirus OR "scabies outbreak" OR vibrio)', [], 7, True),
    ('outbreak-theme', 'generic', [], 'theme:TAX_DISEASE_OUTBREAK', [], 2, False),
    ('outbreak-theme-translingual', 'generic', [], 'theme:TAX_DISEASE_OUTBREAK', [], 2, False),
]


def gdelt_time(dt) -> str:
    return dt.strftime('%Y%m%d%H%M%S')


def query_text(q: dict) -> str:
    base = f"theme:{q['theme']}" if q.get('theme') else q['keywords']
    return base if q['label'].endswith('translingual') else base + ' sourcelang:english'


def url_for(text: str, a, b) -> str:
    return (f'{API}?query={quote(text)}&mode=artlist&maxrecords={MAX_RECORDS}&format=json'
            f'&startdatetime={gdelt_time(a)}&enddatetime={gdelt_time(b)}&sort=dateasc')


def fetch(url: str) -> dict:
    """One GET, no retry (the caller paces and backs off). {} when GDELT has nothing; RateLimited on 429 or its text."""
    try:
        raw = get(url, timeout=90, retries=0)
    except HFError as e:
        if '429' in str(e):
            raise RateLimited(str(e)) from e
        raise
    text = raw.decode('utf-8', 'replace').strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        if 'limit requests' in text.lower():
            raise RateLimited(text[:120])
        raise HFError(f'GDELT answered: {text[:120]}')


# ── the fixed minute offsets ────────────────────────────────────────────────────────────────────────────────────────

def burst_window(t):
    """(start, end) of the burst that contains t, or None when t is between bursts."""
    hour = t.replace(minute=0, second=0, microsecond=0)
    for h in (hour - timedelta(hours=1), hour):
        for m in SLOT_MINUTES:
            s = h + timedelta(minutes=m)
            if s <= t < s + timedelta(seconds=BURST_S):
                return s, s + timedelta(seconds=BURST_S)
    return None


def next_burst(t):
    hour = t.replace(minute=0, second=0, microsecond=0)
    starts = [h + timedelta(minutes=m) for h in (hour, hour + timedelta(hours=1)) for m in SLOT_MINUTES]
    return min(s for s in starts if s > t)


# ── the plan ────────────────────────────────────────────────────────────────────────────────────────────────────────

def make_queries(themes=None):
    out = []
    for label, kind, targets, keywords, candidates, days, split in QUERIES:
        theme = next((c for c in candidates if themes and c in themes), None) if USE_THEMES else None
        q = {'label': label, 'kind': kind, 'targets': targets, 'keywords': keywords, 'theme': theme,
             'form': 'theme' if theme or keywords.startswith('theme:') else 'keywords', 'slice_days': days, 'split': split}
        q['query'] = query_text(q)
        out.append(q)
    return out


def make_plan(queries, start, end):
    """Tasks oldest first: every query's slices, interleaved by slice start."""
    tasks = []
    for q in queries:
        a = start
        while a < end:
            b = min(a + timedelta(days=q['slice_days']), end)
            tasks.append({'q': q['label'], 'a': iso(a), 'b': iso(b), 'status': 'todo', 'attempts': 0})
            a = b
    tasks.sort(key=lambda t: (t['a'], t['q']))
    return tasks


def parse_themes(text: str) -> set:
    """LOOKUP-GKGTHEMES.TXT: one theme per line, its name first (tab-separated count after it)."""
    return {ln.split('\t')[0].strip() for ln in text.splitlines() if ln.strip()}


def run(since, until=None, budget=None, fetch_json=fetch, fetch_text=None, wall=now, sleep=time.sleep, max_requests=None):
    budget = budget or Budget(max_requests=max_requests if max_requests is not None else MAX_REQUESTS)
    st = load_state(SLUG, {'source': SOURCE, 'class': 'news', 'date_field': 'seendate', 'since': None, 'until': None, 'probe': None,
                           'reach': None, 'themes_checked': False, 'queries': None, 'tasks': None, 'requests': 0,
                           'rate_limited': 0, 'failures': 0, 'bursts': 0, 'notes': []})
    if st['since'] != iso(since):
        st.update(since=iso(since), until=None, probe=None, reach=None, themes_checked=False, queries=None, tasks=None,
                  requests=0, rate_limited=0, failures=0, bursts=0, notes=[])
        items = {}
    else:
        items = load_items(SLUG)
    until = parse_date(st['until']) if st['until'] else (until or wall())
    st['until'] = iso(until)
    st['requested'] = {'since': iso(since), 'until': iso(until)}
    st['slots'] = {'minutes': list(SLOT_MINUTES), 'burst_s': BURST_S, 'spacing_s': SPACING_S}
    fetch_text = fetch_text or (lambda url: get(url, timeout=90, retries=0).decode('utf-8', 'replace'))
    leg = {'requests': 0}

    def send(fn, url):
        budget.count()
        st['requests'] += 1
        leg['requests'] += 1
        try:
            return fn(url)
        finally:
            sleep(SPACING_S)

    def save():
        st['items'] = len(items)
        save_items(SLUG, items)
        save_state(SLUG, st)

    def wait_for_next_burst():
        sleep(max(1.0, (next_burst(wall()) - wall()).total_seconds()))

    def burst():
        """Send requests until this burst ends, the work is done, the budget is spent, or GDELT pushes back 3 times;
        then wait for the next offset (never re-enter the burst that just ended)."""
        win = burst_window(wall())
        if win is None or wall() >= win[1] - timedelta(seconds=30):
            wait_for_next_burst()
            return
        st['bursts'] += 1
        strikes = 0
        while wall() < win[1] - timedelta(seconds=30) and not budget.spent():
            step = next_step()
            if step is None:
                return
            kind, payload = step
            try:
                if kind == 'probe':
                    k = payload
                    end = day_floor(until) - timedelta(days=k - 1)
                    data = send(fetch_json, url_for(PROBE_QUERY, end - timedelta(days=1), end))
                    n = len(data.get('articles') or []) if isinstance(data, dict) else 0
                    st['probe'].append({'days_back': k, 'day': iso(end - timedelta(days=1))[:10], 'articles': n})
                    st['probe_errors'] = 0
                elif kind == 'themes':
                    themes = parse_themes(send(fetch_text, THEMES_LOOKUP))
                    st['queries'] = make_queries(themes)
                    st['themes_checked'] = True
                    st['notes'].append(f'GKG theme list read: {len(themes)} themes; per-pathogen queries using a theme: '
                                       f'{[q["label"] for q in st["queries"] if q["theme"]]}')
                else:
                    run_task(payload)
                strikes = 0
            except RateLimited as e:
                strikes += 1
                st['rate_limited'] += 1
                if strikes >= len(BACKOFF_429):
                    st['notes'].append(f'burst ended after {strikes} rate limits: {str(e)[:80]}')
                    break
                sleep(BACKOFF_429[strikes - 1])
            except HFError as e:
                st['failures'] += 1
                if kind == 'probe':
                    st['probe_errors'] = st.get('probe_errors', 0) + 1
                    if st['probe_errors'] >= MAX_ATTEMPTS:
                        st['probe'].append({'days_back': payload, 'error': str(e)[:120]})
                elif kind == 'themes':
                    st['queries'] = make_queries(None)
                    st['themes_checked'] = True
                    st['notes'].append(f'GKG theme list unavailable ({str(e)[:80]}): keywords for every query')
                else:
                    payload['attempts'] += 1
                    if payload['attempts'] >= MAX_ATTEMPTS:
                        payload['status'] = 'failed'
                        payload['error'] = str(e)[:120]
            if leg['requests'] % SAVE_EVERY == 0:
                save()
        save()
        if not budget.spent() and next_step() is not None:
            wait_for_next_burst()

    def next_step():
        if st['probe'] is None:
            st['probe'] = []
        done_probe = any(p.get('articles') == 0 or 'error' in p for p in st['probe']) or len(st['probe']) >= len(PROBE_DAYS)
        if not done_probe:
            return 'probe', PROBE_DAYS[len(st['probe'])]
        if st['reach'] is None:
            hits = [p['days_back'] for p in st['probe'] if p.get('articles')]
            if not hits:
                st['reach'] = 'none'
                st['notes'].append('probe found no articles at any depth: GDELT unavailable for a backfill now')
                st['tasks'] = []
                return None
            st['reach'] = iso(day_floor(until) - timedelta(days=max(hits)))
        if st['reach'] == 'none':
            return None
        if not st['themes_checked']:
            if USE_THEMES:
                return 'themes', None
            st['queries'] = make_queries(None)
            st['themes_checked'] = True
        if st['tasks'] is None:
            start = max(since, parse_date(st['reach']) + MARGIN)
            st['plan_start'] = iso(start)
            st['tasks'] = make_plan(st['queries'], start, until)
            st['planned_slices'] = len(st['tasks'])
        task = next((t for t in st['tasks'] if t['status'] == 'todo'), None)
        return ('task', task) if task else None

    def run_task(t):
        q = next(x for x in st['queries'] if x['label'] == t['q'])
        a, b = parse_date(t['a']), parse_date(t['b'])
        data = send(fetch_json, url_for(q['query'], a, b))
        arts = (data.get('articles') if isinstance(data, dict) else None) or []
        t['articles'] = len(arts)
        if len(arts) >= MAX_RECORDS and q['split'] and b - a > MIN_SLICE:
            mid = a + (b - a) / 2
            i = st['tasks'].index(t)
            st['tasks'][i:i + 1] = [{'q': t['q'], 'a': iso(a), 'b': iso(mid), 'status': 'todo', 'attempts': 0},
                                    {'q': t['q'], 'a': iso(mid), 'b': iso(b), 'status': 'todo', 'attempts': 0}]
            return
        t['status'] = 'capped' if len(arts) >= MAX_RECORDS else 'ok'
        for x in arts:
            x['_q'] = [q['label']]
        evs, _rows = fetch_gdelt.decode(arts, wall())
        for ev in evs:
            keep_earliest(items, finalise(ev, source_date=ev['updated'], field='seendate', key=ev['url']))

    while not budget.spent():
        if next_step() is None:
            break
        burst()
    # manifest section
    tasks = st['tasks'] or []
    for q in st['queries'] or []:
        mine = [t for t in tasks if t['q'] == q['label']]
        q['intervals'] = merge_intervals([[t['a'], t['b']] for t in mine if t['status'] in ('ok', 'capped')])
        q['slices'] = {s: sum(1 for t in mine if t['status'] == s) for s in ('ok', 'capped', 'failed', 'todo')}
    todo = sum(1 for t in tasks if t['status'] == 'todo')
    failed = sum(1 for t in tasks if t['status'] == 'failed')
    st['status'] = ('failed' if st['reach'] == 'none' else 'complete' if st['tasks'] is not None and not todo and not failed
                    else 'partial')
    st['intervals'] = []                      # GDELT coverage is per query: queries[].intervals
    st['resumable'] = st['reach'] != 'none' and (st['tasks'] is None or todo > 0)
    st['requests_this_leg'] = leg['requests']
    save()
    log(f'GDELT backfill: reach {st["reach"]}, {len(tasks)} slices ({todo} to do, {failed} failed), {len(items)} items, '
        f'{leg["requests"]} requests this leg, {st["rate_limited"]} rate-limited, {st["status"]}')
    return st, items


__all__ = ['run', 'url_for', 'burst_window', 'next_burst', 'make_queries', 'make_plan', 'parse_themes', 'QUERIES', 'SLUG', 'SOURCE', 'RateLimited']
