"""WOAH WAHIS animal-disease reports, dated range → data/sources/wahis/.

Verified 2026-11-14 with two list requests: the public list (POST pi/event/filtered-list) returns ONE ROW PER EVENT,
carrying the event's LATEST report, and its submissionDate filter applies to that latest report. (1 Sep – 1 Nov 2026:
233 events, the first 200 rows 200 distinct events; 10 of the 12 events in the 2026-10-30 fixture had left that window
after newer reports.) A date-range walk of the list therefore finds every event active since `since`, but not the
event's earlier reports, and the earliest report is the one a replay needs.

So, for every event on a PULSE pathogen (fetch_wahis.pathogen_for, the live rule), this walks the event's report chain:
each report's detail (`all-information`) gives its date (`report.reportedOn`) and its predecessor
(`report.previousReportId`). The walk goes back from the latest report to the immediate notification (reportNumber 0)
or to the first report dated before `since`. Every report becomes one item, decoded by fetch_wahis.decode() (the live
event, with the report's own outbreak coordinates) and dated by its own reportedOn. The latest report's reportedOn is
checked against the list's submissionDate and the largest difference is recorded (checks.latest_date_diff_s).

Requests: the list in 30-day chunks of latest-report dates (about 2 pages each), then one detail per report on the
chains, SPACING_S apart. Expect a few thousand details; the API answered in ~1 s per call by day and 13–15 s at 05:00Z
(health-feeds README), so this can take more than one leg. Progress is checkpointed and resumes.
"""
import time
from datetime import timedelta
from hf import post_json, get_json, HFError, parse_date
import fetch_wahis
from bf_common import Budget, finalise, keep_earliest, load_state, save_state, load_items, save_items, add_interval, sleep_s, iso, now, log

SLUG = 'wahis'
SOURCE = 'WOAH WAHIS'
CHUNK_DAYS = 30
PAGE_SIZE = 200
MAX_PAGES = 50
SPACING_S = fetch_wahis.SPACING_S
MAX_ATTEMPTS = 3
SAVE_EVERY = 10
ROW_KEYS = ('reportId', 'eventId', 'country', 'disease', 'subType', 'eventStartDate', 'eventStatus', 'reason', 'reportType',
            'submissionDate', 'reportNumber', 'isAquatic')


def list_body(a, b, page: int) -> dict:
    return {'pageSize': PAGE_SIZE, 'pageNumber': page, 'sortColumn': 'submissionDate', 'sortOrder': 'asc',
            'reportTypes': ['IN', 'FUR'],
            'submissionDate': {'from': a.strftime('%Y-%m-%dT%H:%M:%S.000Z'), 'to': b.strftime('%Y-%m-%dT%H:%M:%S.000Z')}}


def chunks(since, until):
    out, a = [], since
    while a < until:
        b = min(a + timedelta(days=CHUNK_DAYS), until)
        out.append((a, b))
        a = b
    return out


def run(since, until=None, budget=None, post=post_json, get=get_json, sleep=time.sleep):
    budget = budget or Budget()
    st = load_state(SLUG, {'source': SOURCE, 'class': 'animal', 'date_field': 'report.reportedOn', 'events': {}, 'chains': {},
                           'chunks_done': [], 'until': None, 'since': None, 'requests': 0, 'failures': 0, 'notes': [],
                           'other_disease_events': 0, 'checks': {'latest_date_diff_s': 0, 'compared': 0}})
    if st['since'] != iso(since):                                   # a new window: start again
        st.update(events={}, chains={}, chunks_done=[], until=None, since=iso(since), requests=0, failures=0, notes=[],
                  other_disease_events=0, checks={'latest_date_diff_s': 0, 'compared': 0})
        items = {}
    else:
        items = load_items(SLUG)
    until = parse_date(st['until']) if st['until'] else (until or now())
    st['until'] = iso(until)
    st['requested'] = {'since': iso(since), 'until': iso(until)}
    leg_requests = 0

    def call(fn, *args, **kw):
        nonlocal leg_requests
        budget.count()
        st['requests'] += 1
        leg_requests += 1
        sleep_s(SPACING_S, sleep)
        return fn(*args, **kw)

    # 1. every event whose latest report falls in [since, until], chunk by chunk
    list_ok = True
    for a, b in chunks(since, until):
        key = f'{iso(a)}/{iso(b)}'
        if key in st['chunks_done']:
            continue
        rows, total, ok = [], None, True
        for page in range(MAX_PAGES):
            if budget.spent():
                ok = False
                break
            try:
                d = call(post, fetch_wahis.LIST_URL, list_body(a, b, page), timeout=90)
            except HFError as e:
                st['failures'] += 1
                st['notes'].append(f'list {key} page {page}: {e}')
                ok = False
                break
            lst = d.get('list') or []
            total = int(d.get('totalSize') or 0)
            rows.extend(lst)
            if not lst or len(rows) >= total:
                break
        if not ok or (total is not None and len(rows) < total):
            list_ok = False
            break                                                   # chunks stay in order; the next leg resumes here
        for r in rows:
            if fetch_wahis.pathogen_for(r.get('disease')):
                st['events'][str(r.get('eventId'))] = {k: r.get(k) for k in ROW_KEYS}
            else:
                st['other_disease_events'] += 1
        st['chunks_done'].append(key)
        save_state(SLUG, st)

    # 2. each event's report chain, newest to oldest
    stopped = False
    for eid in sorted(st['events'], key=lambda k: int(k) if k.isdigit() else 0):
        row = st['events'][eid]
        ch = st['chains'].setdefault(eid, {'next': row.get('reportId'), 'done': False, 'reports': 0, 'attempts': 0})
        while not ch['done']:
            if budget.spent():
                stopped = True
                break
            rid = ch['next']
            try:
                detail = call(get, fetch_wahis.REPORT_URL.format(rid), timeout=60, retries=1)
            except HFError as e:
                st['failures'] += 1
                ch['attempts'] += 1
                if ch['attempts'] >= MAX_ATTEMPTS:
                    ch.update(done=True, failed=True, next=None)
                    st['notes'].append(f'event {eid}: report {rid} failed {MAX_ATTEMPTS} times: {e}')
                break
            rep = detail.get('report') or {}
            n = rep.get('reportNumber')
            when = parse_date(rep.get('reportedOn'))
            if when is None:
                ch.update(done=True, failed=True, next=None)
                st['notes'].append(f'event {eid}: report {rid} has no reportedOn')
                break
            if ch['reports'] == 0 and parse_date(row.get('submissionDate')):
                diff = abs((when - parse_date(row['submissionDate'])).total_seconds())
                st['checks']['latest_date_diff_s'] = max(st['checks']['latest_date_diff_s'], round(diff, 1))
                st['checks']['compared'] += 1
            r = dict(row, reportId=rid, reportType='IN' if n == 0 else 'FUR', reportNumber=n, submissionDate=rep.get('reportedOn'))
            for ev in fetch_wahis.decode([r], {str(rid): fetch_wahis.compact(detail)}, when):
                keep_earliest(items, finalise(ev, source_date=when, field='report.reportedOn', key=f'report-{rid}',
                                              extra={'event_id': eid, 'report_number': n}))
            ch['reports'] += 1
            ch['attempts'] = 0
            prev = rep.get('previousReportId')
            if n == 0 or not prev or when < since:
                ch.update(done=True, next=None)
            else:
                ch['next'] = prev
            if leg_requests % SAVE_EVERY == 0:
                save_items(SLUG, items)
                save_state(SLUG, st)
        if stopped:
            break

    chains_left = [e for e, c in st['chains'].items() if not c['done']] + [e for e in st['events'] if e not in st['chains']]
    chains_failed = [e for e, c in st['chains'].items() if c.get('failed')]
    complete = list_ok and not chains_left and not chains_failed
    st['status'] = 'complete' if complete else 'partial'
    st['intervals'] = add_interval([], since, until) if complete else []
    st['items'] = len(items)
    st['events_on_pulse_pathogens'] = len(st['events'])
    st['chains_left'] = len(chains_left)
    st['chains_failed'] = len(chains_failed)
    st['resumable'] = not complete and (not list_ok or bool(chains_left))
    save_items(SLUG, items)
    save_state(SLUG, st)
    log(f'WAHIS backfill: {len(st["events"])} events on PULSE pathogens, {len(items)} reports, {len(chains_left)} chains left, '
        f'{len(chains_failed)} failed, {leg_requests} requests this leg, {st["status"]}')
    return st, items


__all__ = ['run', 'list_body', 'chunks', 'SLUG', 'SOURCE']
