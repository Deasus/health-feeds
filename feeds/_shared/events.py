"""The HealthEvent record (PLAN §4.4) and the rules every feed shares.

kind          outbreak | alert | advisory | recall | surveillance | news
pathogen      slug from pathogens.py ('unknown' when none named)
severity      0 INFO · 1 MINOR · 2 MODERATE · 3 SEVERE · 4 EXTREME
source_tier   official | aggregator | news
geo_precision point | admin1 | country | none
url           provenance only; the app never renders it as a link
"""
import re
from hf import iso, stable_id, iso_week, now, parse_date
import pathogens
import geocode

KINDS = ('outbreak', 'alert', 'advisory', 'recall', 'surveillance', 'news')
TIERS = ('official', 'aggregator', 'news')
PRECISION = ('point', 'admin1', 'country', 'none')
INFO, MINOR, MODERATE, SEVERE, EXTREME = 0, 1, 2, 3, 4
SEVERITY_LABEL = ['INFO', 'MINOR', 'MODERATE', 'SEVERE', 'EXTREME']


def make(*, source, source_tier, kind, title, updated, pathogen=None, severity=INFO, headline=None, body=None,
         instruction=None, area=None, country_code=None, admin1=None, admin1_code=None, lat=None, lng=None,
         geo_precision=None, cases=None, deaths=None, cases_quote=None, onset=None, expires=None, url=None,
         facts=None, credit=None, licence=None, countries=None, source_id=None, tags=None) -> dict:
    assert kind in KINDS, kind
    assert source_tier in TIERS, source_tier
    if isinstance(pathogen, str):
        pathogen = pathogens.BY_SLUG.get(pathogen) or pathogens.BY_SLUG['unknown']
    if pathogen is None:
        pathogen = pathogens.primary(f'{title} {headline or ""} {body or ""}') or pathogens.BY_SLUG['unknown']
    if geo_precision is None:
        geo_precision = 'point' if lat is not None and admin1 is None and country_code is None else \
                        'admin1' if admin1 else 'country' if country_code else 'none'
    if lat is None and country_code and geo_precision in ('country', 'admin1'):
        if admin1_code and country_code in ('US', 'CA', 'AU'):
            s = geocode.state(admin1_code, country_code)
            if s:
                lat, lng = s['lat'], s['lng']
        if lat is None:
            c = geocode.country(country_code)
            if c:
                lat, lng = c['lat'], c['lng']
                if geo_precision == 'admin1' and not admin1_code:
                    geo_precision = 'country'
    if area is None and country_code:
        c = geocode.country(country_code)
        if c:
            area = c['name'] if not admin1 else f'{admin1}, {c["name"]}'
    ev = {
        'id': stable_id(source, source_id or url or title),
        'kind': kind,
        'pathogen': pathogen['slug'],
        'pathogen_name': pathogen['name'],
        'pathogen_class': pathogen['cls'],
        'severity': int(severity),
        'severity_label': SEVERITY_LABEL[int(severity)],
        'source': source,
        'source_tier': source_tier,
        'title': title,
        'headline': headline,
        'body': body,
        'instruction': instruction,
        'area': area,
        'country_code': country_code,
        'countries': countries or ([country_code] if country_code else []),
        'admin1': admin1,
        'admin1_code': admin1_code,
        'lat': lat,
        'lng': lng,
        'geo_precision': geo_precision if lat is not None else 'none',
        'region': geocode.area_of(country_code, lng) if country_code else 'global',
        'cases': cases,
        'deaths': deaths,
        'cases_quote': cases_quote,
        'onset': iso(onset) if onset is not None and not isinstance(onset, str) else onset,
        'updated': iso(updated) if updated is not None and not isinstance(updated, str) else updated,
        'expires': iso(expires) if expires is not None and not isinstance(expires, str) else expires,
        'url': url,
        'facts': facts or [],
        'credit': credit,
        'licence': licence,
        'tags': tags or [],
        'corroboration': 0,
    }
    return ev


_CASES = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s+(?:laboratory[- ]confirmed |confirmed |suspected |probable |new |additional |cumulative |total )?'
                    r'(?:human )?(?:cases?|infections?|patients|people (?:have been |were )?(?:infected|sickened|hospitali[sz]ed))', re.I)
_DEATHS = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s+(?:deaths?|fatalities|people (?:have )?died|dead)\b|'
                     r'including\s+(\d{1,3}(?:,\d{3})+|\d+)\s+(?:deaths?|fatal)', re.I)
_WORDNUM = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20}


def counts_from_text(text: str):
    """(cases, deaths, quote) when the text states them verbatim; otherwise Nones. Conservative on purpose."""
    if not text:
        return None, None, None
    t = re.sub(r'\b(' + '|'.join(_WORDNUM) + r')\b', lambda m: str(_WORDNUM[m.group(1).lower()]), text, flags=re.I)
    cases = deaths = None
    quote = None
    for m in _CASES.finditer(t):
        n = int(m.group(1).replace(',', ''))
        if cases is None or n > cases:
            cases, quote = n, m.group(0)
    for d in _DEATHS.finditer(t):
        n = int((d.group(1) or d.group(2)).replace(',', ''))
        if deaths is None or n > deaths:
            deaths = n
            quote = quote or d.group(0)
    return cases, deaths, (quote.strip() if quote else None)


def dedupe_key(ev: dict) -> str:
    when = parse_date(ev.get('onset') or ev.get('updated')) or now()
    return f"{ev['pathogen']}|{ev.get('country_code') or ''}|{ev.get('admin1_code') or ''}|{iso_week(when)}"


def clip(s, n=1200):
    if not s:
        return s
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'
