# Backfill: dated-range runs of the fetchers that keep history

A one-off, dispatch-only job (`.github/workflows/backfill.yml`). It re-runs the sources that keep dated history over a past
window, so PULSE can replay "what the pipeline would have shown" (its replay measurement, M1). It is **not** in
`feeds.py`: the conductor never runs it. Its output is the single-commit branch `backfill`, published with
`conductor.publish` like every feed. Drafted 2026-11-14, added 2026-11-25. Python standard library only; every item is
decoded by the live fetcher's own `decode()` into a HealthEvent.

## Run it

```sh
gh workflow run backfill.yml -R Deasus/health-feeds --ref main      # defaults: paho,hdx,reliefweb,wahis,gdelt; leg 1 of 4; since 2024-04-01
```

Each source runs in its own job (up to 5 h of work per leg, inside the job's 345 min). The merge job publishes
`backfill` after every leg and queues the next leg for any unfinished source, up to `max_legs` (concurrency group
`backfill`: legs never overlap). This repository is public, so the minutes are free. Stop: `gh run cancel <id>`
(published checkpoints are kept). Undo: delete the `backfill` branch and the workflow file.

## Output (branch `backfill`)

- `events.json.gz`: `{generated_at, upstream, credit, licence, count, schema: "health-feeds.backfill/1", since, by_source, items}`.
  Each item is a HealthEvent with `updated` = **the date the source gave the record** (never the fetch time), one `id`
  per source record (`stable_id(source, 'bf', key)`), and `bf` naming the field the date came from.
- `manifest.json`: per source `status` (complete, partial, skipped, failed, not_run), the window asked for, and
  `intervals`, the spans of source dates over which every request it needed succeeded (GDELT: per query, in
  `queries[].intervals`, with the pathogen slugs each query `targets`); requests, failures, notes; `legs`; `unfinished`.
  A source with no intervals covers nothing: a replay scores only what an interval covers.
- `sources/<slug>/`: each source's items and checkpoint, so a later leg resumes.

## Sources and how each upstream is treated

| Source | Dated by | Pacing |
|---|---|---|
| GDELT DOC 2.0 (`bf_gdelt.py`) | `seendate` | bursts that start only at **:05 and :35** (`GDELT_SLOT_MINUTES`) and last ≤ 20 min; one request at a time, **≥ 6 s after the previous answer**; on 429 or "limit requests" it waits 15, 30, 60 s and then ends the burst; ≤ 1,000 requests per leg; and **never while the conductor's own GDELT run is due, running or just ended** (below) |
| WOAH WAHIS (`bf_wahis.py`) | each report's `reportedOn` (walks each event's report chain) | 0.5 s apart, as the live fetcher |
| PAHO (`bf_paho.py`) | the list's `<time datetime>` | 2 s apart, ~5 pages |
| HDX (`bf_hdx.py`) | each activity snapshot's `metadata_modified` | 0.5 s apart |
| ReliefWeb (`bf_reliefweb.py`) | `date.created` | makes **no request** without `RELIEFWEB_APPNAME` (status `skipped`) |

Not covered by design: CDC, the State Department, ECDC, FDA, APHIS and WHISPers keep no dated history to request; Google
News has no backfill.

## GDELT and the conductor (2026-11-25)

The conductor runs `gdelt` every 1,800 s from its own start, not at fixed minutes, and a run can last up to its 900 s
timeout, so a :05/:35 burst could overlap it. `ConductorGuard` reads the conductor's `status.json` from this repository's
`status` branch (git, the way `conductor.restore` reads a branch; never an upstream) and allows a request only when that
status is fresh (≤ 10 min old), `gdelt` is not running, its last run ended ≥ 60 s ago, and its next run cannot start
within 150 s (the 90 s request timeout plus 60 s). Otherwise the burst waits. Anything unreadable means wait: it fails
closed. The manifest's `guard` section counts the waits and their reasons. Keep `CONDUCTOR_GDELT_EVERY_S` equal to
`feeds.py`'s `gdelt` interval (a test checks it). Do not dispatch the conductor with `only=gdelt` while a backfill runs:
a one-off conductor run publishes its status only when it ends.
