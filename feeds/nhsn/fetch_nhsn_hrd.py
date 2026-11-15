#!/usr/bin/env python3
"""CDC NHSN Hospital Respiratory Data by jurisdiction → data/hrd.json (the `nhsn` feed).

Added 2026-11-15 with the owner's approval (PULSE Web, docs/PLAN.md §9 decision 7), from the draft written by PULSE Web's
care lane (PULSE-Web/pipeline-drafts/nhsn-hrd/). PULSE Web reads
https://raw.githubusercontent.com/Deasus/health-feeds/nhsn/hrd.json and falls back to its own dated snapshot when this file
is missing. Daily cadence (CDC updates the dataset weekly, on Fridays per its dashboard page). One request a run, about
330 KB (15 weeks × 67 jurisdictions); `SOCRATA_APP_TOKEN` is sent when the pipeline has it, as fetch_cdc_nndss.py does.
A run that decodes fewer than 50 jurisdictions or no USA row writes nothing, so the published file is kept.
Not merged into `extract/events.json`: these are capacity figures, not events (the envelope's `items` is empty).

Upstream (read 2026-11-14): Socrata dataset ua7e-t2fy on data.cdc.gov, "Weekly Hospital Respiratory Data (HRD) Metrics by
Jurisdiction, National Healthcare Safety Network (NHSN)". Metadata licence: "Public Domain U.S. Government"
(termsLink https://www.usa.gov/government-works). Update frequency (metadata): Weekly. Suggested citation (metadata):
"Centers for Disease Control and Prevention, Division of Healthcare Quality Promotion, National Healthcare Safety Network,
Weekly United States Hospital Respiratory Data (HRD) Metrics by Jurisdiction, National Healthcare Safety Network (NHSN)".
Columns used (their metadata descriptions): weekendingdate (MMWR week end date), jurisdiction, numinptbeds / numinptbedsocc
/ numicubeds / numicubedsocc ("captured for Wednesday of the reporting week"), pctinptbedsocc / pcticubedsocc ("Percent of
… beds occupied by any patient"), numinptbedshosprep / numicubedshosprep ("Total number of hospitals that reported").

What is kept: states, DC, the territories and USA (the ten HHS regions are dropped); the newest WEEKS weeks; every figure
as published (counts as whole numbers, percentages exactly as CDC states them, never recomputed). REPORTED.

Python 3.9+, standard library only.
  python3 fetch_nhsn_hrd.py --out data/hrd.json                                  # one request to data.cdc.gov
  python3 fetch_nhsn_hrd.py --rows saved_rows.json --now 2026-11-14T22:50:00Z --out hrd.json   # from a saved response
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = 'https://data.cdc.gov/resource/ua7e-t2fy.json'
PAGE = 'https://data.cdc.gov/d/ua7e-t2fy'
CREDIT = 'CDC National Healthcare Safety Network (NHSN), Hospital Respiratory Data (HRD) by jurisdiction'
LICENCE = 'Public Domain U.S. Government'
UA = 'health-feeds/1.0 (+https://github.com/Deasus/health-feeds)'
WEEKS = 15
COLUMNS = ['weekendingdate', 'jurisdiction', 'numinptbeds', 'numinptbedsocc', 'numicubeds', 'numicubedsocc', 'pctinptbedsocc',
           'pcticubedsocc', 'numinptbedshosprep', 'numinptbedsocchosprep', 'numicubedshosprep', 'numicubedsocchosprep']
FIELDS = ['weekEnding', 'inptBeds', 'inptOcc', 'inptPct', 'icuBeds', 'icuOcc', 'icuPct', 'hospReportingInpt', 'hospReportingIcu']
CAVEATS = ['Data for reporting dates from 1 Nov 2024 are reported under the current CMS mandate (dataset description).',
           'Psychiatric, rehabilitation and religious non-medical hospitals are excluded from the metrics (dataset description).',
           'Occupancy from the week ending 12 Oct 2024 is a single-day (Wednesday) value (dataset description).',
           'Figures may change week to week as CDC applies corrections and backfilled data (dataset description).']
JURISDICTION = re.compile(r'^(?:[A-Z]{2}|USA)$')


def count(v):
    """'21665.0' → 21665; blank → None. Counts are whole numbers as published (the API writes them with '.0')."""
    if v is None or str(v).strip() == '':
        return None
    return int(round(float(v)))


def pct(v):
    """The percentage exactly as CDC publishes it ('80.1100' → 80.11), never recomputed from the counts."""
    if v is None or str(v).strip() == '':
        return None
    return float(v)


def decode(rows, weeks=WEEKS):
    """Socrata rows → ({jurisdiction: [[week, inptBeds, inptOcc, inptPct, icuBeds, icuOcc, icuPct, hospInpt, hospIcu], …]}, [weeks newest first])."""
    by, seen = {}, set()
    for r in rows:
        j = r.get('jurisdiction') or ''
        if not JURISDICTION.match(j):
            continue
        w = str(r.get('weekendingdate') or '')[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', w):
            continue
        seen.add(w)
        by.setdefault(j, []).append([w, count(r.get('numinptbeds')), count(r.get('numinptbedsocc')), pct(r.get('pctinptbedsocc')),
                                     count(r.get('numicubeds')), count(r.get('numicubedsocc')), pct(r.get('pcticubedsocc')),
                                     count(r.get('numinptbedshosprep')), count(r.get('numicubedshosprep'))])
    keep = sorted(seen, reverse=True)[:weeks]
    out = {}
    for j in sorted(by):
        xs = sorted((x for x in by[j] if x[0] in keep), key=lambda x: x[0], reverse=True)
        if xs:
            out[j] = xs
    return out, keep


def query_url(since):
    q = {'$select': ','.join(COLUMNS), '$where': f"weekendingdate >= '{since}'", '$order': 'weekendingdate DESC, jurisdiction', '$limit': '5000'}
    return BASE + '?' + urllib.parse.urlencode(q)


def download(url):
    h = {'User-Agent': UA}
    tok = os.environ.get('SOCRATA_APP_TOKEN')
    if tok:
        h['X-App-Token'] = tok
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))


def envelope(states, weeks, *, now, upstream):
    return {
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'upstream': upstream, 'page': PAGE, 'credit': CREDIT, 'licence': LICENCE,
        'kind': 'REPORTED', 'what': 'Hospital beds and their occupancy by state, each Wednesday, as hospitals reported them to CDC NHSN (percentages as CDC publishes them).',
        'fields': FIELDS, 'caveats': CAVEATS, 'weeks': weeks, 'newest': weeks[0] if weeks else None,
        'count': 0, 'items': [], 'states': states,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--rows', help='read a saved Socrata response instead of downloading')
    ap.add_argument('--out', default=os.path.join(os.getcwd(), 'data', 'hrd.json'))
    ap.add_argument('--now', help='the reference time, ISO (default: now, UTC)')
    ap.add_argument('--weeks', type=int, default=WEEKS)
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now.replace('Z', '+00:00')) if a.now else datetime.now(timezone.utc)
    since = (now - timedelta(days=7 * (a.weeks + 1))).strftime('%Y-%m-%d')
    url = query_url(since)
    if a.rows:
        with open(a.rows, encoding='utf-8') as f:
            rows = json.load(f)
    else:
        rows = download(url)
    states, weeks = decode(rows, a.weeks)
    if 'USA' not in states or len(states) < 50:
        print(f'refusing to write: only {len(states)} jurisdictions decoded (keep the previous file)', file=sys.stderr)
        return 1
    env = envelope(states, weeks, now=now, upstream=url)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(env, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, a.out)
    print(f'{a.out}: {len(states)} jurisdictions, weeks {weeks[-1]} → {weeks[0]}, {os.path.getsize(a.out) // 1024} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
