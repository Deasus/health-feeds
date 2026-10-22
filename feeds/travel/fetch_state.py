#!/usr/bin/env python3
"""US State Department travel advisories → data/state.json.

Upstream (verified 2026-10-22): https://travel.state.gov/_res/rss/TAsTWs.xml — every country's current
advisory (~220 items). <category domain="Threat-Level"> holds the level, <category domain="Country-Tag">
the ISO-2 code. The description's first paragraph lists the reasons in <b>…</b> ("due to <b>crime</b>,
<b>health</b>"). Public domain.

`advisories` carries every country (for the travel brief). `items` carries HealthEvents only for
advisories whose reasons include health.
"""
import re
from hf import get_xml, rss_items, write_if_changed, envelope, parse_date, strip_html, log, fail, HFError
import events, geocode, pathogens

URL = 'https://travel.state.gov/_res/rss/TAsTWs.xml'
CREDIT = 'US Department of State, Travel Advisories'
LICENCE = 'US Government work, public domain'
HEALTH = re.compile(r'health|disease|outbreak|medical care|epidemic', re.I)


def decode(root):
    advisories, items = [], []
    for it in rss_items(root):
        title = it['title'] or ''
        level = None
        iso2 = None
        for domain, text in it['categories']:
            if domain == 'Threat-Level':
                m = re.search(r'Level\s+(\d)', text or '')
                level = int(m.group(1)) if m else None
            elif domain == 'Country-Tag':
                iso2 = (text or '').strip().upper() or None
        if level is None:
            m = re.search(r'Level\s+(\d)', title)
            level = int(m.group(1)) if m else None
        html_desc = it['description'] or ''
        first_p = re.split(r'</p>', html_desc, 1)[0]
        reasons = [strip_html(b).strip(' .,') for b in re.findall(r'<b>(.*?)</b>', first_p, flags=re.S)]
        reasons = [r for r in reasons if r and len(r) < 80]
        country_name = title.split(' - ')[0].strip()
        if reasons and reasons[0].lower().startswith(country_name.lower()[:6]):
            reasons = reasons[1:]
        text = strip_html(html_desc)
        health = any(HEALTH.search(r) for r in reasons)
        cs = geocode.find_countries(country_name)                     # the title names the country
        c = cs[0] if cs else geocode.country_from_fips(iso2)           # the tag is FIPS 10-4, not ISO
        published = parse_date(it['pubDate'])
        adv = {'country': country_name, 'iso2': c['iso2'] if c else None, 'fips': iso2, 'level': level, 'title': title,
               'reasons': reasons, 'health': health, 'updated': published.strftime('%Y-%m-%d') if published else None,
               'url': it['link'] or it['guid']}
        advisories.append(adv)
        if not health:
            continue
        pathogen = pathogens.primary(text)
        sev = {4: events.SEVERE, 3: events.MODERATE, 2: events.MINOR}.get(level, events.INFO)
        flat = re.sub(r'\s+', ' ', text)
        health_sentence = next((s for s in re.split(r'(?<=[.!?])\s+', flat) if HEALTH.search(s) and len(s) < 400), None)
        items.append(events.make(
            source='US State Department', source_tier='official', kind='advisory', severity=sev, title=title,
            headline=health_sentence, body=events.clip(text, 500), pathogen=pathogen,
            country_code=c['iso2'] if c else None, updated=published, url=adv['url'],
            facts=[['Level', str(level) if level else '—'], ['Reasons', ', '.join(reasons) or '—']],
            credit=CREDIT, licence=LICENCE, tags=['travel-advisory'], source_id=adv['url']))
    return advisories, items


def main():
    try:
        root = get_xml(URL, timeout=90)
    except HFError as e:
        fail(str(e))
    advisories, items = decode(root)
    if len(advisories) < 100:
        fail(f'State Dept feed decoded to only {len(advisories)} advisories')
    log(f'State Dept: {len(advisories)} advisories, {len(items)} with a health reason')
    write_if_changed('state.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE, advisories=advisories))


if __name__ == '__main__':
    main()
