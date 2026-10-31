"""Feed registry for conductor.py (health-feeds).

Each feed's output lives on a branch of the same name; the branch root is the
feed's data/ directory, so a file is served at
  https://raw.githubusercontent.com/Deasus/health-feeds/<feed>/<path>

every     target seconds between run starts
timeout   seconds allowed per command
run       commands, run in order inside work/<feed>/
code      feeds/<dir>/ folders copied into the work dir (files land side by side; _shared holds the helpers)
requires  env vars that must be set, else the feed is skipped (shown in status.json)
optional  env vars passed through when set
keep_going run every command even if one fails
ignore    patterns under data/ never published

Cadences follow the upstream rhythm (research/sources-2026-10-22.md in ~/PULSE):
WHO DON several a week, CDC travel notices per notice, State Dept per advisory,
ECDC weekly, HDX per dataset, GDELT continuous (one request per 5 s), openFDA
weekly, NNDSS weekly (Thursdays), NWSS weekly (Fridays), ReliefWeb per disaster,
PAHO alerts a few a month, WAHIS notifications daily, APHIS CSVs weekly, WHISPers daily.
"""

FEEDS = {
    # ── official, structured ───────────────────────────────────────────────────
    'who':       {'code': ['who', '_shared'], 'keep_going': True,
                  'run': [['python3', 'fetch_who.py'], ['python3', 'fetch_who_archive.py']],
                  'every': 1800, 'timeout': 600},
    'cdc':       {'code': ['cdc', '_shared'], 'keep_going': True,
                  'run': [['python3', 'fetch_cdc_travel.py'], ['python3', 'fetch_cdc_nndss.py'], ['python3', 'fetch_cdc_nwss.py']],
                  'every': 3600, 'timeout': 900, 'optional': ['SOCRATA_APP_TOKEN']},
    'travel':    {'code': ['travel', '_shared'], 'run': [['python3', 'fetch_state.py']],
                  'every': 3600, 'timeout': 300},
    'ecdc':      {'code': ['ecdc', '_shared'], 'run': [['python3', 'fetch_ecdc.py']],
                  'every': 21600, 'timeout': 300},
    'fda':       {'code': ['fda', '_shared'], 'run': [['python3', 'fetch_fda.py']],
                  'every': 21600, 'timeout': 300, 'optional': ['OPENFDA_API_KEY']},
    'paho':      {'code': ['paho', '_shared'], 'run': [['python3', 'fetch_paho.py']],
                  'every': 21600, 'timeout': 300},

    # ── official, animal and wildlife health (2026-10-30, the HealthMap delta: PLAN §11) ──
    'wahis':     {'code': ['wahis', '_shared'], 'run': [['python3', 'fetch_wahis.py']],
                  'every': 3600, 'timeout': 1200, 'optional': ['WAHIS_MAX_REPORT_FETCHES', 'WAHIS_TIME_BUDGET_S']},
    'aphis':     {'code': ['aphis', '_shared'], 'run': [['python3', 'fetch_aphis.py']],
                  'every': 21600, 'timeout': 600},
    'whispers':  {'code': ['whispers', '_shared'], 'run': [['python3', 'fetch_whispers.py']],
                  'every': 21600, 'timeout': 900},

    # ── aggregators ────────────────────────────────────────────────────────────
    'hdx':       {'code': ['hdx', '_shared'], 'run': [['python3', 'fetch_hdx.py']],
                  'every': 21600, 'timeout': 600},
    'reliefweb': {'code': ['reliefweb', '_shared'], 'run': [['python3', 'fetch_reliefweb.py']],
                  'every': 1800, 'timeout': 300, 'requires': ['RELIEFWEB_APPNAME']},

    # ── news ───────────────────────────────────────────────────────────────────
    'gdelt':     {'code': ['gdelt', '_shared'], 'run': [['python3', 'fetch_gdelt.py']],
                  'every': 1800, 'timeout': 900},
    'gnews':     {'code': ['gnews', '_shared'], 'run': [['python3', 'fetch_gnews.py']],
                  'every': 1800, 'timeout': 600},

    # ── the merge: every feed above → one HealthEvent list ─────────────────────
    # Runs in work/extract/ and reads its siblings' data/ directories (../<feed>/data),
    # falling back to the published branches when a sibling has not run yet.
    'extract':   {'code': ['extract', '_shared'], 'run': [['python3', 'build_events.py']],
                  'every': 900, 'timeout': 600, 'optional': ['ANTHROPIC_API_KEY']},
}
