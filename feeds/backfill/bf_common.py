"""Shared pieces of the dated-range backfill (health-feeds conventions: Python standard library only).

The backfill re-runs the fetchers whose sources keep dated history over a past window, so PULSE's evidence pack can
replay "what the pipeline would have shown" (PULSE Web docs/eris-evidence/METHOD-M1.md). Every source module decodes
with the live fetcher's own decode(), then finalise() turns each decoded HealthEvent into a backfill item:

  updated   the date the SOURCE attached to the record (GDELT seendate, WAHIS reportedOn, PAHO list date, HDX activity
            time, ReliefWeb date.created), never the time we fetched it; the replay's signal time starts here
  id        one id per source record (stable_id(source, 'bf', key)), so two reports of one WAHIS event stay two items
  bf        {date_field, key, ...}: which field the date came from, for the manifest and for audit

Each source keeps its progress in data/sources/<slug>/state.json and its items in data/sources/<slug>/items.json.gz, so a
run that is cancelled or hits its time budget resumes where it stopped. The state carries the source's manifest
section: the window it was asked for, the intervals of source dates it actually covered (every request for that span
succeeded), request and failure counts, and notes.
"""
import gzip
import json
import os
import time
from datetime import timedelta
from hf import iso, parse_date, now, log, stable_id, read_json, DATA

DEFAULT_SINCE = '2024-04-01T00:00:00Z'     # 2025-01-01 minus the 90-day look-back, minus the 180-day unusual test
TIME_BUDGET_S = int(os.environ.get('BACKFILL_TIME_BUDGET_S', str(5 * 3600)))   # + up to 30 min waiting for a GDELT offset < the job's 345 min
SOURCES_DIR = os.path.join(DATA, 'sources')


def since_dt():
    return parse_date(os.environ.get('BACKFILL_SINCE') or DEFAULT_SINCE)


class Budget:
    """A wall-clock and request budget for one leg. spent() is checked before every request."""

    def __init__(self, seconds=None, max_requests=None, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()
        self.seconds = TIME_BUDGET_S if seconds is None else seconds
        self.max_requests = max_requests
        self.requests = 0

    def spent(self) -> bool:
        if self.max_requests is not None and self.requests >= self.max_requests:
            return True
        return self.clock() - self.t0 >= self.seconds

    def count(self, n=1):
        self.requests += n


# ── intervals of covered source dates, kept as [[iso, iso], …] ───────────────────────────────────────────────────

def merge_intervals(intervals):
    """Sort and merge overlapping or touching intervals."""
    spans = sorted((parse_date(a), parse_date(b)) for a, b in intervals if parse_date(a) and parse_date(b))
    out = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return [[iso(a), iso(b)] for a, b in out]


def add_interval(intervals, a, b):
    return merge_intervals(list(intervals) + [[iso(a) if not isinstance(a, str) else a, iso(b) if not isinstance(b, str) else b]])


# ── items ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def finalise(ev: dict, *, source_date, field: str, key: str, extra=None) -> dict:
    """A decoded HealthEvent → a backfill item dated by its source (METHOD-M1 3.3)."""
    ev['updated'] = iso(source_date) if not isinstance(source_date, str) else source_date
    ev['id'] = stable_id(ev['source'], 'bf', key)
    note = {'date_field': field, 'key': key}
    note.update(extra or {})
    ev['bf'] = note
    return ev


def keep_earliest(store: dict, ev: dict) -> None:
    """Add ev to store (id → item); a duplicate keeps the earliest date and the union of its tags (query labels)."""
    old = store.get(ev['id'])
    if old is None:
        store[ev['id']] = ev
        return
    tags = sorted(set(old.get('tags') or []) | set(ev.get('tags') or []))
    if (ev.get('updated') or '') < (old.get('updated') or ''):
        store[ev['id']] = ev
    store[ev['id']]['tags'] = tags


# ── per-source files ──────────────────────────────────────────────────────────────────────────────────────────────

def source_dir(slug: str) -> str:
    d = os.path.join(SOURCES_DIR, slug)
    os.makedirs(d, exist_ok=True)
    return d


def load_state(slug: str, defaults: dict) -> dict:
    st = read_json(os.path.join(source_dir(slug), 'state.json')) or {}
    for k, v in defaults.items():
        st.setdefault(k, v)
    return st


def save_state(slug: str, state: dict) -> None:
    path = os.path.join(source_dir(slug), 'state.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    os.replace(tmp, path)


def load_items(slug_or_path: str) -> dict:
    path = slug_or_path if slug_or_path.endswith('.gz') else os.path.join(source_dir(slug_or_path), 'items.json.gz')
    if not os.path.exists(path):
        return {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return {e['id']: e for e in json.load(f)}


def write_gz_json(path: str, obj) -> None:
    """Deterministic gzip (mtime 0), written atomically."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        with gzip.GzipFile(filename='', mode='wb', fileobj=fh, mtime=0) as gz:
            gz.write(raw)
    os.replace(tmp, path)


def save_items(slug: str, items: dict) -> None:
    write_gz_json(os.path.join(source_dir(slug), 'items.json.gz'), sorted(items.values(), key=lambda e: (e.get('updated') or '', e['id'])))


def sleep_s(seconds: float, sleep=time.sleep) -> None:
    if seconds > 0:
        sleep(seconds)


def day_floor(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def days_between(a, b):
    """Day starts from a to b (b exclusive)."""
    out, d = [], day_floor(a)
    while d < b:
        out.append(d)
        d += timedelta(days=1)
    return out


__all__ = ['DEFAULT_SINCE', 'Budget', 'since_dt', 'merge_intervals', 'add_interval', 'finalise', 'keep_earliest', 'load_state',
           'save_state', 'load_items', 'save_items', 'write_gz_json', 'source_dir', 'sleep_s', 'iso', 'parse_date', 'now', 'log']
