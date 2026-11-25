#!/usr/bin/env python3
"""National Response Center incident reports → data/incidents.json (the `nrc` feed).

Added 2026-11-25 with the owner's approval (PULSE Web, docs/PLAN.md §9 decision 7), from the draft written by PULSE Web's
lane F4 (PULSE-Web/pipeline-drafts/nrc/). PULSE Web's chemical layer reads
https://raw.githubusercontent.com/Deasus/health-feeds/nrc/incidents.json. Daily cadence in the conductor (the workbook is
updated about weekly): at most one download of this year's workbook a run (and last year's in January), sent with
If-Modified-Since, so an unchanged workbook is not downloaded again. On any failure (download, a missing sheet, no report
in the window) nothing is written and the run exits 1, so the published file is kept. County centroids come from the
pipeline's geocode.county (feeds/_shared/counties.json), city points from its GeoNames gazetteer (feeds/_shared/cities.json).
Not merged into `extract/events.json`: these are chemical and oil release reports, not HealthEvents (own item schema).

Upstream (read 2026-11-13; U.S. Coast Guard National Response Center):
  https://nrc.uscg.mil/FOIAFiles/CY<yy>.xlsx     one workbook per calendar year, updated about weekly (CY26: 11.2 MB,
                                                 Last-Modified Mon, 09 Nov 2026 17:28:02 GMT)
  https://nrc.uscg.mil/FOIAFiles/DataDictionary.xlsx   the field definitions (Last-Modified 18 Apr 2024)
Sheets used, linked by the report number SEQNOS: CALLS (received time), INCIDENT_COMMONS (what, where, the degrees /
minutes / seconds), INCIDENT_DETAILS (medium, injuries, evacuations, the reported duration and rate, the reported
weather), MATERIAL_INVOLVED (one row per material: name, CAS, UN, CHRIS code, amount, unit).
Terms as found on https://nrc.uscg.mil/ (2026-11-13): no licence text. The page says the NRC "is not a response agency",
that it "fields INITIAL reports", and that "The spreadsheets posted to the NRC website contain INITIAL incident data that
has not been validated or investigated by a federal/state response agency." Every row here is therefore REPORTED and
an unverified initial report, and says so.

What is kept: reports received in the last WINDOW_DAYS days that name a material, except railroad non-release reports
(trespassers, crossings: no material released). Not kept: the suspected responsible party (company, city, zip: it can be
a private person), the caller, every street address (LOCATION_ADDRESS, the street lines and the ZIP code: a report can be
a private home; 2026-11-25), a stated incident location that begins with a house number, and descriptions that look as
if they name a person, a phone number or an e-mail address.

Location: the stated degrees / minutes / seconds when they are complete and valid (precision `reported`); otherwise the
centroid of the stated county (US Census gazetteer, precision `county`, radius = half the square root of its area);
otherwise the stated nearest city (the pipeline's GeoNames gazetteer, cities >= 50,000 and capitals, CC BY 4.0, precision
`city`); otherwise none (listed, not mapped).

Times: the workbook gives the received time as an Excel serial and the incident time as text; neither states a time
zone. Both are kept as written (ISO form, no zone) and the file says so.

Python 3.9+, standard library only (zipfile + xml.etree read the .xlsx).
  python3 fetch_nrc.py                                                  # the conductor: data/incidents.json
  python3 fetch_nrc.py --xlsx CY26.xlsx --now 2026-11-13T21:00:00Z --out incidents.json     # from a saved workbook
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import unicodedata
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

URL = 'https://nrc.uscg.mil/FOIAFiles/CY{yy:02d}.xlsx'
SITE = 'https://nrc.uscg.mil/'
CREDIT = 'U.S. Coast Guard National Response Center (NRC), FOIA incident files'
TERMS = ('No licence text on nrc.uscg.mil (read 2026-11-13). The site: "The spreadsheets posted to the NRC website contain '
         'INITIAL incident data that has not been validated or investigated by a federal/state response agency."')
UA = 'health-feeds/1.0 (+https://github.com/Deasus/health-feeds)'
WINDOW_DAYS = 30
DESC_MAX = 280
NON_RELEASE_TYPES = {'RAILROAD NON-RELEASE'}
NON_RELEASE_MEDIA = {'RAIL REPORT (N/A)', 'NON-RELEASE (N/A)'}
# A stated incident location that begins with a house number is a street address: left out (a report can be a private home).
HOUSE_NUMBER = re.compile(r'^\s*\d+[A-Z]?\s+\S', re.I)
# A description that may name a person, or carries a phone number or an e-mail address, is left out (not redacted in place).
PERSONAL = re.compile(r'\b(MR|MRS|MS|MISS|DR)\.?\s+[A-Z]{2,}|\b\d{3}[-. )]\s?\d{3}[-.]\d{4}\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.I)

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
HERE = os.path.dirname(os.path.abspath(__file__))

STATES = {'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut',
          'DE': 'Delaware', 'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
          'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
          'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana',
          'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
          'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania',
          'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
          'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
          'PR': 'Puerto Rico', 'VI': 'United States Virgin Islands', 'GU': 'Guam', 'AS': 'American Samoa', 'MP': 'Northern Mariana Islands'}


# ── .xlsx with the standard library ───────────────────────────────────────────

def _col(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(z):
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    out = []
    for _, el in ET.iterparse(z.open('xl/sharedStrings.xml'), events=('end',)):
        if el.tag == NS + 'si':
            out.append(''.join(t.text or '' for t in el.iter(NS + 't')))
            el.clear()
    return out


def _sheet_paths(z):
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target = {r.get('Id'): r.get('Target') for r in rels}
    out = {}
    for s in wb.iter(NS + 'sheet'):
        t = target[s.get(RNS + 'id')].lstrip('/')
        out[s.get('name')] = t if t.startswith('xl/') else 'xl/' + t
    return out


def _rows(z, path, sst):
    for _, el in ET.iterparse(z.open(path), events=('end',)):
        if el.tag != NS + 'row':
            continue
        vals = {}
        for c in el.iter(NS + 'c'):
            t, v = c.get('t'), c.find(NS + 'v')
            if t == 's' and v is not None:
                val = sst[int(v.text)]
            elif t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter(NS + 't'))
            else:
                val = v.text if v is not None else None
            vals[_col(c.get('r'))] = val
        el.clear()
        yield [vals.get(i) for i in range(max(vals) + 1)] if vals else []


def read_workbook(blob, sheets=('CALLS', 'INCIDENT_COMMONS', 'INCIDENT_DETAILS', 'MATERIAL_INVOLVED')):
    """The named sheets as lists of dicts keyed by the header row (header names stripped)."""
    z = zipfile.ZipFile(io.BytesIO(blob) if isinstance(blob, (bytes, bytearray)) else blob)
    sst, paths, out = _shared_strings(z), _sheet_paths(z), {}
    for name in sheets:
        if name not in paths:
            raise ValueError(f'sheet {name} missing from the workbook')
        it = _rows(z, paths[name], sst)
        head = [str(h or '').strip() for h in next(it, [])]
        out[name] = [dict(zip(head, r + [None] * (len(head) - len(r)))) for r in it if any(x not in (None, '') for x in r)]
    return out


# ── field rules ───────────────────────────────────────────────────────────────

def s(v, n=None):
    """A text cell, trimmed; None when empty."""
    if v is None:
        return None
    t = re.sub(r'\s+', ' ', str(v)).strip()
    if not t:
        return None
    return t[:n] if n else t


def num(v):
    try:
        x = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return int(x) if x.is_integer() else x


def excel_time(v):
    """An Excel serial (days since 1899-12-30) → 'YYYY-MM-DDTHH:MM' (no zone: the file states none)."""
    x = num(v)
    if x is None:
        return None
    d = datetime(1899, 12, 30) + timedelta(days=float(x))
    d = d + timedelta(seconds=30)   # round to the minute
    return d.strftime('%Y-%m-%dT%H:%M')


def text_time(v):
    """'11/8/2026 18:50' (or an Excel serial) → '2026-11-08T18:50'."""
    if v is None:
        return None
    t = str(v).strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?', t)
    if m:
        mo, da, yr, hh, mi = m.groups()
        return f'{int(yr):04d}-{int(mo):02d}-{int(da):02d}T{int(hh or 0):02d}:{int(mi or 0):02d}'
    return excel_time(t)


def dms(deg, mins, secs, quad, kind):
    """Degrees, minutes, seconds and a quadrant (N/S, E/W) → signed decimal degrees, or None when incomplete or invalid.
    Minutes and seconds must be in [0, 60); latitude within 90, longitude within 180; the quadrant must be stated."""
    d, m, sec = num(deg), num(mins), num(secs)
    q = (s(quad) or '').upper()[:1]
    if d is None or q not in (('N', 'S') if kind == 'lat' else ('E', 'W')):
        return None
    m = 0 if m is None else m
    sec = 0 if sec is None else sec
    if d < 0 or not (0 <= m < 60) or not (0 <= sec < 60):
        return None
    v = d + m / 60.0 + sec / 3600.0
    if v > (90 if kind == 'lat' else 180):
        return None
    return round(-v if q in ('S', 'W') else v, 5)


def cas(v):
    """'007664-41-7' → '7664-41-7'; the file's placeholder 000000-00-0 → None."""
    t = s(v)
    if not t or not re.match(r'^\d+-\d{2}-\d$', t):
        return None
    head, mid, tail = t.split('-')
    head = head.lstrip('0')
    return f'{head}-{mid}-{tail}' if head else None


def yn(v):
    t = (s(v) or '').upper()[:1]
    return t if t in ('Y', 'N', 'U') else None


def _norm_key(name):
    """geocode._county_key in health-feeds (feeds/_shared/geocode.py), copied so the draft runs on its own."""
    t = unicodedata.normalize('NFKD', name or '')
    t = ''.join(ch for ch in t if not unicodedata.combining(ch)).lower().strip()
    t = re.sub(r'\bst\.\s*', 'st ', t)
    t = re.sub(r'\bsaint\s+', 'st ', t)
    t = re.sub(r'\s+(county|parish|borough|census area|municipality|municipio|city and borough)$', '', t)
    t = re.sub(r"[^a-z0-9' ]", ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


class Places:
    """County centroids (Census gazetteer: geocode.county, else counties.json) and US city points (cities.json: the pipeline's
    GeoNames gazetteer, whose US rows carry the state's postal code as admin1; a Natural Earth table with state names also
    reads)."""

    def __init__(self, counties_path=None, cities_path=None):
        self.counties, self.cities = {}, {}
        try:   # inside health-feeds: the pipeline's own geocoder
            import geocode   # noqa: F401
            self._geocode = geocode
        except ImportError:
            self._geocode = None
        path = counties_path or os.path.join(HERE, 'counties.json')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                table = json.load(f)['counties']
            for st, rows in table.items():
                for name, fips, lat, lng, radius in rows:
                    self.counties.setdefault((st, _norm_key(name)), {'name': name, 'state': st, 'fips': fips, 'lat': lat, 'lng': lng, 'radius_km': radius})
        cities_path = cities_path or os.path.join(HERE, 'cities.json')
        if os.path.exists(cities_path):
            with open(cities_path, encoding='utf-8') as f:
                c = json.load(f)
            fi = {k: i for i, k in enumerate(c['fields'])}
            for r in c['rows']:
                if r[fi['country']] != 'US':
                    continue
                key = (r[fi['admin1']], r[fi['name']].lower())
                self.cities.setdefault(key, {'name': r[fi['name']], 'lat': r[fi['lat']], 'lng': r[fi['lng']]})
                if r[fi['admin1']] in STATES:                                   # postal code → also under the state's name
                    self.cities.setdefault((STATES[r[fi['admin1']]], r[fi['name']].lower()), self.cities[key])

    def county(self, name, st):
        if not name or not st:
            return None
        if self._geocode is not None:
            return self._geocode.county(name, st)
        key = _norm_key(name)
        hit = self.counties.get((st, key))
        if hit is None and key.endswith(' city'):
            hit = self.counties.get((st, key[:-5]))
        if hit is None:
            hit = self.counties.get((st, key + ' city'))
        return hit

    def city(self, name, st):
        if not name or st not in STATES:
            return None
        return self.cities.get((STATES[st], name.strip().lower()))


def distance_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def locate(c, places):
    """The point for a report and how far to trust it: {lat, lng, precision, radius_km, dms, county_check_km}."""
    st = (s(c.get('LOCATION_STATE')) or '').upper()
    lat = dms(c.get('LAT_DEG'), c.get('LAT_MIN'), c.get('LAT_SEC'), c.get('LAT_QUAD'), 'lat')
    lng = dms(c.get('LONG_DEG'), c.get('LONG_MIN'), c.get('LONG_SEC'), c.get('LONG_QUAD'), 'lng')
    raw = None
    if s(c.get('LAT_DEG')) or s(c.get('LONG_DEG')):
        raw = ' '.join(x for x in [s(c.get('LAT_DEG')), s(c.get('LAT_MIN')), s(c.get('LAT_SEC')), s(c.get('LAT_QUAD'))] if x) + ', ' + \
              ' '.join(x for x in [s(c.get('LONG_DEG')), s(c.get('LONG_MIN')), s(c.get('LONG_SEC')), s(c.get('LONG_QUAD'))] if x)
    # Washington DC has one county-equivalent; Connecticut's eight old counties are not in the 2025 gazetteer (it lists the
    # planning regions that replaced them in 2022), so those reports fall back to the city, else stay unmapped.
    cty = places.county('District of Columbia' if st == 'DC' else s(c.get('LOCATION_COUNTY')), st)
    if lat is not None and lng is not None and not (lat == 0 and lng == 0):
        out = {'lat': lat, 'lng': lng, 'precision': 'reported', 'radius_km': None, 'dms': raw}
        if cty:
            out['county_check_km'] = round(distance_km((lat, lng), (cty['lat'], cty['lng'])), 1)
        return out
    if cty:
        return {'lat': cty['lat'], 'lng': cty['lng'], 'precision': 'county', 'radius_km': cty['radius_km'], 'dms': raw}
    city = places.city(s(c.get('LOCATION_NEAREST_CITY')), st)
    if city:
        return {'lat': city['lat'], 'lng': city['lng'], 'precision': 'city', 'radius_km': 15, 'dms': raw}
    return {'lat': None, 'lng': None, 'precision': 'none', 'radius_km': None, 'dms': raw}


def location_text(c):
    """The stated incident location ("REFINERY", "PACIFIC OCEAN", "MELISSA DRIVE & RANGE LINE STREET"), never a street
    address: LOCATION_ADDRESS is not read, and a stated location that begins with a house number is left out."""
    t = s(c.get('INCIDENT_LOCATION'), 120)
    return None if t and HOUSE_NUMBER.match(t) else t


def material(m):
    amount = num(m.get('AMOUNT_OF_MATERIAL'))
    unit = s(m.get('UNIT_OF_MEASURE'))
    if unit == 'UNKNOWN AMOUNT' or (amount == 0 and unit in (None, 'UNKNOWN AMOUNT')):
        amount = None
    return {'name': s(m.get('NAME_OF_MATERIAL'), 120), 'cas': cas(m.get('CAS_NUMBER')), 'un': s(m.get('UN_NUMBER'), 12),
            'chris': s(m.get('CHRIS_CODE'), 8), 'amount': amount, 'unit': unit, 'reached_water': s(m.get('IF_REACHED_WATER'), 12)}


def prune(x):
    """Drop empty fields (None, empty objects) so the file stays small; a missing key reads as 'not stated'.
    `materials` is kept as a list; the location keys (lat, lng, precision) are always kept."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            v = prune(v)
            if v is None or v == {}:
                if k in ('lat', 'lng', 'precision'):
                    out[k] = v
                continue
            out[k] = v
        return out
    if isinstance(x, list):
        return [prune(v) for v in x]
    return x


def decode(book, now, places, days=WINDOW_DAYS):
    """The workbook's sheets → incident items received in the last `days` days, newest first, plus counts."""
    calls = {s(r.get('SEQNOS')): r for r in book['CALLS']}
    details = {s(r.get('SEQNOS')): r for r in book['INCIDENT_DETAILS']}
    mats = {}
    for m in book['MATERIAL_INVOLVED']:
        mats.setdefault(s(m.get('SEQNOS')), []).append(m)
    lo = (now - timedelta(days=days)).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M')
    hi = now.replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M')
    counts = {'reports': len(book['INCIDENT_COMMONS']), 'in_window': 0, 'non_release': 0, 'no_material': 0, 'kept': 0,
              'precision': {'reported': 0, 'county': 0, 'city': 0, 'none': 0}, 'descriptions_left_out': 0, 'invalid_dms': 0}
    items = []
    for c in book['INCIDENT_COMMONS']:
        sid = s(c.get('SEQNOS'))
        call = calls.get(sid) or {}
        received = excel_time(call.get('DATE_TIME_RECEIVED')) or text_time(call.get('DATE_TIME_RECEIVED'))
        if not received or not (lo <= received <= hi):
            continue
        counts['in_window'] += 1
        d = details.get(sid) or {}
        medium = s(d.get('MEDIUM_DESC'))
        if s(c.get('TYPE_OF_INCIDENT')) in NON_RELEASE_TYPES or medium in NON_RELEASE_MEDIA:
            counts['non_release'] += 1
            continue
        ms = [material(m) for m in mats.get(sid, [])]
        ms = [m for m in ms if m['name']]
        if not ms:
            counts['no_material'] += 1
            continue
        loc = locate(c, places)
        if loc['dms'] and loc['precision'] != 'reported':
            counts['invalid_dms'] += 1
        counts['precision'][loc['precision']] += 1
        desc = s(c.get('DESCRIPTION_OF_INCIDENT'))
        if desc and PERSONAL.search(desc):
            desc = None
            counts['descriptions_left_out'] += 1
        elif desc and len(desc) > DESC_MAX:
            desc = desc[:DESC_MAX - 1].rstrip() + '…'
        item = {
            'id': sid, 'received': received, 'time': text_time(c.get('INCIDENT_DATE_TIME')), 'time_kind': s(c.get('INCIDENT_DTG'), 12),
            'type': s(c.get('TYPE_OF_INCIDENT'), 40), 'cause': s(c.get('INCIDENT_CAUSE'), 60),
            'medium': medium, 'medium_info': s(d.get('ADDITIONAL_MEDIUM_INFO'), 120),
            'materials': ms,
            'location': location_text(c),
            'city': s(c.get('LOCATION_NEAREST_CITY'), 60), 'county': s(c.get('LOCATION_COUNTY'), 60), 'state': s(c.get('LOCATION_STATE'), 4),
            'lat': loc['lat'], 'lng': loc['lng'], 'precision': loc['precision'], 'radius_km': loc['radius_km'],
            'injuries': {'any': yn(d.get('ANY_INJURIES')), 'injured': num(d.get('NUMBER_INJURED')), 'hospitalized': num(d.get('NUMBER_HOSPITALIZED'))},
            'fatalities': {'any': yn(d.get('ANY_FATALITIES')), 'n': num(d.get('NUMBER_FATALITIES'))},
            'evacuations': {'any': yn(d.get('ANY_EVACUATIONS')), 'n': num(d.get('NUMBER_EVACUATED')), 'who': s(d.get('WHO_EVACUATED'), 60),
                            'radius': s(d.get('RADIUS_OF_EVACUATION'), 40)},
            'fire': yn(d.get('FIRE_INVOLVED')),
            'release': {'secured': yn(d.get('RELEASE_SECURED')), 'duration': num(d.get('ESTIMATED_DURATION_OF_RELEASE')), 'duration_unit': s(d.get('DURATION_UNIT'), 20),
                        'rate': num(d.get('RELEASE_RATE')), 'rate_unit': s(d.get('RELEASE_RATE_UNIT'), 20), 'rate_per': s(d.get('RELEASE_RATE_RATE'), 20)},
            'weather': {'conditions': s(d.get('WEATHER_CONDITIONS'), 40), 'air_temp': s(d.get('AIR_TEMPERATURE'), 12),
                        'wind_speed': s(d.get('WIND_SPEED'), 12), 'wind_speed_unit': s(d.get('WIND_SPEED_UNIT'), 12), 'wind_direction': s(d.get('WIND_DIRECTION'), 12)},
            'description': desc,
        }
        if loc.get('dms'):
            item['dms'] = loc['dms']
        if loc.get('county_check_km') is not None:
            item['county_check_km'] = loc['county_check_km']
        items.append(prune(item))
    items.sort(key=lambda x: (x['received'] or '', x['id'] or ''), reverse=True)
    counts['kept'] = len(items)
    return items, counts


def envelope(items, counts, *, now, sources, days):
    return {
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'upstream': ' · '.join(x['url'] for x in sources), 'site': SITE,
        'credit': CREDIT, 'licence': TERMS, 'kind': 'REPORTED',
        'caveat': 'Initial reports to the NRC, not validated or investigated by a response agency. Times as written in the file (no time zone stated).',
        'window_days': days, 'window': {'from': (now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M'), 'to': now.strftime('%Y-%m-%dT%H:%M')},
        'sources': sources, 'newest_received': items[0]['received'] if items else None, 'counts': counts,
        'geocoder': {'reported': 'the stated degrees / minutes / seconds', 'county': 'US Census Bureau gazetteer county internal point (public domain); radius = half the square root of the county area',
                     'city': 'Natural Earth populated places (public domain); radius 15 km', 'none': 'not mapped'},
        'count': len(items), 'items': items,
    }


def download(url, since=None):
    """(bytes, Last-Modified), or (None, since) when the server says 304 Not Modified. Raises OSError on any failure."""
    h = {'User-Agent': UA}
    if since:
        h['If-Modified-Since'] = since
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
            return r.read(), r.headers.get('Last-Modified')
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, since
        raise


def fail(msg):
    """Nothing is written: the conductor keeps the published file and records the failure."""
    print(f'FAILED: {msg} (keeping the previous file)', file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--xlsx', action='append', help='read this workbook instead of downloading (repeatable)')
    ap.add_argument('--out', default=os.path.join(os.getcwd(), 'data', 'incidents.json'))
    ap.add_argument('--now', help='the reference time, ISO (default: now, UTC)')
    ap.add_argument('--days', type=int, default=WINDOW_DAYS)
    ap.add_argument('--counties', default=None)
    ap.add_argument('--cities', default=None)
    ap.add_argument('--last-modified', default=None, help='the Last-Modified header seen when a local --xlsx was downloaded')
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now.replace('Z', '+00:00')) if a.now else datetime.now(timezone.utc)
    places = Places(a.counties, a.cities)
    sources, book = [], {'CALLS': [], 'INCIDENT_COMMONS': [], 'INCIDENT_DETAILS': [], 'MATERIAL_INVOLVED': []}
    blobs = []
    if a.xlsx:
        for p in a.xlsx:
            with open(p, 'rb') as f:
                blob = f.read()
            blobs.append((os.path.basename(p), blob, None))
    else:
        years = sorted({now.year, (now - timedelta(days=a.days)).year})
        prev = None
        if os.path.exists(a.out):
            try:
                with open(a.out, encoding='utf-8') as f:
                    prev = json.load(f)
            except ValueError:
                prev = None
        urls = [URL.format(yy=y % 100) for y in years]
        prev_urls = {x.get('url') for x in (prev or {}).get('sources', [])}
        try:
            got = []
            for url in urls:
                since = next((x.get('last_modified') for x in prev.get('sources', []) if x.get('url') == url), None) \
                    if prev and set(urls) == prev_urls else None            # a new set of years: download them all
                got.append((url,) + download(url, since))
            if all(blob is None for _, blob, _ in got):
                print(f'{", ".join(urls)}: not modified since the previous run; keeping {a.out}')
                return 0
            for url, blob, lm in got:                                   # one changed, one not (January): fetch both whole
                if blob is None:
                    blob, lm = download(url)
                blobs.append((url, blob, lm))
        except (OSError, ValueError) as e:                              # urllib errors are OSErrors
            return fail(f'download: {str(e)[:160]}')
    for name, blob, lm in blobs:
        try:
            part = read_workbook(blob)
        except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as e:
            return fail(f'{os.path.basename(name)} is not the workbook expected: {str(e)[:160]}')
        for k in book:
            book[k].extend(part[k])
        yy = re.search(r'CY(\d\d)\.xlsx$', name)
        url = name if name.startswith('http') else (URL.format(yy=int(yy.group(1))) if yy else name)
        sources.append({'url': url, 'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest(),
                        'last_modified': lm or a.last_modified})
    items, counts = decode(book, now, places, a.days)
    if counts['in_window'] == 0:
        return fail(f'no report received in the last {a.days} days ({counts["reports"]} in the workbook)')
    env = envelope(items, counts, now=now, sources=sources, days=a.days)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(env, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, a.out)
    print(f'{a.out}: {len(items)} reports ({counts}), {os.path.getsize(a.out) // 1024} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
