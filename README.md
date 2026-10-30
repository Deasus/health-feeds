# health-feeds

Scheduled mirrors of public outbreak, health-alert and travel-health feeds, normalised to one event
schema and served from `raw.githubusercontent.com` (CORS-open). Clients read JSON here instead of
hitting the upstream sources, which are rate-limited (GDELT), bot-blocked (cdc.gov) or slow.
Built the same way as `geo-feeds` and `hazard-feeds`: one GitHub Actions job (`conductor.py`) runs
every feed on its own cadence; each feed publishes its `data/` directory as a single-commit branch.

```
https://raw.githubusercontent.com/Deasus/health-feeds/<feed>/<file>
https://raw.githubusercontent.com/Deasus/health-feeds/extract/events.json   # the merged event list
https://raw.githubusercontent.com/Deasus/health-feeds/status/status.json    # health of every feed
```

## Feeds

| Branch | File | Upstream (verified 2026-10-22) | Cadence | Licence |
|---|---|---|---|---|
| `who` | `don.json` | WHO Disease Outbreak News JSON (`who.int/api/news/diseaseoutbreaknews`) | 30 min | WHO terms: public-health use, attribution, no commercial promotion |
| `who` | `archive.json` | every WHO DON the API holds (3,201, 2004 → today; ~1,000 migrated notices carry a June 2021 date), one compact row each (title, date, link, pathogen, countries, stated counts); not merged into events | 30 min (new DONs only after the first walk) | WHO terms, as above |
| `cdc` | `travel_notices.json` | CDC Travel Health Notices RSS | 1 h | US Government, public domain |
| `cdc` | `nndss.json` | CDC NNDSS weekly provisional counts (Socrata `x9gk-5huc`) | 1 h (weekly data) | public domain |
| `cdc` | `nwss.json` | CDC NWSS wastewater, per-sample (Socrata `j9g8-acpt` SARS-CoV-2, `ymmh-divb` flu A, `xpxn-rzgz` mpox, `45cq-cw4i` RSV, `akvg-8vrb` measles) | 1 h (weekly data) | public domain; `WastewaterSCAN` rows (CC BY-NC) excluded |
| `travel` | `state.json` | US State Department travel advisories RSS | 1 h | public domain |
| `ecdc` | `cdtr.json` | ECDC Communicable Disease Threats Report RSS | 6 h | CC BY 4.0 |
| `hdx` | `datasets.json` | HDX CKAN, outbreak datasets by title | 6 h | per dataset (carried on each event) |
| `fda` | `recalls.json` | openFDA food enforcement (ongoing recalls with a pathogen) | 6 h | public domain |
| `paho` | `alerts.json` | PAHO Epidemiological Alerts and Updates (HTML list; title, date, link, pathogen, countries only) | 6 h | PAHO documents CC BY-NC-SA 3.0 IGO; no text reproduced |
| `wahis` | `events.json` | WOAH WAHIS official animal-disease notifications, zoonoses and HPAI only, with outbreak coordinates (public API behind wahis.woah.org) | 1 h | WOAH: public data, attribution; commercial reuse permission requested (PLAN §4.7) |
| `aphis` | `hpai.json` | USDA APHIS HPAI detections in wild birds and mammals (CSV), one event per US county in 45 days | 6 h | public domain |
| `whispers` | `events.json` | USGS WHISPers wildlife morbidity/mortality events (zoonotic diagnoses and undiagnosed die-offs ≥ 100 animals) | 6 h | public domain |
| `gdelt` | `articles.json` | GDELT DOC 2.0, health themes and keywords (one translingual slot per rotation) | 30 min | GDELT terms; headlines © publishers |
| `gnews` | `articles.json` | Google News RSS, 62 health queries in eight languages (English 20; Spanish, French, Portuguese, Arabic, Chinese, Russian, Indonesian 6 each) | 30 min | headlines © publishers; RSS used for discovery |
| `reliefweb` | `disasters.json` | ReliefWeb API v2, disasters typed Epidemic (needs `RELIEFWEB_APPNAME`) | 30 min | attribution |
| `extract` | `events.json` | the merge of everything above | 15 min | see each event |
| `status` | `status.json` | per-feed health written by the conductor | 5 min | — |

Not here, and why: CDC HAN (no RSS; cdc.gov blocks non-browser clients, even with a browser user
agent; the syndication API's HAN topic stopped in 2019), HealthMap and Outbreaks Near Me (terms forbid retrieval),
ProMED (paywalled; its RSS is gone), Global.health (non-commercial), Epic Health Alerts (web and email only),
WastewaterSCAN (CC BY-NC), GDELT GEO 2.0 (dead), Eurosurveillance (Cloudflare challenge on the RSS; ECDC's CDTR covers
it), FAO EMPRES-i (no documented API or licence; WAHIS carries the same official notifications), APHIS poultry-flock
and livestock tables (rendered in the browser only; their CSVs stopped in 2025 and 2024, so US poultry and dairy HPAI
come through WAHIS). See `~/PULSE/research/sources-2026-10-22.md` and PLAN §11 (the HealthMap delta, 2026-10-30).

## The event

Every `items[]` entry in every file is one **HealthEvent**:

```
id, kind (outbreak|alert|advisory|recall|surveillance|news), pathogen, pathogen_name,
pathogen_class (viral|bacterial|vectorBorne|foodborne|zoonotic|fungal|unknown),
severity 0–4 + severity_label (INFO|MINOR|MODERATE|SEVERE|EXTREME),
source, source_tier (official|aggregator|news), title, headline, body, instruction,
area, country_code, countries[], admin1, admin1_code, lat, lng, geo_precision (point|admin1|country|none),
region (CINDER Area: unitedStates|canada|latinAmerica|europe|africa|middleEast|asia|oceania|global),
cases, deaths, cases_quote, onset, updated, expires, url (provenance only), facts [[label, value]],
credit, licence, tags[], corroboration, cluster
```

Files are `{generated_at, upstream, credit, licence, count, …, items: [...]}`. Some carry extra
tables: `cdc/nndss.json.table` (every state × condition with a count this week), `cdc/nwss.json.summaries`
(per pathogen, per state: sites, detecting %, rising %), `travel/state.json.advisories` (every
country's current advisory), `gdelt/articles.json.articles` (headline rows).

**The curator pass (`feeds/extract/llm_extract.py`, PLAN §4.6):** with `ANTHROPIC_API_KEY` set, Claude Haiku reads every
news headline once (cached 72 h in `extract/llm_cache.json`, at most 300 new headlines per run, thinking disabled): items
that are not outbreak news are dropped; pathogen, country, a stated city and verbatim counts are filled in; the city
becomes a point (GeoNames gazetteer, `feeds/_shared/gazetteer.py` + `cities.json`, CC BY 4.0, 12,456 cities ≥ 50k plus
capitals, ~15 km radius) once the item is corroborated. Official country-level events whose own text names a city are
placed at that city too (`city-from-text`). Without the key the merge runs rule-based only.

**Rules the merge applies (`feeds/extract/build_events.py`):** events older than 45 days are dropped;
`corroboration` counts distinct non-news sources naming the same pathogen in the same country within
21 days; a news headline is mapped (country centroid) and lifted to MINOR only when corroborated,
otherwise it stays INFO with no coordinates; `cluster` = pathogen | country | admin1 | ISO week.

**Severity rules:** WHO DON → SEVERE · CDC travel notice level 3/2/1 → SEVERE/MODERATE/MINOR ·
State Dept level 4/3/2 with a health reason → SEVERE/MODERATE/MINOR · NNDSS new 52-week high →
MODERATE (≥ 10 cases or a priority pathogen) else MINOR · NWSS measles detection → MODERATE, mpox
detection → MINOR, COVID/flu/RSV rising in ≥ 50 % of a state's sites → MINOR · FDA Class I with a
pathogen → MODERATE · HDX, ECDC, uncorroborated news → INFO.

## The animal and wildlife layer (2026-10-30)

HealthMap's edge over an official-feeds app was animal health (WOAH, FAO), wildlife (the Wildlife Data Integration
Network) and fifteen news languages. `wahis`, `aphis` and `whispers` close the first two: every event they emit is
tagged `animal` plus `wild`/`domestic`, `wildlife`, `wild-bird`, `mammal`, `poultry`, `suspect` (presumptive diagnosis)
or `undiagnosed` (a die-off with no diagnosis yet, pathogen `unknown`). Animal counts live in `facts` (`Animal cases`,
`Animal deaths`, `Detections`), never in `cases`/`deaths`. Only diseases that map to a PULSE pathogen are kept (HPAI,
rabies, West Nile, Rift Valley fever, anthrax, screwworm, Nipah, CCHF, Q fever, brucellosis, tularemia, EEE,
leishmaniasis); African swine fever, foot-and-mouth and the other animal-only diseases stay out, and names that would
collide with a human pathogen (fowl typhoid, avian cholera, duck plague, avian mycoplasmosis, bovine TB) are skipped.
In the merge, animal events corroborate news only for HPAI ("bird flu" headlines); a rabies or anthrax report in
animals is its own layer. Severity: MINOR, MODERATE when HPAI is found in mammals or WOAH notes human cases.

**Languages.** `gnews` reads Google News in English, Spanish, French, Portuguese, Arabic, Chinese, Russian and
Indonesian. `feeds/_shared/pathogens.py` carries the pathogen names in each (Arabic and CJK aliases match without
word boundaries) and the outbreak words the generic-pathogen filter needs; a headline becomes an event when a pathogen
is recognised in its own language, and the Haiku curator confirms it and fills the country. Events carry
`["Language", …]` in `facts` and a `lang:<language>` tag; `articles.json` reports `articles_by_language`.

## Geocoding

No upstream except CDC NNDSS ships coordinates. `feeds/_shared/geocode.py` resolves country names,
aliases and ISO2/ISO3 codes to Natural Earth centroids (`centroids.py`, generated from CINDER
Mobile's `boundaries.json` by `tools/build_centroids.py`), plus US/CA/AU states and a few overseas
territories. `geo_precision` says how far to trust a point; the app should draw country-precision
events at the centroid with a precision ring, never as a pin. US counties (APHIS, WHISPers) come from the Census
gazetteer (`counties.json`, `tools/build_counties.py`, 3,222 counties): a `point` at the county centroid with
`geo_radius_km` = half the square root of the county's area. WAHIS events sit at the mean of their outbreak coordinates
with a ring that covers them all (`point` ≤ 60 km, `admin1` ≤ 300 km, else `country`).

## Running locally

```sh
git init                                   # the conductor needs a repository (no remote required)
CONDUCTOR_PUSH=0 CONDUCTOR_ONLY=who,cdc,travel,ecdc,hdx,fda,gdelt,gnews,paho,wahis,aphis,whispers python3 conductor.py
CONDUCTOR_PUSH=0 CONDUCTOR_ONLY=extract HF_OFFLINE=1 python3 conductor.py
python3 -m unittest discover -s tests -q   # decoders on fixtures, no network
```

Outputs land in `work/<feed>/data/`. Python 3.9+ and the standard library only.

Secrets (GitHub Actions, all optional): `SOCRATA_APP_TOKEN` (lifts data.cdc.gov throttling),
`OPENFDA_API_KEY` (120,000 requests/day instead of 1,000), `RELIEFWEB_APPNAME` (enables the
ReliefWeb feed), `ANTHROPIC_API_KEY` (enables the curator pass; set 2026-10-22). `WAHIS_MAX_REPORT_FETCHES` (default 60) caps
the per-run detail fetches on a cold start.

## Known sensitivities

- **GDELT** answers a single theme or one keyword OR-group in 14–18 s and refuses OR-ed themes as
  "larger queries" (429). It also throttles a client that has sent several requests in the previous
  minutes, with the same 429 text. The fetcher therefore runs three simple queries per run on a
  rotation, 12 s apart, and merges a rolling 48-hour window; a failed run keeps the previous rows.
  Never call GDELT from the app.
- **cdc.gov** (not data.cdc.gov) returns 403 to non-browser clients. Everything here uses Socrata or
  `wwwnc.cdc.gov` RSS.
- **WHO DON** sorts oldest-first by default and has no geography; countries are read from the title.
- **State Dept RSS** is ~600 KB for 222 items; health relevance comes from the bolded reasons.
- **aphis.usda.gov** 403s browser-like User-Agents (Akamai) but serves the pipeline's own `health-feeds/1.0` UA; keep it.
- **WAHIS** has no documented API: the list endpoint is a POST with the dashboard's filter body and `totalSize`; a report's
  `all-information` carries the coordinates. Details are cached in `wahis/reports.json` so each report is fetched once.
- **WHO DON `$skip`** repeats pages past ~1,100 rows when publication dates tie; the archive walks a `$filter` date cursor
  and keys rows by `UrlName` (DonId is not unique).

## Attribution

Each file and each event names its upstream source, credit line and licence. Credit the original
providers when you reuse the data.
