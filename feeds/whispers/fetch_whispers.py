#!/usr/bin/env python3
"""USGS WHISPers wildlife morbidity and mortality events → data/events.json.

Upstream (verified 2026-10-30; USGS, public domain):
  https://whispers.usgs.gov/api/eventsummaries/?page_size=100&ordering=-start_date&page=N
  → results[]: id, event_type_string, event_status_string, start_date, end_date, affected_count,
    eventdiagnoses[] {diagnosis_string, suspect}, species[] {name, class_name},
    administrativelevelones[] {id, name, abbreviation}, administrativeleveltwos[] {name, administrative_level_one, points}
WHISPers is the National Wildlife Health Center's public event system: the wildlife layer HealthMap draws from the
Wildlife Data Integration Network. Kept: events whose diagnosis maps to a PULSE pathogen (HPAI, West Nile, rabies, plague,
tularemia, brucellosis, EEE, salmonella …) and undiagnosed die-offs of at least MASS_MORTALITY animals (pathogen `unknown`,
tagged `undiagnosed`). Avian cholera, duck plague and the other wildlife-only names that would match a human pathogen are
skipped. Pages are read newest-first until the events are older than WINDOW_DAYS. Counties are placed with the Census
gazetteer (geocode.county). Tagged `animal` + `wildlife`, and `suspect` while the diagnosis is presumptive.
"""
import re
import time
from datetime import timedelta
from hf import get_json, write_if_changed, envelope, parse_date, log, fail, HFError, now
import events, geocode, pathogens

URL = 'https://whispers.usgs.gov/api/eventsummaries/?page_size=100&ordering=-start_date&page={}'
PUBLIC_URL = 'https://whispers.usgs.gov/event/{}'
CREDIT = 'USGS National Wildlife Health Center, WHISPers'
LICENCE = 'US Government work, public domain (WHISPers events may be provisional)'
WINDOW_DAYS = 45
MAX_PAGES = 6
MASS_MORTALITY = 100
SKIP = re.compile(r'avian cholera|fowl cholera|duck plague|hog cholera|avian pox|avian tuberculosis|new duck disease|white-nose|'
                  r'avian malaria|canine distemper|trichomon|aspergill', re.I)
PENDING = re.compile(r'pending|undetermined|open|unknown', re.I)
ZOONOTIC = {'h5n1', 'rabies', 'west_nile', 'eee', 'plague', 'tularemia', 'brucellosis', 'anthrax', 'salmonella', 'leptospirosis', 'q_fever'}


def pathogen_for(diagnoses: list):
    for d in diagnoses:
        if SKIP.search(d):
            continue
        p = pathogens.primary(d)
        if p and p['slug'] != 'unknown':
            return p
    return None


def decode(results: list, ref) -> list:
    cutoff = ref - timedelta(days=WINDOW_DAYS)
    out = []
    for r in results:
        start = parse_date(r.get('start_date'))
        if not start or start < cutoff:
            continue
        dx = r.get('eventdiagnoses') or []
        diags = [(d.get('diagnosis_string') or '').strip() for d in dx if d.get('diagnosis_string')]
        suspect = any(d.get('suspect') or 'suspect' in (d.get('diagnosis_string') or '').lower() for d in dx)
        affected = int(r.get('affected_count') or 0)
        p = pathogen_for(diags)
        undiagnosed = False
        if not p:
            if affected >= MASS_MORTALITY and (not diags or any(PENDING.search(d) for d in diags)):
                p, undiagnosed = pathogens.BY_SLUG['unknown'], True
            else:
                continue
        states = {a.get('id'): a for a in (r.get('administrativelevelones') or [])}
        counties = []
        for a2 in (r.get('administrativeleveltwos') or []):
            code = (states.get(a2.get('administrative_level_one')) or {}).get('abbreviation')
            c = geocode.county(a2.get('name') or '', code) if code else None
            counties.append((a2.get('name') or '', code, c))
        species = [s.get('name') for s in (r.get('species') or []) if s.get('name')]
        first_state = next(iter(states.values()), None)
        st = geocode.state((first_state or {}).get('abbreviation') or '', 'US') if first_state else None
        pts = [c for _, _, c in counties if c]
        if pts:
            lat = sum(c['lat'] for c in pts) / len(pts)
            lng = sum(c['lng'] for c in pts) / len(pts)
            radius = max(geocode.distance_km((lat, lng), (c['lat'], c['lng'])) + c['radius_km'] for c in pts)
            if len(pts) == 1:
                area, precision = f"{pts[0]['name']}, {pts[0]['state']}", 'point'
            else:
                area, precision = f"{len(pts)} counties, {'/'.join(sorted({c['state'] for c in pts}))}", 'admin1'
        elif st:
            lat = lng = radius = None
            area, precision = st['name'], 'admin1'
        else:
            continue
        sp = (', '.join(species[:3]) + (' and others' if len(species) > 3 else '')) if species else 'wildlife'
        title = f'Wildlife die-off under investigation: {area}' if undiagnosed else f"{p['name']} in wildlife: {area}"
        headline = f"{affected:,} {sp} affected since {start.strftime('%d %b')}; diagnosis: {', '.join(diags) or 'pending'}."
        severity = events.MINOR if p['slug'] in ZOONOTIC else events.INFO
        tags = ['animal', 'wildlife'] + (['suspect'] if suspect else []) + (['undiagnosed'] if undiagnosed else [])
        facts = [['Affected', f'{affected:,}'], ['Event type', r.get('event_type_string') or '—'], ['Status', r.get('event_status_string') or '—'],
                 ['Diagnosis', '; '.join(diags) or 'pending'], ['Species', ', '.join(species[:6]) or '—'],
                 ['Counties', ', '.join(f'{n} ({c})' for n, c, _ in counties[:6] if n) or '—'], ['Started', start.strftime('%d %b %Y')]]
        if r.get('end_date'):
            facts.append(['Ended', str(r['end_date'])])
        ev = events.make(source='USGS WHISPers', source_tier='official', kind='surveillance', severity=severity, title=title, headline=headline,
                         pathogen=p, country_code='US', admin1=st['name'] if st else None, admin1_code=st['code'] if st else None,
                         lat=lat, lng=lng, geo_precision=precision, area=area, updated=parse_date(r.get('end_date')) or start, onset=start,
                         url=PUBLIC_URL.format(r.get('id')), facts=facts, credit=CREDIT, licence=LICENCE, tags=tags, source_id=str(r.get('id')))
        if radius is not None:
            ev['geo_radius_km'] = round(radius, 1)
        out.append(ev)
    return out


def main():
    ref = now()
    cutoff = ref - timedelta(days=WINDOW_DAYS)
    results = []
    for page in range(1, MAX_PAGES + 1):
        try:
            d = get_json(URL.format(page), timeout=90, retries=1)
        except HFError as e:
            if results:
                log(f'  page {page}: {e}')
                break
            fail(str(e))
        rs = d.get('results') or []
        results.extend(rs)
        dated = [parse_date(r.get('start_date')) for r in rs if r.get('start_date')]
        if not rs or not d.get('next') or (dated and min(dated) < cutoff):
            break
        time.sleep(1)
    items = decode(results, ref)
    log(f'WHISPers: {len(results)} events read, {len(items)} kept in {WINDOW_DAYS} days '
        f'({sum(1 for e in items if "undiagnosed" in e["tags"])} undiagnosed die-offs)')
    write_if_changed('events.json', envelope(items, upstream=URL.format(1), credit=CREDIT, licence=LICENCE, window_days=WINDOW_DAYS,
                                             events_read=len(results)))


if __name__ == '__main__':
    main()
