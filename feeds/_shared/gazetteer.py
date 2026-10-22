"""City-level place finding for the pipeline (GeoNames cities >= 50k + capitals, CC BY 4.0; see cities.json).

find_places(text, country_hint) returns the cities named in the text, longest match first, resolving ambiguity by
the country hint, then capital status, then population. Common English words that are also city names (Mobile,
Nice, Reading, Orange…) only count when a country hint confirms them. Precision for a city hit is 'point' with a
~15 km radius; callers decide whether to trust it (news needs corroboration first).
"""
import json
import os
import re
from collections import defaultdict

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cities.json'), encoding='utf-8') as _f:
    _DATA = json.load(_f)
ROWS = _DATA['rows']
SOURCE = _DATA['source']
CITY_RADIUS_KM = 15.0

_BY_NAME = defaultdict(list)
for _i, _r in enumerate(ROWS):
    for _n in {_r[0].lower(), (_r[1] or _r[0]).lower()}:
        if len(_n) >= 4:
            _BY_NAME[_n].append(_i)
try:
    from centroids import COUNTRIES as _CTRY
    _COUNTRY_NAMES = {c[1]: c[0] for c in _CTRY}
except ImportError:                                    # tests importing the module alone
    _COUNTRY_NAMES = {}
_RX = re.compile(r'(?<![\w])(' + '|'.join(re.escape(n) for n in sorted(_BY_NAME, key=len, reverse=True)) + r')(?![\w])', re.I)

# City names that are also ordinary words or people's names: need a country hint to count.
STOP = {'mobile', 'nice', 'bath', 'reading', 'orange', 'surprise', 'independence', 'normal', 'commerce', 'enterprise', 'union',
        'liberty', 'victoria', 'male', 'split', 'most', 'bar', 'van', 'sale', 'battle', 'deal', 'march', 'along', 'man', 'ede',
        'george', 'jackson', 'madison', 'lincoln', 'washington', 'columbus', 'austin', 'phoenix', 'eugene', 'tyler', 'warren',
        'jordan', 'chad', 'georgia', 'florence', 'hamilton', 'nelson', 'kent', 'lima', 'santa', 'salem', 'stanley', 'york',
        'san', 'la', 'los', 'las', 'de', 'del', 'the', 'east', 'west', 'north', 'south', 'central', 'new', 'old', 'bay', 'hill',
        'valley', 'spring', 'springs', 'mountain', 'river', 'lake', 'beach', 'park', 'city', 'town', 'home', 'care', 'health',
        'hospital', 'general', 'major', 'minor', 'golden', 'silver', 'rose', 'lily', 'may', 'june', 'august', 'dover',
        'date', 'level', 'travel', 'alert', 'notice', 'global', 'world', 'national', 'state', 'county', 'district', 'province',
        'region', 'border', 'crossing', 'camp', 'base', 'post', 'field', 'ward', 'zone', 'area', 'island', 'islands'}


def find_places(text, country_hint=None, limit=3):
    """Cities named in text → [{name, country, admin1, lat, lng, population, capital}] (deduped, first-seen order)."""
    out, seen = [], set()
    if not text:
        return out
    hint = country_hint.upper() if country_hint else None
    for m in _RX.finditer(text):
        key = m.group(1).lower()
        cands = [ROWS[i] for i in _BY_NAME.get(key, [])]
        if not cands:
            continue
        if hint:
            cands = [c for c in cands if c[2] == hint]      # with a known country, never place in another one
            if not cands:
                continue
        elif key in STOP:
            continue
        best = max(cands, key=lambda c: (c[7], c[6]))
        if not hint and len(cands) > 1 and best[6] < 100000:
            continue                                   # a small town sharing a name with others: too ambiguous
        if _COUNTRY_NAMES.get(best[2], '').lower() == best[0].lower():
            continue                                   # 'Jamaica', 'Mozambique', 'Singapore' as a town: the country already says it
        if best[0] in seen:
            continue
        seen.add(best[0])
        out.append({'name': best[0], 'country': best[2], 'admin1': best[3], 'lat': best[4], 'lng': best[5],
                    'population': best[6], 'capital': bool(best[7])})
        if len(out) >= limit:
            break
    return out


def city(name, country_hint=None):
    """One city by exact name (case-insensitive), preferring the hinted country."""
    cands = [ROWS[i] for i in _BY_NAME.get((name or '').lower(), [])]
    if country_hint:
        hinted = [c for c in cands if c[2] == country_hint.upper()]
        cands = hinted or cands
    if not cands:
        return None
    b = max(cands, key=lambda c: (c[7], c[6]))
    return {'name': b[0], 'country': b[2], 'admin1': b[3], 'lat': b[4], 'lng': b[5], 'population': b[6], 'capital': bool(b[7])}
