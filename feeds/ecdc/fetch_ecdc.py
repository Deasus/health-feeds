#!/usr/bin/env python3
"""ECDC Communicable Disease Threats Report (weekly) → data/cdtr.json.

Upstream (verified 2026-10-22): https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed — one RSS item
per weekly report; the description lists the topics covered. CC BY 4.0. One surveillance event per
report (world, unmapped) with the topics as facts and every pathogen named as a tag, so the SITREP
can say "ECDC's week-42 report covers Ebola, mpox, dengue…".
"""
import re
from hf import get_xml, rss_items, write_if_changed, envelope, parse_date, strip_html, log, fail, HFError
import events, pathogens

URL = 'https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed'
CREDIT = 'European Centre for Disease Prevention and Control (ECDC), Communicable disease threats report'
LICENCE = 'CC BY 4.0'


def topics_of(desc: str) -> list:
    m = re.search(r'includes? updates? on (.+?)\.?\s*$', desc, flags=re.I | re.S)
    if not m:
        return []
    raw = re.split(r',\s*|\s+and\s+', m.group(1))
    return [t.strip(' .') for t in raw if t.strip(' .') and len(t.strip()) < 90]


def decode(root) -> list:
    out = []
    for it in rss_items(root)[:8]:
        title = it['title'] or ''
        desc = strip_html(it['description'] or '')
        topics = topics_of(desc)
        tags = sorted({p['slug'] for p in pathogens.find(' | '.join(topics) or desc)})
        published = parse_date(it['pubDate'])
        wk = re.search(r'week\s+(\d{1,2})', title, re.I)
        out.append(events.make(
            source='ECDC CDTR', source_tier='official', kind='surveillance', severity=events.INFO, title=title,
            headline=f'Topics: {", ".join(topics)}.' if topics else desc[:240], body=events.clip(desc, 600),
            pathogen=pathogens.BY_SLUG['unknown'], updated=published, onset=published, url=it['link'] or it['guid'],
            facts=[['Week', wk.group(1) if wk else '—'], ['Topics', str(len(topics))]] + [['Topic', t] for t in topics],
            credit=CREDIT, licence=LICENCE, tags=tags, source_id=it['guid'] or it['link']))
    return out


def main():
    try:
        root = get_xml(URL, timeout=40)
    except HFError as e:
        fail(str(e))
    items = decode(root)
    if not items:
        fail('ECDC CDTR decoded to zero items')
    log(f'ECDC CDTR: {len(items)} weekly reports')
    write_if_changed('cdtr.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
