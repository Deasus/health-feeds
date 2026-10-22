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
| `cdc` | `travel_notices.json` | CDC Travel Health Notices RSS | 1 h | US Government, public domain |
| `cdc` | `nndss.json` | CDC NNDSS weekly provisional counts (Socrata `x9gk-5huc`) | 1 h (weekly data) | public domain |
| `cdc` | `nwss.json` | CDC NWSS wastewater, per-sample (Socrata `j9g8-acpt` SARS-CoV-2, `ymmh-divb` flu A, `xpxn-rzgz` mpox, `45cq-cw4i` RSV, `akvg-8vrb` measles) | 1 h (weekly data) | public domain; `WastewaterSCAN` rows (CC BY-NC) excluded |
| `travel` | `state.json` | US State Department travel advisories RSS | 1 h | public domain |
| `ecdc` | `cdtr.json` | ECDC Communicable Disease Threats Report RSS | 6 h | CC BY 4.0 |
| `hdx` | `datasets.json` | HDX CKAN, outbreak datasets by title | 6 h | per dataset (carried on each event) |
| `fda` | `recalls.json` | openFDA food enforcement (ongoing recalls with a pathogen) | 6 h | public domain |
| `gdelt` | `articles.json` | GDELT DOC 2.0, health themes and keywords, English | 30 min | GDELT terms; headlines © publishers |
| `reliefweb` | `disasters.json` | ReliefWeb API v2, disasters typed Epidemic (needs `RELIEFWEB_APPNAME`) | 30 min | attribution |
| `extract` | `events.json` | the merge of everything above | 15 min | see each event |
| `status` | `status.json` | per-feed health written by the conductor | 5 min | — |

Not here, and why: CDC HAN (no RSS; cdc.gov blocks non-browser clients, even with a browser user
agent), HealthMap and Outbreaks Near Me (terms forbid retrieval), ProMED (paywalled), Global.health
(non-commercial), Epic Health Alerts (web and email only), WastewaterSCAN (CC BY-NC), GDELT GEO 2.0
(dead). See `~/PULSE/research/sources-2026-10-22.md`.

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

**Rules the merge applies (`feeds/extract/build_events.py`):** events older than 45 days are dropped;
`corroboration` counts distinct non-news sources naming the same pathogen in the same country within
21 days; a news headline is mapped (country centroid) and lifted to MINOR only when corroborated,
otherwise it stays INFO with no coordinates; `cluster` = pathogen | country | admin1 | ISO week.

**Severity rules:** WHO DON → SEVERE · CDC travel notice level 3/2/1 → SEVERE/MODERATE/MINOR ·
State Dept level 4/3/2 with a health reason → SEVERE/MODERATE/MINOR · NNDSS new 52-week high →
MODERATE (≥ 10 cases or a priority pathogen) else MINOR · NWSS measles detection → MODERATE, mpox
detection → MINOR, COVID/flu/RSV rising in ≥ 50 % of a state's sites → MINOR · FDA Class I with a
pathogen → MODERATE · HDX, ECDC, uncorroborated news → INFO.

## Geocoding

No upstream except CDC NNDSS ships coordinates. `feeds/_shared/geocode.py` resolves country names,
aliases and ISO2/ISO3 codes to Natural Earth centroids (`centroids.py`, generated from CINDER
Mobile's `boundaries.json` by `tools/build_centroids.py`), plus US/CA/AU states and a few overseas
territories. `geo_precision` says how far to trust a point; the app should draw country-precision
events at the centroid with a precision ring, never as a pin.

## Running locally

```sh
git init                                   # the conductor needs a repository (no remote required)
CONDUCTOR_PUSH=0 CONDUCTOR_ONLY=who,cdc,travel,ecdc,hdx,fda,gdelt python3 conductor.py
CONDUCTOR_PUSH=0 CONDUCTOR_ONLY=extract HF_OFFLINE=1 python3 conductor.py
python3 -m unittest discover -s tests -q   # decoders on fixtures, no network
```

Outputs land in `work/<feed>/data/`. Python 3.9+ and the standard library only.

Secrets (GitHub Actions, all optional): `SOCRATA_APP_TOKEN` (lifts data.cdc.gov throttling),
`OPENFDA_API_KEY` (120,000 requests/day instead of 1,000), `RELIEFWEB_APPNAME` (enables the
ReliefWeb feed), `ANTHROPIC_API_KEY` (reserved for the extraction stage, not wired yet).

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

## Attribution

Each file and each event names its upstream source, credit line and licence. Credit the original
providers when you reuse the data.
