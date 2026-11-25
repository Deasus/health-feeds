"""HDX outbreak datasets, dated range → data/sources/hdx/.

The live fetcher (fetch_hdx.py) asks CKAN for the 15 most recently modified datasets per disease title and dates each
by `metadata_modified`, which holds only the LATEST change. A replay needs every change, so this:

1. runs the same title queries (fetch_hdx.DISEASES) with `fq=metadata_modified:[since TO *]`, every page (rows=1000):
   every dataset with any change since `since` (verified 2026-11-14: `q=title:cholera` with that filter → 9 datasets);
2. reads each dataset's activity history (`package_activity_list`, newest first, 100 per page) back to `since`. Each
   activity carries the dataset AS IT WAS then (title, country groups, licence), verified 2026-11-14 on
   yemen-cholera-outbreak-daily-epidemiology-update;
3. decodes each snapshot with fetch_hdx.decode() and dates it by the snapshot's own `metadata_modified` (else the
   activity's timestamp). One item per (dataset, country, UTC day): the earliest change that day.

A dataset whose history cannot be read falls back to its creation date and its latest change (both true dated events)
and the source is marked partial: its coverage then is empty, because earlier changes may be missing.
Requests: 18 searches plus one or more pages of history per dataset (a few hundred), SPACING_S apart.
"""
import time
from hf import get_json, HFError, parse_date
import fetch_hdx
from bf_common import Budget, finalise, keep_earliest, load_state, save_state, load_items, save_items, add_interval, sleep_s, iso, now, log

SLUG = 'hdx'
SOURCE = 'HDX'
SEARCH = fetch_hdx.BASE
ACTIVITY = 'https://data.humdata.org/api/3/action/package_activity_list'
ROWS = 1000
ACTIVITY_PAGE = 100
SPACING_S = 0.5
MAX_ACTIVITY_PAGES = 40


def search_url(disease: str, since, start: int = 0) -> str:
    q = disease.replace(' ', '%20')
    return (f'{SEARCH}?q=title:{q}&fq=metadata_modified:%5B{iso(since)}%20TO%20*%5D&rows={ROWS}&start={start}'
            f'&sort=metadata_modified%20asc')


def activity_url(name: str, offset: int = 0) -> str:
    return f'{ACTIVITY}?id={name}&limit={ACTIVITY_PAGE}&offset={offset}'


def _day(ts: str) -> str:
    return (ts or '')[:10]


def run(since, until=None, budget=None, get=get_json, sleep=time.sleep):
    until = until or now()
    budget = budget or Budget()
    st = load_state(SLUG, {'source': SOURCE, 'class': 'aggregator', 'date_field': 'activity snapshot metadata_modified'})
    st.update(requested={'since': iso(since), 'until': iso(until)}, intervals=[], requests=0, failures=0, notes=[],
              datasets=0, histories_complete=0, histories_incomplete=[])
    items, by_day = {}, {}
    complete = True

    def call(url):
        if budget.spent():
            raise HFError('backfill budget spent')
        budget.count()
        st['requests'] += 1
        sleep_s(SPACING_S, sleep)
        return get(url, timeout=60)

    # 1. every dataset changed since `since`, per disease title query
    datasets = {}
    for d in fetch_hdx.DISEASES:
        start = 0
        while True:
            try:
                res = (call(search_url(d, since, start)) or {}).get('result') or {}
            except HFError as e:
                st['failures'] += 1
                st['notes'].append(f'search {d} start {start}: {e}')
                complete = False
                break
            rows = res.get('results') or []
            for p in rows:
                if p.get('id'):
                    datasets.setdefault(p['id'], p)
            start += len(rows)
            if not rows or start >= int(res.get('count') or 0):
                if rows == [] and start < int(res.get('count') or 0):
                    complete = False
                    st['notes'].append(f'search {d}: {start} of {res.get("count")} returned')
                break
    st['datasets'] = len(datasets)

    def add(pkg, when, key_suffix):
        for ev in fetch_hdx.decode([pkg], since):
            t = parse_date(ev.get('updated'))
            if not t or t > until:
                continue
            k = (pkg.get('id'), ev.get('country_code') or '', _day(ev.get('updated')))
            if k in by_day and by_day[k] <= ev['updated']:
                continue
            by_day[k] = ev['updated']
            keep_earliest(items, finalise(ev, source_date=t, field='activity snapshot metadata_modified',
                                          key=f'{k[0]}|{k[1]}|{k[2]}', extra={'dataset': pkg.get('name'), 'via': key_suffix}))

    # 2 and 3. each dataset's history back to `since`
    for pid in sorted(datasets):
        p = datasets[pid]
        name = p.get('name') or pid
        ok, offset = False, 0
        for _ in range(MAX_ACTIVITY_PAGES):
            try:
                acts = (call(activity_url(name, offset)) or {}).get('result') or []
            except HFError as e:
                st['failures'] += 1
                st['notes'].append(f'history {name}: {e}')
                break
            older = False
            for a in acts:
                ts = parse_date(a.get('timestamp'))
                if not ts:
                    continue
                if ts < since:
                    older = True
                    continue
                if a.get('activity_type') not in ('new package', 'changed package'):
                    continue
                snap = dict(((a.get('data') or {}).get('package')) or p)
                if not snap.get('metadata_modified') or not (a.get('data') or {}).get('package'):
                    snap['metadata_modified'] = a.get('timestamp')
                add(snap, ts, 'activity')
            offset += len(acts)
            if older or len(acts) < ACTIVITY_PAGE:
                ok = True
                break
        if ok:
            st['histories_complete'] += 1
        else:
            complete = False
            st['histories_incomplete'].append(name)
            for field in ('metadata_created', 'metadata_modified'):        # fallback: two true dated events
                t = parse_date(p.get(field))
                if t and since <= t <= until:
                    add(dict(p, metadata_modified=p.get(field)), t, field)
    st['status'] = 'complete' if complete else ('partial' if items or datasets else 'failed')
    st['intervals'] = add_interval([], since, until) if complete else []
    st['items'] = len(items)
    st['resumable'] = not complete
    save_items(SLUG, items)
    save_state(SLUG, st)
    log(f'HDX backfill: {len(datasets)} datasets, {st["histories_complete"]} histories read, {len(items)} items, {st["status"]}')
    return st, items


__all__ = ['run', 'search_url', 'activity_url', 'SLUG', 'SOURCE']
