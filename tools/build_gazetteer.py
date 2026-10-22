#!/usr/bin/env python3
"""Build feeds/_shared/cities.json from GeoNames cities15000.txt (CC BY 4.0, https://www.geonames.org).
Keeps cities with population >= 50,000 plus every national capital: name, ascii name (when different), country,
admin1 code, lat, lng, population, capital flag. Run from the repo root:
  python3 tools/build_gazetteer.py path/to/cities15000.txt"""
import json, os, sys
from datetime import date
src = sys.argv[1]
rows = []
with open(src, encoding='utf-8') as f:
    for line in f:
        p = line.rstrip('\n').split('\t')
        if len(p) < 15:
            continue
        _, name, ascii_, _alt, lat, lng, _fc, fcode, cc, _cc2, a1, _a2, _a3, _a4, pop = p[:15]
        pop = int(pop or 0)
        if pop >= 50000 or fcode == 'PPLC':
            rows.append([name, ascii_ if ascii_ != name else '', cc, a1, round(float(lat), 3), round(float(lng), 3), pop, 1 if fcode == 'PPLC' else 0])
rows.sort(key=lambda r: -r[6])
out = os.path.join(os.path.dirname(__file__), '..', 'feeds', '_shared', 'cities.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump({'source': f'GeoNames cities15000 (CC BY 4.0), population >= 50,000 plus capitals; built {date.today()}',
               'fields': ['name', 'ascii', 'country', 'admin1', 'lat', 'lng', 'population', 'capital'], 'rows': rows},
              f, ensure_ascii=False, separators=(',', ':'))
print(f'{len(rows)} cities → {os.path.relpath(out)} ({os.path.getsize(out)//1024} KB)')
