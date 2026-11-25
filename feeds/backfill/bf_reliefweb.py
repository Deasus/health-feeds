"""ReliefWeb epidemic disasters, dated range → data/sources/reliefweb/.  GUARDED: needs RELIEFWEB_APPNAME.

ReliefWeb's API v2 requires a pre-approved app name on every call ("Every API URL needs a pre-approved appname so
ReliefWeb can monitor usage", apidoc.reliefweb.int, read 2026-11-14); health-feeds has none yet (PLAN decision 9). Without
it this module makes NO request: it records status "skipped" and an empty coverage, so the replay reports every unit
ReliefWeb would have covered as covered by something else or not scorable, never as "no signal".

With the app name: one POST per 1,000 disasters, `type.name = Epidemic` and `date.created ≥ since`, oldest first, decoded
by fetch_reliefweb.decode() and dated by `date.created` (when ReliefWeb first recorded the epidemic). The live decoder's
`updated` is `date.changed`, the latest edit, which a replay cannot use; it is replaced here and kept in bf.changed.
Like the live decoder, this follows the documented response shape and is NOT YET RUN: check the first response.
"""
import json
import os
import urllib.error
import urllib.request
from hf import HFError, UA, parse_date
import fetch_reliefweb
from bf_common import Budget, finalise, keep_earliest, load_state, save_state, save_items, add_interval, iso, now, log

SLUG = 'reliefweb'
SOURCE = 'ReliefWeb'
LIMIT = 1000
MAX_PAGES = 20


def body_for(since, offset: int) -> dict:
    b = json.loads(json.dumps(fetch_reliefweb.BODY))
    b['filter'] = {'operator': 'AND', 'conditions': [
        {'field': 'type.name', 'value': 'Epidemic'},
        {'field': 'date.created', 'value': {'from': iso(since)}}]}
    b['sort'] = ['date.created:asc']
    b['limit'] = LIMIT
    b['offset'] = offset
    return b


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'User-Agent': UA, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise HFError(f'HTTP {e.code}') from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HFError(str(e)[:160]) from e


def run(since, until=None, budget=None, post_json=post, appname=None):
    until = until or now()
    budget = budget or Budget()
    st = load_state(SLUG, {'source': SOURCE, 'class': 'aggregator', 'date_field': 'date.created'})
    st.update(requested={'since': iso(since), 'until': iso(until)}, intervals=[], requests=0, failures=0, notes=[])
    appname = appname if appname is not None else os.environ.get('RELIEFWEB_APPNAME')
    if not appname:
        st.update(status='skipped', items=0, resumable=False,
                  reason='RELIEFWEB_APPNAME is not set: ReliefWeb needs a pre-approved app name (PLAN decision 9); no request made')
        save_items(SLUG, {})
        save_state(SLUG, st)
        log('ReliefWeb backfill: skipped (no approved app name)')
        return st, {}
    items, offset, complete = {}, 0, False
    for _ in range(MAX_PAGES):
        if budget.spent():
            st['notes'].append('budget spent')
            break
        try:
            budget.count()
            st['requests'] += 1
            payload = post_json(f'{fetch_reliefweb.BASE}?appname={appname}', body_for(since, offset))
        except HFError as e:
            st['failures'] += 1
            st['notes'].append(f'offset {offset}: {e}')
            break
        data = payload.get('data') or []
        for ev in fetch_reliefweb.decode(payload):
            created = parse_date(ev.get('onset'))           # decode() sets onset = date.created
            if not created or created > until:
                continue
            keep_earliest(items, finalise(ev, source_date=created, field='date.created',
                                          key=f"{ev.get('url') or ev['title']}|{ev.get('country_code') or ''}",
                                          extra={'changed': ev.get('updated')}))
        offset += len(data)
        total = payload.get('totalCount')
        if len(data) < LIMIT or (total is not None and offset >= int(total)):
            complete = True
            break
    st['status'] = 'complete' if complete else ('partial' if items else 'failed')
    st['intervals'] = add_interval([], since, until) if complete else []
    st['items'] = len(items)
    st['resumable'] = not complete
    save_items(SLUG, items)
    save_state(SLUG, st)
    log(f'ReliefWeb backfill: {len(items)} items, {st["status"]}')
    return st, items


__all__ = ['run', 'body_for', 'SLUG', 'SOURCE']
