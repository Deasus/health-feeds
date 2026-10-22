#!/usr/bin/env python3
"""WHO Disease Outbreak News → data/don.json (HealthEvents, one per country named in the title).

Upstream (verified 2026-10-22): https://www.who.int/api/news/diseaseoutbreaknews  (OData-style JSON,
default sort oldest-first, so we order explicitly). Fields used: Title, PublicationDate, Summary,
UrlName, DonId. No geography in the data: countries are read from the title (fallback: summary).
Licence: WHO terms — public-health use, attribution, no commercial promotion. We publish WHO's
own Summary paragraph (clipped) with credit; the app shows facts and credit, not full text.
"""
import sys
from hf import get_json, write_if_changed, envelope, parse_date, strip_html, first_sentence, log, fail, HFError
import events, geocode, pathogens

URL = 'https://www.who.int/api/news/diseaseoutbreaknews?$orderby=PublicationDate%20desc&$top=80'
ITEM_URL = 'https://www.who.int/emergencies/disease-outbreak-news/item/{}'
CREDIT = 'World Health Organization, Disease Outbreak News'
LICENCE = 'WHO terms of use (public-health use, attribution; no commercial promotion)'


def decode(payload: dict) -> list:
    out = []
    for v in payload.get('value', []):
        title = (v.get('Title') or '').strip()
        if not title:
            continue
        published = parse_date(v.get('PublicationDate'))
        summary = strip_html(v.get('Summary') or '')
        pathogen = pathogens.primary(title) or pathogens.primary(summary)
        countries = geocode.find_countries(title) or geocode.find_countries(summary[:400])
        cases, deaths, quote = events.counts_from_text(summary)
        url = ITEM_URL.format(v.get('UrlName') or '')
        facts = [['DON', v.get('DonId') or ''], ['Published', published.strftime('%d %b %Y') if published else '']]
        if cases is not None:
            facts.append(['Cases', f'{cases:,}'])
        if deaths is not None:
            facts.append(['Deaths', f'{deaths:,}'])
        targets = countries or [None]
        for c in targets:
            out.append(events.make(
                source='WHO DON', source_tier='official', kind='outbreak', severity=events.SEVERE,
                title=title, headline=first_sentence(summary), body=events.clip(summary, 700),
                pathogen=pathogen, country_code=c['iso2'] if c else None,
                countries=[x['iso2'] for x in countries], updated=published, onset=published,
                cases=cases, deaths=deaths, cases_quote=quote, url=url, facts=facts,
                credit=CREDIT, licence=LICENCE, source_id=f"{v.get('DonId')}|{c['iso2'] if c else ''}"))
    return out


def main():
    try:
        payload = get_json(URL, timeout=40)
    except HFError as e:
        fail(str(e))
    items = decode(payload)
    if not items:
        fail('WHO DON decoded to zero items')
    log(f'WHO DON: {len(items)} events from {len(payload.get("value", []))} notices')
    write_if_changed('don.json', envelope(items, upstream=URL, credit=CREDIT, licence=LICENCE))


if __name__ == '__main__':
    main()
