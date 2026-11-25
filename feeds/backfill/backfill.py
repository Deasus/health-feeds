#!/usr/bin/env python3
"""The one-off dated-range backfill (never listed in feeds.py, so the conductor never runs it).

  python3 backfill.py fetch <gdelt|wahis|paho|hdx|reliefweb>   one source, resuming from data/sources/<slug>/
  python3 backfill.py merge [--leg N]                          data/sources/*/ → data/events.json.gz + data/manifest.json

Runs with cwd = work/backfill (feeds/backfill/run.py prepares it the way the conductor prepares a feed). BACKFILL_SINCE
sets the window's start (default 2024-04-01T00:00:00Z); the window ends when each source's first leg starts.

Output, published on the `backfill` branch:
  events.json.gz   {generated_at, upstream, credit, licence, count, schema, since, by_source, items: [HealthEvent…]}:
                   every item decoded by its live fetcher and dated by its SOURCE in `updated` (bf.date_field says which
                   field), one item per source record
  manifest.json    per source: status (complete | partial | skipped | failed | not_run), the window asked for, the
                   intervals of source dates actually covered (GDELT: per query), requests, failures and notes
  sources/<slug>/  each source's items and checkpoint, so a later leg resumes
The last line printed by `merge` is `UNFINISHED=<slugs>` (empty when every source is done or cannot go further).
"""
import json
import os
import sys
from collections import Counter
from hf import log, iso, now, read_json, envelope, DATA
import bf_common
from bf_common import since_dt, load_items, write_gz_json
import bf_gdelt
import bf_wahis
import bf_paho
import bf_hdx
import bf_reliefweb

MODULES = {m.SLUG: m for m in (bf_gdelt, bf_wahis, bf_paho, bf_hdx, bf_reliefweb)}
SCHEMA = 'health-feeds.backfill/1'
MANIFEST_SCHEMA = 'health-feeds.backfill.manifest/1'
WORKING_KEYS = ('events', 'chains', 'chunks_done', 'tasks')          # checkpoint detail, kept out of the manifest


def fetch(slug: str) -> int:
    if slug not in MODULES:
        log(f'unknown source {slug!r}; one of {sorted(MODULES)}')
        return 2
    st, _ = MODULES[slug].run(since_dt())
    return 0 if st.get('status') in ('complete', 'partial', 'skipped') else 1


def merge(leg=None) -> int:
    items, sections, unfinished = {}, {}, []
    for slug, mod in MODULES.items():
        st = read_json(os.path.join(bf_common.SOURCES_DIR, slug, 'state.json'))
        if not st:
            sections[mod.SOURCE] = {'slug': slug, 'status': 'not_run', 'intervals': []}
            unfinished.append(slug)
            continue
        sec = {k: v for k, v in st.items() if k not in WORKING_KEYS}
        sec['slug'] = slug
        if slug == 'gdelt':
            for q in sec.get('queries') or []:
                q.pop('keywords', None)
        sections[mod.SOURCE] = sec
        if st.get('resumable'):
            unfinished.append(slug)
        for e in load_items(slug).values():
            items[e['id']] = e
    ordered = sorted(items.values(), key=lambda e: (e.get('updated') or '', e['id']))
    by_source = dict(sorted(Counter(e['source'] for e in ordered).items()))
    env = envelope(ordered, upstream='health-feeds backfill: dated-range runs of gdelt, wahis, paho, hdx, reliefweb',
                   credit='see each event', licence='see each event', schema=SCHEMA, since=iso(since_dt()), by_source=by_source)
    write_gz_json(os.path.join(DATA, 'events.json.gz'), env)
    prev = read_json(os.path.join(DATA, 'manifest.json')) or {}
    legs = list(prev.get('legs') or [])
    legs.append({'leg': leg, 'merged_at': iso(now()), 'statuses': {s['slug']: s.get('status') for s in sections.values()}})
    manifest = {'schema': MANIFEST_SCHEMA, 'generated_at': iso(now()), 'requested': {'since': iso(since_dt())},
                'note': 'intervals = source dates over which every request the backfill needed succeeded; GDELT per query '
                        '(queries[].intervals, targets = pathogen slugs). A source with no intervals covers nothing.',
                'legs': legs, 'items': len(ordered), 'by_source': by_source, 'unfinished': unfinished, 'sources': sections}
    tmp = os.path.join(DATA, 'manifest.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(DATA, 'manifest.json'))
    log(f'backfill merge: {len(ordered)} items {by_source}; unfinished {unfinished}')
    print(f'UNFINISHED={",".join(unfinished)}', flush=True)
    return 0


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == 'fetch':
        return fetch(argv[1])
    if argv and argv[0] == 'merge':
        leg = int(argv[argv.index('--leg') + 1]) if '--leg' in argv else None
        return merge(leg)
    print(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
