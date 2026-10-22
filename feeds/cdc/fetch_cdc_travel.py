#!/usr/bin/env python3
"""CDC Travel Health Notices → data/travel_notices.json.

Upstream (verified 2026-10-22): https://wwwnc.cdc.gov/travel/rss/notices.xml — RSS 2.0 served with a
text/html content type; parsed as XML regardless. Level and destination live only in the title
("Level 1 - Ebola Bundibugyo Virus Disease in Kenya"). US Government work, public domain.
"""
import re
from hf import get_xml, rss_items, write_if_changed, envelope, parse_date, strip_html, log, fail, HFError
import events, geocode, pathogens

URL = 'https://wwwnc.cdc.gov/travel/rss/notices.xml'
CREDIT = 'CDC Travelers’ Health, Travel Health Notices'
LICENCE = 'US Government work, public domain'
SEVERITY = {3: events.SEVERE, 2: events.MODERATE, 1: events.MINOR}


def decode(root) -> list:
    out = []
    for it in rss_items(root):
        title = it['title'] or ''
        m = re.match(r'\s*Level\s+(\d)\s*[-–—:]\s*(.*)$', title)
        level = int(m.group(1)) if m else None
        rest = m.group(2) if m else title
        dest = rest.rsplit(' in ', 1)[1].strip() if ' in ' in rest else None
        desc = strip_html(it['description'] or '')
        pathogen = pathogens.primary(rest) or pathogens.primary(desc)
        countries = geocode.find_countries(dest or '') or geocode.find_countries(rest) or geocode.find_countries(desc[:300])
        published = parse_date(it['pubDate'])
        facts = [['Level', str(level) if level else '—'], ['Destination', dest or '—']]
        targets = countries or [None]
        for c in targets:
            out.append(events.make(
                source='CDC Travel Health Notices', source_tier='official', kind='advisory',
                severity=SEVERITY.get(level, events.INFO), title=title, headline=desc.split('\n')[0][:240] or None,
                body=events.clip(desc, 600), pathogen=pathogen, country_code=c['iso2'] if c else None,
                area=dest if (c is None and dest) else None, countries=[x['iso2'] for x in countries],
                updated=published, onset=published, url=it['link'] or it['guid'], facts=facts,
                credit=CREDIT, licence=LICENCE, source_id=f"{it['guid'] or it['link']}|{c['iso2'] if c else ''}"))
    return out


def main():
    try:
        root = get_xml(URL, timeout=40, headers={'Accept': 'application/rss+xml, application/xml, text/xml, */*'})
    except HFError as e:
        fail(str(e))
    items = decode(root)
    if not items:
        fail('CDC travel notices decoded to zero items')
    log(f'CDC travel notices: {len(items)} events')
    write_if_changed('travel_notices.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
