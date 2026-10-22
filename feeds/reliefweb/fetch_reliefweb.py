#!/usr/bin/env python3
"""ReliefWeb API v2 epidemic disasters → data/disasters.json.

Upstream (gate verified 2026-10-22): https://api.reliefweb.int/v2/disasters needs a pre-approved
appname (403 otherwise: "Kindly request an appname from ReliefWeb"). Apply (free) at
https://apidoc.reliefweb.int/parameters#appname and set RELIEFWEB_APPNAME. v1 is decommissioned (410).

NOT YET RUN LIVE: the decoder follows the documented response shape
(data[].fields.{name, glide, status, date.created, date.changed, country[].{name, iso3}, primary_country, type[].name, url}).
Verify against the first real response and fix the decoder if a field differs.
"""
import json
import os
import urllib.request
from hf import write_if_changed, envelope, parse_date, log, fail, HFError, UA, now, first_sentence
import events, geocode, pathogens

BASE = 'https://api.reliefweb.int/v2/disasters'
CREDIT = 'ReliefWeb (OCHA); reports © their publishers'
LICENCE = 'ReliefWeb API terms: attribution'
BODY = {
    'filter': {'field': 'type.name', 'value': 'Epidemic'},
    'sort': ['date.changed:desc'],
    'limit': 60,
    'fields': {'include': ['name', 'glide', 'status', 'date', 'country', 'primary_country', 'type', 'url', 'description']},
}
SEVERITY = {'ongoing': events.MODERATE, 'alert': events.MINOR, 'past': events.INFO}


def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'User-Agent': UA, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise HFError(f'HTTP {e.code} {e.read()[:200].decode("utf-8", "replace")}') from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HFError(str(e)[:160]) from e


def decode(payload) -> list:
    out = []
    for d in payload.get('data', []):
        f = d.get('fields', {})
        name = f.get('name') or ''
        status = (f.get('status') or '').lower()
        dates = f.get('date') or {}
        created, changed = parse_date(dates.get('created')), parse_date(dates.get('changed'))
        countries = [c for c in (geocode.country((x.get('iso3') or '').upper()) for x in f.get('country', [])) if c]
        primary = f.get('primary_country') or {}
        pc = geocode.country((primary.get('iso3') or '').upper()) if primary else None
        pathogen = pathogens.primary(name) or pathogens.primary(f.get('description') or '')
        desc = f.get('description') or ''
        facts = [['Status', status or '—'], ['GLIDE', f.get('glide') or '—'], ['Countries', ', '.join(c['name'] for c in countries) or '—']]
        targets = countries if 0 < len(countries) <= 4 else ([pc] if pc else [None])
        for c in targets:
            out.append(events.make(
                source='ReliefWeb', source_tier='aggregator', kind='outbreak', severity=SEVERITY.get(status, events.INFO), title=name,
                headline=first_sentence(desc) if desc else None, body=events.clip(desc, 600), pathogen=pathogen,
                country_code=c['iso2'] if c else None, countries=[x['iso2'] for x in countries], updated=changed or created, onset=created,
                url=f.get('url'), facts=facts, credit=CREDIT, licence=LICENCE, tags=[f'status:{status}'] if status else [],
                source_id=f"{d.get('id')}|{c['iso2'] if c else ''}"))
    return out


def main():
    appname = os.environ.get('RELIEFWEB_APPNAME')
    if not appname:
        fail('RELIEFWEB_APPNAME is not set (apply at https://apidoc.reliefweb.int/parameters#appname)')
    try:
        payload = post_json(f'{BASE}?appname={appname}', BODY)
    except HFError as e:
        fail(str(e))
    items = decode(payload)
    log(f'ReliefWeb: {len(items)} epidemic events from {payload.get("count", "?")} disasters')
    write_if_changed('disasters.json', envelope(items, upstream=BASE, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
