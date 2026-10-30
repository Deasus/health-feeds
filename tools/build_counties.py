#!/usr/bin/env python3
"""Build feeds/_shared/counties.json from the US Census Bureau's national county gazetteer (public domain).

Source: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/<year>_Gazetteer/<year>_Gaz_counties_national.zip
(pipe-separated: USPS, GEOID, GEOIDFQ, ANSICODE, NAME, ALAND, AWATER, ALAND_SQMI, AWATER_SQMI, INTPTLAT, INTPTLONG).
One row per county, parish, borough, census area, municipio or independent city (3,222 in 2025). The internal point
is the county's centroid; radius_km is half the square root of its land + water area, the "how far from the drawn point
the truth may lie" figure the app draws as a precision ring (geo_radius_km).

Run from the pipeline root:  python3 tools/build_counties.py [year]
"""
import io
import json
import math
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

YEAR = sys.argv[1] if len(sys.argv) > 1 else '2025'
URL = f'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{YEAR}_Gazetteer/{YEAR}_Gaz_counties_national.zip'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'feeds', '_shared', 'counties.json')


def main():
    with urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'health-feeds/1.0'}), timeout=60) as r:
        blob = r.read()
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = [n for n in z.namelist() if n.endswith('.txt')][0]
    text = z.read(name).decode('utf-8-sig', 'replace')
    lines = [ln.rstrip('\r\n') for ln in text.split('\n') if ln.strip()]
    header = [h.strip() for h in lines[0].split('|')]
    idx = {h: i for i, h in enumerate(header)}
    by_state = {}
    for ln in lines[1:]:
        cols = [c.strip() for c in ln.split('|')]
        if len(cols) < len(header):
            continue
        st = cols[idx['USPS']]
        area_km2 = (float(cols[idx['ALAND']]) + float(cols[idx['AWATER']])) / 1e6
        radius = round(math.sqrt(max(area_km2, 1.0)) / 2, 1)
        by_state.setdefault(st, []).append([cols[idx['NAME']], cols[idx['GEOID']],
                                            round(float(cols[idx['INTPTLAT']]), 4), round(float(cols[idx['INTPTLONG']]), 4), radius])
    n = sum(len(v) for v in by_state.values())
    out = {'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': URL,
           'credit': 'US Census Bureau, Gazetteer Files (counties)', 'licence': 'US Government work, public domain',
           'count': n, 'fields': ['name', 'fips', 'lat', 'lng', 'radius_km'], 'counties': by_state}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'{OUT}: {n} counties in {len(by_state)} states/territories, {os.path.getsize(OUT) // 1024} KB')


if __name__ == '__main__':
    main()
