"""health-feeds shared helpers (stdlib only; Python 3.9+).

Every fetcher: fetch → decode → write data/<file>.json only if the items changed →
exit 0. On any upstream failure: log, leave the previous file alone, exit 1 so the
conductor records the failure in status.json. Nothing here touches the network
except get() and sibling_data().
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

DATA = os.path.join(os.getcwd(), 'data')          # the conductor runs each feed with cwd = work/<feed>
UA = 'health-feeds/1.0 (+https://github.com/Deasus/health-feeds)'
RAW_BASE = os.environ.get('HF_RAW_BASE', 'https://raw.githubusercontent.com/Deasus/health-feeds')


class HFError(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt) -> str:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def log(msg: str) -> None:
    print(f'{iso(now())} {msg}', flush=True)


def fail(msg: str) -> None:
    """Log and exit 1: the conductor marks the feed FAILED and keeps the previous data."""
    log(f'FAILED: {msg}')
    sys.exit(1)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def get(url: str, *, headers=None, timeout: int = 30, retries: int = 2, backoff: float = 5.0,
        ua: str = UA, rate_limit_sleep: float = 15.0) -> bytes:
    """GET with a UA, timeouts, retry on 5xx/network errors, and one wait on 429."""
    h = {'User-Agent': ua, 'Accept': '*/*'}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
            if e.code == 429 and attempt < retries:
                log(f'  429 from {urllib.parse.urlparse(url).netloc}; waiting {rate_limit_sleep}s')
                time.sleep(rate_limit_sleep)
                continue
            if 500 <= e.code < 600 and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise HFError(f'{last} for {url[:120]}') from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = str(e)[:120]
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise HFError(f'{last} for {url[:120]}') from e
    raise HFError(f'{last} for {url[:120]}')


def get_json(url: str, **kw):
    kw.setdefault('headers', {})
    kw['headers'].setdefault('Accept', 'application/json')
    raw = get(url, **kw)
    try:
        return json.loads(raw.decode('utf-8', 'replace'))
    except ValueError as e:
        raise HFError(f'bad JSON from {url[:120]}: {str(e)[:80]}') from e


def get_text(url: str, **kw) -> str:
    return get(url, **kw).decode('utf-8', 'replace')


def get_xml(url: str, **kw) -> ET.Element:
    raw = get(url, **kw)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise HFError(f'bad XML from {url[:120]}: {str(e)[:80]}') from e


# ── RSS ───────────────────────────────────────────────────────────────────────

def rss_items(root: ET.Element) -> list:
    """RSS 2.0 items as dicts: title, link, description, pubDate, guid, categories [(domain, text)], extra {tag: text}."""
    out = []
    for it in root.iter('item'):
        d = {'title': None, 'link': None, 'description': None, 'pubDate': None, 'guid': None,
             'categories': [], 'extra': {}}
        for c in it:
            tag = c.tag.split('}')[-1]
            text = (c.text or '').strip()
            if tag == 'category':
                d['categories'].append((c.get('domain'), text))
            elif tag in d:
                d[tag] = text
            else:
                d['extra'][tag] = text
        out.append(d)
    return out


# ── text and dates ────────────────────────────────────────────────────────────

_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')


def strip_html(s) -> str:
    if not s:
        return ''
    s = re.sub(r'<\s*/?(br|p|li|h\d|div|tr|ul|ol)(\s[^>]*)?\s*/?>', '\n', s, flags=re.I)
    s = _TAG.sub('', s)
    s = html.unescape(s)
    lines = [_WS.sub(' ', ln).strip() for ln in s.split('\n')]
    return '\n'.join(ln for ln in lines if ln).strip()


def first_sentence(s: str, limit: int = 240) -> str:
    s = _WS.sub(' ', s or '').strip()
    m = re.match(r'(.+?[.!?])(\s|$)', s)
    out = m.group(1) if m else s
    return out if len(out) <= limit else out[:limit - 1].rstrip() + '…'


def parse_date(s):
    """RFC 822, ISO 8601, GDELT 'YYYYMMDDTHHMMSSZ', openFDA 'YYYYMMDD', 'Wed, 21 Oct 2026' → aware UTC datetime or None."""
    if not s:
        return None
    s = str(s).strip()
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%MZ', '%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
                '%Y%m%dT%H%M%SZ', '%Y-%m-%d', '%Y%m%d', '%d %B %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(s[:26] if '%f' in fmt else s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:?\d{2})$', s)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).split('.')[0] + m.group(2)[:3] + ':' + m.group(2)[-2:])
        except ValueError:
            return None
    return None


def stable_id(*parts) -> str:
    return hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16]


def iso_week(dt) -> str:
    y, w, _ = (dt or now()).isocalendar()
    return f'{y}-W{w:02d}'


# ── files ─────────────────────────────────────────────────────────────────────

def read_json(path: str, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def envelope(items: list, *, upstream: str, credit: str, licence: str, **extra) -> dict:
    env = {'generated_at': iso(now()), 'upstream': upstream, 'credit': credit, 'licence': licence,
           'count': len(items)}
    env.update(extra)
    env['items'] = items
    return env


def write_if_changed(name: str, env: dict, volatile=('generated_at',)) -> bool:
    """Write data/<name> only when its non-volatile content changed. Returns True if written."""
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    old = read_json(path)
    def strip(d):
        return {k: v for k, v in (d or {}).items() if k not in volatile}
    if old is not None and strip(old) == strip(env):
        log(f'  {name}: unchanged ({env.get("count", "?")} items)')
        return False
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(env, f, ensure_ascii=False, separators=(',', ':'), sort_keys=False)
    os.replace(tmp, path)
    log(f'  {name}: written ({env.get("count", "?")} items, {os.path.getsize(path)//1024} KB)')
    return True


def sibling_data(feed: str, name: str):
    """Another feed's output: ../<feed>/data/<name> when it ran on this runner, else the
    published branch. HF_OFFLINE=1 disables the network fallback (tests)."""
    local = os.path.join(os.getcwd(), '..', feed, 'data', name)
    d = read_json(local)
    if d is not None:
        return d
    if os.environ.get('HF_OFFLINE') == '1':
        return None
    try:
        return get_json(f'{RAW_BASE}/{feed}/{name}?m={int(time.time() // 60)}', timeout=30, retries=1)
    except HFError as e:
        log(f'  sibling {feed}/{name} unavailable: {e}')
        return None


def cache_bust(url: str) -> str:
    sep = '&' if '?' in url else '?'
    return f'{url}{sep}m={int(time.time() // 60)}'
