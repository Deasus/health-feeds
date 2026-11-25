"""PAHO Epidemiological Alerts and Updates, dated range → data/sources/paho/.

The live fetcher (fetch_paho.py) reads page 0 of PAHO's Drupal list (18 items, back to 2026-02-18 on 2026-10-30). The
list pages with `?page=N`, newest first; page 3 held 2024-07-17 → 2024-11-18 on 2026-11-14. This walks pages until the
oldest item on a page is older than `since`, decodes each page with fetch_paho.decode() (title, date, pathogen,
countries; no PAHO text reproduced) and dates every item by the list's own <time datetime> (Drupal's posting date).
About 5 requests, one per SPACING_S. Coverage: [since, run time] once a page older than `since` is reached; if the walk
fails at page k, [oldest date on page k−1, run time].
"""
import time
from hf import get_text, HFError, parse_date
import fetch_paho
from bf_common import Budget, finalise, keep_earliest, load_state, save_state, load_items, save_items, add_interval, sleep_s, iso, now, log

SLUG = 'paho'
SOURCE = 'PAHO'
SPACING_S = 2.0
MAX_PAGES = 40


def page_url(n: int) -> str:
    return fetch_paho.URL + (f'?page={n}' if n else '')


def page_dates(page: str):
    return [d for d in (parse_date(m.group(1)) for m in fetch_paho.ITEM.finditer(page)) if d]


def run(since, until=None, budget=None, get=get_text, sleep=time.sleep):
    until = until or now()
    budget = budget or Budget()
    st = load_state(SLUG, {'source': SOURCE, 'class': 'official', 'date_field': 'list <time datetime> (posting date)'})
    st.update(requested={'since': iso(since), 'until': iso(until)}, intervals=[], requests=0, failures=0, pages=0, notes=[])
    items = {}
    oldest_ok, complete = None, False
    for n in range(MAX_PAGES):
        if budget.spent():
            st['notes'].append(f'budget spent before page {n}')
            break
        if n:
            sleep_s(SPACING_S, sleep)
        try:
            budget.count()
            st['requests'] += 1
            page = get(page_url(n), timeout=40)
        except HFError as e:
            st['failures'] += 1
            st['notes'].append(f'page {n}: {e}')
            log(f'  PAHO page {n}: {e}')
            break
        dates = page_dates(page)
        if not dates:
            st['notes'].append(f'page {n}: no dated items (end of list or markup changed)')
            complete = n > 0 and oldest_ok is not None
            break
        st['pages'] += 1
        for ev in fetch_paho.decode(page, limit=10 ** 6):
            when = parse_date(ev.get('updated'))
            if not when or when < since or when > until:
                continue
            keep_earliest(items, finalise(ev, source_date=when, field='list date', key=f"{ev['url']}|{ev.get('country_code') or ''}"))
        oldest_ok = min(dates)
        if oldest_ok < since:
            complete = True
            break
    if complete:
        st['intervals'] = add_interval([], since, until)
        st['status'] = 'complete'
    elif oldest_ok is not None:
        st['intervals'] = add_interval([], max(oldest_ok, since), until)
        st['status'] = 'partial'
    else:
        st['status'] = 'failed'
    st['items'] = len(items)
    st['resumable'] = st['status'] != 'complete'
    save_items(SLUG, items)
    save_state(SLUG, st)
    log(f'PAHO backfill: {st["pages"]} pages, {len(items)} items, {st["status"]}, covered {st["intervals"]}')
    return st, items


__all__ = ['run', 'page_url', 'SLUG', 'SOURCE']
