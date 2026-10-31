#!/usr/bin/env python3
"""WOAH WAHIS official animal-disease notifications → data/events.json (zoonoses and HPAI only).

Upstream (verified 2026-10-30), the public API behind https://wahis.woah.org, no key:
  POST https://wahis.woah.org/api/v1/pi/event/filtered-list?language=en
       {pageSize, pageNumber, sortColumn, sortOrder, reportTypes: ["IN","FUR"], submissionDate: {from, to}}
       → {list: [{reportId, eventId, country, disease, reportType, reportNumber, eventStartDate, submissionDate,
                  eventStatus, reason, isAquatic …}], totalSize}
  GET  https://wahis.woah.org/api/v1/pi/review/report/<reportId>/all-information?language=en
       → event (country.isoCode, disease, causalAgent, zoonoticDesc, reason, eventStatus), report, outbreaks
         (latitude/longitude per outbreak, approximate), quantitativeData.totals (per species: susceptible, cases,
         deaths, killed).
WOAH says WAHIS data is "made available to the public without restriction for analysis, interpretation and risk-based
decision-making"; woah.org's site terms ask for attribution and written permission for commercial reuse, so this feed
sits on the licence watch-list (PLAN §4.7) with the request to WOAH drafted in ~/PULSE/notes/.

PULSE is a human-health app: only diseases that map to pathogens.py are kept (HPAI in poultry and wild birds, rabies,
Rift Valley fever, West Nile, anthrax, screwworm, Nipah, CCHF, Q fever, brucellosis, tularemia, EEE …); African swine
fever, foot-and-mouth, lumpy skin and the other animal-only diseases stay out. Every event is tagged `animal` plus
`wild`/`domestic`; animal counts go in facts, never in `cases`. One event per WAHIS event (its newest report in the
window). Report details are fetched at most MAX_REPORT_FETCHES per run, inside a TIME_BUDGET_S wall-clock budget (the API
answered in ~1 s per call on 2026-10-30 and 13–15 s per call at 05:00Z the next morning, so a cold start can take 14 min),
and kept in data/reports.json between runs (saved every CACHE_EVERY fetches); an event whose detail is not cached yet is
placed at its country centroid and upgraded on a later run.
"""
import json
import os
import re
import time
from datetime import timedelta
from hf import get_json, post_json, read_json, write_if_changed, envelope, parse_date, iso, log, fail, HFError, now, DATA
import events, geocode, pathogens

LIST_URL = 'https://wahis.woah.org/api/v1/pi/event/filtered-list?language=en'
REPORT_URL = 'https://wahis.woah.org/api/v1/pi/review/report/{}/all-information?language=en'
PUBLIC_URL = 'https://wahis.woah.org/#/in-review/{}'
CREDIT = 'World Organisation for Animal Health (WOAH), World Animal Health Information System (WAHIS)'
LICENCE = 'WOAH WAHIS public data, attribution to WOAH (commercial reuse: written permission requested, PLAN §4.7)'
WINDOW_DAYS = 60
PAGE_SIZE = 200
MAX_PAGES = 10
MAX_REPORT_FETCHES = int(os.environ.get('WAHIS_MAX_REPORT_FETCHES', '60'))
TIME_BUDGET_S = int(os.environ.get('WAHIS_TIME_BUDGET_S', '600'))   # the API ran at 13–15 s per call on 2026-10-31 05:00Z
CACHE_EVERY = 10
SPACING_S = 0.5
# WAHIS reports these as their own territories; geocode.country does not know them (seen 2026-10-31: rabies, Ceuta and Melilla)
TERRITORIES = {'ceuta': ('ES', 'Ceuta, Spain', 35.889, -5.319), 'melilla': ('ES', 'Melilla, Spain', 35.293, -2.938)}
# WAHIS disease names that would match a human pathogen by accident (fowl typhoid is Salmonella Gallinarum, not typhoid)
SKIP = re.compile(r'fowl typhoid|pullorum|bovine tuberculosis|mycobacterium|mycoplasma|abortusovis|low pathogenicity|swine|porcine|'
                  r'avian chlamydiosis|bovine spongiform|theileria|babesi|infectious (?:bursal|bronchitis|laryngo)|salmon|koi|crayfish|'
                  r'equine encephalomyelitis \((?:western|venezuelan)\)|equine infectious|equine influenza|equine arteritis', re.I)
MAMMAL = re.compile(r'fox|cat\b|cats\b|dog|seal|otter|mink|skunk|bear|dolphin|porpoise|whale|cattle|cow|bovine|swine|pig|goat|sheep|ovine|caprine|'
                    r'horse|equidae|equine|mouse|rat\b|raccoon|lynx|bobcat|puma|cougar|tiger|lion|ferret|marten|badger|squirrel|hedgehog|deer|'
                    r'camel|alpaca|llama|buffalo|bison|wolf|jackal|hyena|monkey|primate|bat\b|bats\b|mammal', re.I)


def pathogen_for(disease):
    if not disease or SKIP.search(disease):
        return None
    return pathogens.primary(disease)


def report_kind(row):
    return 'Immediate notification' if row.get('reportType') == 'IN' else f"Follow-up report {row.get('reportNumber') or ''}".strip()


def compact(detail: dict) -> dict:
    """The parts of a report's all-information payload the event needs (kept in data/reports.json)."""
    ev = detail.get('event') or {}
    outbreaks = detail.get('outbreaks') or []
    pts = [[o['latitude'], o['longitude']] for o in outbreaks
           if isinstance(o.get('latitude'), (int, float)) and isinstance(o.get('longitude'), (int, float))]
    totals = []
    for t in ((detail.get('quantitativeData') or {}).get('totals') or []):
        totals.append({'species': t.get('speciesName'), 'wild': bool(t.get('isWild')), 'susceptible': t.get('susceptible'),
                       'cases': t.get('cases'), 'deaths': t.get('deaths'), 'killed': t.get('killed')})
    places = sorted({(o.get('adminDivision') or o.get('location') or '').strip() for o in outbreaks} - {''})
    return {'fetched': iso(now()), 'iso3': (ev.get('country') or {}).get('isoCode'), 'agent': (ev.get('causalAgent') or {}).get('name'),
            'zoonotic': ev.get('zoonoticDesc') or None, 'started': ev.get('startedOn'), 'confirmed': ev.get('confirmOn'),
            'outbreaks': len(outbreaks), 'points': pts, 'totals': totals, 'places': places[:6]}


def _centre(points):
    """Mean of the outbreak points and a radius that covers them all (+10 km: WAHIS coordinates are approximate)."""
    if not points:
        return None, None, None
    lat = sum(p[0] for p in points) / len(points)
    lng = sum(p[1] for p in points) / len(points)
    radius = max(geocode.distance_km((lat, lng), (p[0], p[1])) for p in points) + 10.0
    return lat, lng, round(radius, 1)


def _sum(totals, key):
    vals = [t.get(key) for t in totals if isinstance(t.get(key), (int, float))]
    return int(sum(vals)) if vals else None


def decode(rows, reports, ref):
    """rows: the filtered list, newest first; reports: {reportId: compact detail}. One event per WAHIS eventId."""
    cutoff = ref - timedelta(days=WINDOW_DAYS)
    out, seen = [], set()
    for r in rows:
        submitted = parse_date(r.get('submissionDate'))
        p = pathogen_for(r.get('disease'))
        if not p or r.get('eventId') in seen or (submitted and submitted < cutoff):
            continue
        seen.add(r.get('eventId'))
        d = reports.get(str(r.get('reportId'))) or {}
        raw_country = re.sub(r'\s*\(.*?\)', '', r.get('country') or '').strip()
        country = geocode.country(d.get('iso3') or '') or geocode.country(raw_country)
        terr = None if country else TERRITORIES.get(raw_country.lower())
        iso2 = country['iso2'] if country else (terr[0] if terr else None)
        country_name = country['name'] if country else (terr[1] if terr else raw_country)
        disease = (r.get('disease') or '').strip()
        totals = d.get('totals') or []
        wild = any(t.get('wild') for t in totals) or 'wild' in disease.lower()
        species = sorted({(t.get('species') or '').strip().lower() for t in totals} - {''})
        mammals = any(MAMMAL.search(sp) for sp in species)
        low = disease.lower()
        if '(poultry)' in low:
            host = 'poultry'
        elif 'non-poultry' in low or 'wild bird' in low:
            host = 'wild birds and mammals' if mammals else 'wild birds'
        elif len(species) <= 2 and species:
            host = ('wild ' if wild else '') + ' and '.join(species)
        elif species:
            host = f"{'wild ' if wild else ''}{species[0]} and {len(species) - 1} other species"
        else:
            host = 'wild animals' if wild else 'animals'
        lat, lng, radius = _centre(d.get('points') or [])
        if lat is None and terr:
            lat, lng, radius = terr[2], terr[3], 10.0
        started = parse_date(r.get('eventStartDate')) or parse_date(d.get('started'))
        severity = events.MODERATE if (d.get('zoonotic') or (p['slug'] == 'h5n1' and mammals)) else events.MINOR
        n_out = d.get('outbreaks')
        deaths, killed, cases = _sum(totals, 'deaths'), _sum(totals, 'killed'), _sum(totals, 'cases')
        bits = [f'{report_kind(r)}']
        if r.get('reason'):
            bits.append(str(r['reason']).strip().rstrip('.').lower())
        if n_out:
            where = f" in {', '.join(d.get('places') or [])}" if d.get('places') else ''
            bits.append(f'{n_out} outbreak{"s" if n_out != 1 else ""}{where}')
        counts = [f'{v:,} {k}' for k, v in (('animal cases', cases), ('deaths', deaths), ('killed', killed)) if v]
        headline = '; '.join(bits) + (f' ({", ".join(counts)})' if counts else '') + '.'
        places = d.get('places') or []
        area = f'{places[0]}, {country_name}' if places and country_name else country_name or None
        facts = [['Disease (WOAH)', disease], ['Species', ', '.join(species) if species else host]]
        if d.get('agent'):
            facts.append(['Causal agent', d['agent']])
        for label, v in (('Outbreaks', n_out), ('Susceptible', _sum(totals, 'susceptible')), ('Animal cases', cases),
                         ('Animal deaths', deaths), ('Killed and disposed of', killed)):
            if v is not None:
                facts.append([label, f'{v:,}'])
        facts += [['Report', report_kind(r)], ['Reason', str(r.get('reason') or '').strip() or '—'],
                  ['Status', str(r.get('eventStatus') or '').strip() or '—']]
        if started:
            facts.append(['Event started', started.strftime('%d %b %Y')])
        if d.get('zoonotic'):
            facts.append(['Zoonotic note', 'see report'])
        tags = ['animal', 'wild' if wild else 'domestic'] + (['zoonotic-cases'] if d.get('zoonotic') else []) + (['aquatic'] if r.get('isAquatic') else [])
        ev = events.make(
            source='WOAH WAHIS', source_tier='official', kind='outbreak', severity=severity,
            title=f"{p['name']} in {host}: {country_name or 'unknown country'}", headline=headline,
            body=events.clip(d.get('zoonotic'), 600) if d.get('zoonotic') else None, pathogen=p,
            country_code=iso2, countries=[iso2] if iso2 else [], lat=lat, lng=lng,
            geo_precision=('point' if radius <= 60 else 'admin1' if radius <= 300 else 'country') if lat is not None else None,
            area=area, updated=submitted, onset=started, url=PUBLIC_URL.format(r.get('reportId')), facts=facts,
            credit=CREDIT, licence=LICENCE, tags=tags, source_id=f"wahis-event-{r.get('eventId')}")
        if lat is not None:
            ev['geo_radius_km'] = radius
        out.append(ev)
    return out


def fetch_list(ref):
    since = ref - timedelta(days=WINDOW_DAYS)
    rows = []
    for page in range(MAX_PAGES):
        body = {'pageSize': PAGE_SIZE, 'pageNumber': page, 'sortColumn': 'submissionDate', 'sortOrder': 'desc',
                'reportTypes': ['IN', 'FUR'],
                'submissionDate': {'from': since.strftime('%Y-%m-%dT00:00:00.000Z'), 'to': (ref + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00.000Z')}}
        d = post_json(LIST_URL, body, timeout=60)
        lst = d.get('list') or []
        rows.extend(lst)
        if not lst or len(rows) >= int(d.get('totalSize') or 0):
            break
        time.sleep(SPACING_S)
    return rows


def _save(path, reports):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(reports, f, ensure_ascii=False, separators=(',', ':'))


def main():
    ref = now()
    t0 = time.monotonic()
    try:
        rows = fetch_list(ref)
    except HFError as e:
        fail(str(e))
    if not rows:
        fail('WAHIS list came back empty')
    cache_path = os.path.join(DATA, 'reports.json')
    reports = read_json(cache_path) or {}
    wanted = [r for r in rows if pathogen_for(r.get('disease'))]
    fetched = failed = 0
    seen = set()
    for r in wanted:                                   # newest report per event first; the budget caps a cold start
        if r.get('eventId') in seen:
            continue
        seen.add(r.get('eventId'))
        rid = str(r.get('reportId'))
        if rid in reports or fetched >= MAX_REPORT_FETCHES:
            continue
        if time.monotonic() - t0 > TIME_BUDGET_S:          # slow API: publish what we have, the cache fills over later runs
            log(f'  time budget of {TIME_BUDGET_S}s reached after {fetched} detail fetches; the rest stay at country precision')
            break
        try:
            reports[rid] = compact(get_json(REPORT_URL.format(rid), timeout=60, retries=1))
            fetched += 1
            if fetched % CACHE_EVERY == 0:
                _save(cache_path, reports)                 # a killed run still keeps its progress
        except HFError as e:
            failed += 1
            log(f'  report {rid}: {e}')
        time.sleep(SPACING_S)
    keep = {str(r.get('reportId')) for r in wanted}
    reports = {k: v for k, v in reports.items() if k in keep}
    items = decode(rows, reports, ref)
    log(f'WAHIS: {len(rows)} reports in {WINDOW_DAYS} days, {len(wanted)} on PULSE pathogens → {len(items)} events; '
        f'{fetched} details fetched, {failed} failed, {len(reports)} cached')
    _save(cache_path, reports)
    write_if_changed('events.json', envelope(items, upstream=LIST_URL, credit=CREDIT, licence=LICENCE, window_days=WINDOW_DAYS,
                                             reports_in_window=len(rows), details_cached=len(reports),
                                             details_fetched=fetched, budget_s=TIME_BUDGET_S, elapsed_s=round(time.monotonic() - t0, 1)))


if __name__ == '__main__':
    main()
