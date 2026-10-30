#!/usr/bin/env python3
"""PAHO Epidemiological Alerts and Updates → data/alerts.json (title, date, pathogen, countries and link only).

Upstream (verified 2026-10-30): https://www.paho.org/en/epidemiological-alerts-and-updates, a Drupal list with no RSS; each
item is a `views-field-created` <time datetime=…> followed by a `views-field-title` link. PAHO's documents are
CC BY-NC-SA 3.0 IGO, so this feed is a reference layer: it reproduces no text (licence watch-list, PLAN §4.7).
Epidemiological Alert (a new regional risk) → MODERATE, Epidemiological Update → MINOR, a PHEIC note → SEVERE.
Region-wide items ("… in the Americas Region") have no single country and stay unmapped, tagged `regional`; a named
country is placed at its centroid, one event per country.
"""
import html
import re
from hf import get_text, write_if_changed, envelope, parse_date, log, fail, HFError
import events, geocode, pathogens

URL = 'https://www.paho.org/en/epidemiological-alerts-and-updates'
CREDIT = 'Pan American Health Organization (PAHO/WHO), Epidemiological Alerts and Updates'
LICENCE = 'PAHO documents CC BY-NC-SA 3.0 IGO; title, date and link only (no text reproduced)'
ITEM = re.compile(r'<time datetime="([^"]+)"[^>]*>.*?views-field-title.*?<a href="(/en/documents/[^"]+)"[^>]*>(.*?)</a>', re.S)
PREFIX = re.compile(r'^(?:epidemiological (?:alert|update)|public health emergency of international concern \(pheic\))\s*[:\-–]?\s*', re.I)
DATE_SUFFIX = re.compile(r'\s*[-–,]?\s*\d{1,2} \w+ \d{4}\s*$')


def decode(page: str, limit: int = 40) -> list:
    out = []
    for m in ITEM.finditer(page):
        when = parse_date(m.group(1))
        href = m.group(2)
        title = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', m.group(3)))).strip()
        if not title or not when:
            continue
        low = title.lower()
        if 'epidemiological alert' in low:
            severity, label = events.MODERATE, 'Epidemiological Alert'
        elif 'epidemiological update' in low:
            severity, label = events.MINOR, 'Epidemiological Update'
        elif 'public health emergency of international concern' in low or 'pheic' in low:
            severity, label = events.SEVERE, 'PHEIC'
        else:
            severity, label = events.INFO, 'Document'
        subject = DATE_SUFFIX.sub('', PREFIX.sub('', title)).strip(' .')
        pathogen = pathogens.primary(title)
        countries = geocode.find_countries(title)
        tags = ['americas'] + ([] if countries else ['regional'])
        for c in (countries or [None]):
            out.append(events.make(
                source='PAHO', source_tier='official', kind='alert', severity=severity, title=title, headline=subject,
                pathogen=pathogen, country_code=c['iso2'] if c else None, countries=[x['iso2'] for x in countries],
                updated=when, onset=when, url='https://www.paho.org' + href,
                facts=[['Type', label], ['Published', when.strftime('%d %b %Y')]], credit=CREDIT, licence=LICENCE,
                tags=tags, source_id=f"{href}|{c['iso2'] if c else ''}"))
        if len(out) >= limit:
            break
    return out


def main():
    try:
        page = get_text(URL, timeout=40)
    except HFError as e:
        fail(str(e))
    items = decode(page)
    if not items:
        fail('PAHO alerts page decoded to zero items (markup changed?)')
    log(f'PAHO: {len(items)} alert/update events')
    write_if_changed('alerts.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
