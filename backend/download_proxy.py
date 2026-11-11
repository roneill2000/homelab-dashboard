"""Manual downloads from the dashboard: search Prowlarr's indexers, grab a result, or hand RdtClient a magnet.

Grabs go through Prowlarr, which sends torrents to RdtClient and NZBs to SABnzbd under the 'prowlarr' category,
the same path its own Search page uses. Magnets go straight to RdtClient in that category. The browser never sees
indexer URLs or API keys: search results are handed out as short-lived tokens.
"""
import base64
import http.cookiejar
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import qwen_proxy

RDT = '/etc/homelab-dashboard/rdt.json'
PROWLARR = '/etc/homelab-dashboard/prowlarr.json'
TORBOX = '/etc/homelab-dashboard/torbox.json'
REALDEBRID = '/etc/homelab-dashboard/realdebrid.json'
CATEGORY = 'prowlarr'
MAGNET = re.compile(r'magnet:\?[\x21-\x7e]*xt=urn:bt(ih|mh):[0-9A-Za-z]{32,68}[\x21-\x7e]*')
RESULTS = {}; RESULTS_LOCK = threading.Lock()
_opener = None; _opener_lock = threading.Lock()

# --- Debrid direct downloads -------------------------------------------------
# Ryan pastes a Real-Debrid (or TorBox) link into the homepage; the n100 downloads it
# straight to Unraid with aria2c at full speed, bypassing RdtClient.
# The dashboard never runs a shell on the n100. It uses its own download key, whose forced command
# on .3 is /usr/local/sbin/homelab-download-rpc: one JSON request in, one JSON answer out (ping,
# start, check). The download rules - which folders a file may land in, what a file may be called,
# when a download counts as finished - live on the n100 in /usr/local/sbin/homelab_download_lib.py
# (repo: scripts/homelab_download_lib.py). The checks below only reject the obvious mistakes early
# so the panel can say why; tests/test_debrid_parity.py keeps the two rule sets equal.
# Film or series: the file name decides when it states a season/episode; otherwise Jellyfin is asked what
# it already calls that title, and only then SearXNG. A tie in the search answers nothing (see searxng_ground).
# Library update: POST .17:8096/ScheduledTasks/Running/<Scan Media Library id>, using the
# dashboard process's own Jellyfin header (__main__._JF_HDR), the same one posters use.
# State: /var/lib/homelab-dashboard/debrid.json (older deploys kept it in /etc/homelab-dashboard).
DEBRID_HTTP = re.compile(r'https://[^\s"\'<>|\\]+')
# Episode evidence in a file name. Case matters for the shorthand forms, so a lowercase hex or uuid
# name cannot read as S01E02, and classify() ignores a shorthand that sits after a tech tag segment:
# 16x9, 21x9, and a release tag like -E9 are release tags, not episodes.
SE_DOT = re.compile(r'\bS(\d{1,2})\s*[Ee]\s*(\d{1,3})\b')
SE_X = re.compile(r'\b(\d{1,2})x(\d{2,3})\b')                     # 1x04, 11x104: the episode is zero-padded
SEASON_ONLY = re.compile(r'\b(?:Season|Staffel)\s*[._-]\s*(\d{1,2})\b', re.I)
EP_WORD = re.compile(r'\bEpisode\s*[._-]?\s*(\d{1,3})\b', re.I)  # Episode 7, Episode.07
EP_BARE = re.compile(r'\bE\s*[._-]\s*(\d{2,3})\b')               # E-12, E_07 (capital E only)
YEAR = re.compile(r'\b(19\d{2}|20\d{2})\b')
# The worker on .3 checks the same list (scripts/homelab_download_lib.py); tests/test_debrid_parity.py
# fails if one side adds or drops an extension without the other.
MEDIA_EXT = ('.mkv', '.mp4', '.m4v', '.mov', '.avi', '.wmv', '.flv', '.webm', '.m4a', '.mp3', '.flac', '.epub')
SEARXNG = 'http://192.168.0.13:8888/search'
JF_SCAN = 'http://192.168.0.17:8096/ScheduledTasks/Running/7738148ffcd07979c7ceb148e06b3aed'  # "Scan Media Library"
JF_ITEMS = 'http://192.168.0.17:8096/Items'
DEBRID_STATE = '/var/lib/homelab-dashboard/debrid.json'
DEBRID_STATE_OLD = '/etc/homelab-dashboard/debrid.json'
MOVIES_DIR = '/mnt/unraid/Movies'
SHOWS_DIR = '/mnt/unraid/Shows'
_DOWNLOAD_KEY = '/etc/homelab-dashboard/download_ed25519'
_PROXMOX_KNOWN = '/etc/homelab-dashboard/update_known_hosts'
_PROXMOX_TARGET = 'root@192.168.0.3'
_DEBRID_IF_VERSION = 1
_DEBRID_KEEP = 50      # rows kept in the state file
_DEBRID_AGE = 7 * 86400   # how long a finished row stays on the homepage list
_DEBRID_ROWS = 15      # rows the homepage shows
_DEBRID_CHECK_MAX = 40 # downloads in one check call
_DEBRID_SCAN_UNTIL = ('finished', 'present')   # states that mean "ask Jellyfin to look"
_DEBRID_STATE_WORDS = ('downloading', 'finished', 'failed', 'stopped', 'interrupted', 'present', 'missing', 'preparing', 'unknown')
_RETRY_STATES = ('failed', 'stopped', 'interrupted')   # a stored link can be run again in these
# A Debrid-tab Get sends the torrent straight to TorBox and remembers it here as a 'preparing' row.
# The panel polls /api/debrid; the tick loop (the next step of the spec) picks the row up once TorBox
# has the files. The key file decides whether this path is on at all: without it, Get behaves exactly
# as before (Prowlarr -> RdtClient) and 'search' is not in features.
TORBOX_API = 'https://api.torbox.app/v1/api'
TORBOX_KEY = None
TORBOX_KEY_AT = 0
_TORBOX_KEY_LOCK = threading.Lock()
_TORBOX_MAX_TORRENT_BYTES = 5 * 1024 * 1024   # the indexer's .torrent file, at most
RD_API = 'https://api.real-debrid.com/rest/1.0'   # the primary service: its downloads are not capped
RD_KEY = None
RD_KEY_AT = 0
_RD_KEY_LOCK = threading.Lock()
# Real-Debrid refuses some torrents outright. These codes are about the torrent, so TorBox gets the
# same torrent next; a bad key or account trouble (8, 9, 14) is not the torrent's fault, but this grab
# still goes to TorBox and Ryan hears what Real-Debrid said.
_RD_TORRENT_REFUSALS = {28: 'file not allowed', 29: 'torrent too big', 30: 'torrent file invalid',
                        35: 'infringing file', 36: 'fair usage limit', 23: 'traffic exhausted',
                        21: 'too many active downloads'}
_RD_ACCOUNT_REFUSALS = {8: 'the Real-Debrid key is not accepted', 9: 'Real-Debrid refused this account',
                        14: 'the Real-Debrid account is locked'}
# Slow down (5), too many requests (34) and HTTP 429 are the same thing: the 250 a minute limit. Asking
# again can block the account, so this grab goes to the fallback instead of waiting.
_RD_RATE_LIMITS = (5, 34)
# aria2c 22 (the server refused the URL) and 24 (HTTP auth/authorization error) are what an
# expired debrid access token looks like. Only counted after a retry, where the link itself is the suspect.
_EXPIRED_LINK = re.compile(r'aria2c exited with code (?:22|24)\b')
_STATE_LOCK = threading.Lock()


def _config(path):
    with open(path) as f:
        return json.load(f)


def rdt(path, data=None):
    """Call RdtClient's qBittorrent API, logging in again if the session cookie has expired."""
    global _opener
    config = _config(RDT)
    with _opener_lock:
        if _opener is None:
            _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    def call():
        with _opener.open(urllib.request.Request(config['url'] + path, data=body), timeout=10) as r:
            return r.read()
    try:
        return call()
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403): raise
        login = json.dumps({'UserName': config['username'], 'Password': config['password']}).encode()
        with _opener.open(urllib.request.Request(config['url'] + '/Api/Authentication/Login', data=login,
                                                 headers={'Content-Type': 'application/json'}), timeout=10) as r:
            r.read()
        return call()


def prowlarr(path, body=None, timeout=60):
    config = _config(PROWLARR)
    headers = {'X-Api-Key': config['key']}
    if body is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(config['url'] + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def recent():
    """The 12 most recently added RdtClient items, newest first.

    RdtClient's qBittorrent 'progress' is (debrid progress + local download progress) / 2, so a file the debrid
    service has finished shows 50% before a byte reaches the disk. Active items are re-read from RdtClient's own
    API instead: 'stage' is debrid (the service is still fetching it) or local (copying to disk), with that stage's
    real progress, speed and RdtClient's status text."""
    items = json.loads(rdt('/api/v2/torrents/info'))
    items.sort(key=lambda t: t.get('added_on') or 0, reverse=True)
    keep = ('name', 'category', 'state', 'progress', 'size', 'dlspeed', 'eta', 'added_on')
    out = [{k: t.get(k) for k in keep} | {'id': (t.get('hash') or '').lower()} for t in items[:12]]
    active = [t for t in out if (t['progress'] or 0) < 1]
    if active:
        native = {(n.get('hash') or '').lower(): n for n in json.loads(rdt('/Api/Torrents'))}
        for t in active:
            n = native.get(t['id'])
            if not n: continue
            t['status_text'], t['error'] = n.get('statusText'), n.get('error')
            if (n.get('rdProgress') or 0) < 100:
                t.update(stage='debrid', progress=(n.get('rdProgress') or 0) / 100, dlspeed=n.get('rdSpeed') or 0, seeders=n.get('rdSeeders'))
                continue
            downloads = json.loads(rdt('/Api/Torrents/Get/' + urllib.parse.quote(n['torrentId']))).get('downloads') or []
            total = sum(d.get('bytesTotal') or 0 for d in downloads)
            if total:
                t.update(stage='local', progress=sum(d.get('bytesDone') or 0 for d in downloads) / total,
                         dlspeed=sum(d.get('speed') or 0 for d in downloads))
                if t['progress'] >= 1: t.update(stage='finishing', progress=0.999)
    for t in out:
        if not t['id']:
            del t['id']   # an item RdtClient gives no hash to cannot be addressed, so it gets no id
    return out


def search(query, kind):
    rows = prowlarr('/api/v1/search?' + urllib.parse.urlencode({'query': query, 'type': 'search', 'limit': 100}))
    now = time.time()
    out = []
    with RESULTS_LOCK:
        for key in [k for k, v in RESULTS.items() if now - v['at'] > 1800]: del RESULTS[key]
        for r in rows or []:
            if r.get('protocol') != kind or not r.get('guid') or not isinstance(r.get('indexerId'), int): continue
            token = secrets.token_urlsafe(12)
            # magnetUrl, downloadUrl and infoHash stay server-side: downloadUrl carries Prowlarr's API
            # key, so none of them may ever be in an HTTP answer. Get needs them to reach TorBox.
            RESULTS[token] = {'at': now, 'guid': r['guid'], 'indexerId': r['indexerId'], 'title': r.get('title'),
                              'magnetUrl': r.get('magnetUrl'), 'downloadUrl': r.get('downloadUrl'),
                              'infoHash': r.get('infoHash'), 'protocol': r.get('protocol')}
            info = r.get('infoUrl') if str(r.get('infoUrl', '')).startswith(('https://', 'http://')) else None
            out.append({'id': token, 'title': r.get('title'), 'size': r.get('size'), 'seeders': r.get('seeders'),
                        'leechers': r.get('leechers'), 'grabs': r.get('grabs'), 'indexer': r.get('indexer'),
                        'published': r.get('publishDate'), 'info': info})
    key = (lambda x: x['seeders'] or 0) if kind == 'torrent' else (lambda x: x['grabs'] or 0)
    out.sort(key=key, reverse=True)
    return out[:60]


def add_magnet(magnet):
    rdt('/api/v2/torrents/add', {'urls': magnet, 'category': CATEGORY})


_GRAB_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')   # the sha1 (and sha256) forms RdtClient uses


def dismiss_grab(item_id):
    """Remove one failed Prowlarr grab from RdtClient's list, without deleting any data.

    A search grab that RdtClient refuses otherwise stays in the list forever. RdtClient's qBittorrent
    interface (verified in the running build: QBittorrentController serves torrents/delete with a
    deleteFiles field) answers this; deleteFiles=false means the entry leaves the list only. Raises
    ValueError for anything this call must not remove: a malformed or unknown id, another category,
    or a healthy item. An item is only removed when its category is 'prowlarr' and it is in an error
    state - the qBittorrent state says error/missing, or RdtClient's own record states an error.
    """
    if not isinstance(item_id, str) or not _GRAB_ID.fullmatch(item_id):
        raise ValueError('that is not a download id')
    item = next((t for t in json.loads(rdt('/api/v2/torrents/info'))
                 if (t.get('hash') or '').lower() == item_id), None)
    if item is None:
        raise ValueError('that download is not in the list anymore')
    if (item.get('category') or '') != CATEGORY:
        raise ValueError('that download was not a search grab; it stays in the list')
    state = (item.get('state') or '').lower()
    errored = 'error' in state or 'missing' in state
    if not errored:
        record = next((n for n in json.loads(rdt('/Api/Torrents'))
                       if (n.get('hash') or '').lower() == item_id), None) or {}
        errored = bool(str(record.get('error') or '').strip())
    if not errored:
        raise ValueError('that download has not failed; it stays in the list')
    rdt('/api/v2/torrents/delete', {'hashes': item_id, 'deleteFiles': 'false'})
    return dict(ok=True, id=item_id)


def grab(token):
    with RESULTS_LOCK:
        hit = RESULTS.get(token)
    if not hit: raise LookupError
    prowlarr('/api/v1/search', {'guid': hit['guid'], 'indexerId': hit['indexerId']})
    return hit['title']


# --- TorBox: a Debrid-tab Get goes straight to the debrid service --------------------------
# TorBox keeps the torrent and the files; the panel watches it under Downloads. The key lives in
# its own 0600 file on the dashboard host and is never printed, logged or sent to the browser.
# Without it, nothing here runs and Get behaves exactly as before.

class _NoFollow(urllib.request.HTTPRedirectHandler):
    """Redirects are not followed: a redirect to a magnet: link is read as that magnet, anything else is unusable."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _indexer_get(url):
    """One GET of an indexer download URL, with redirects not followed: (code, Location, body).

    The body is read only up to the size cap. A redirect lands in the HTTPError, like every other
    HTTP answer: its Location is returned so a magnet: redirect can be used.
    """
    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(urllib.request.Request(url), timeout=15) as r:
            return r.getcode(), (r.headers.get('Location') or ''), r.read(_TORBOX_MAX_TORRENT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, ((exc.headers.get('Location') or '') if exc.headers else ''), b''
    except Exception:
        raise ValueError('the indexer did not give a usable torrent for this result.')


def _torbox_key():
    """The TorBox key from its own file, or None when the file is missing or empty. Re-read after 60 s."""
    global TORBOX_KEY, TORBOX_KEY_AT
    with _TORBOX_KEY_LOCK:
        if TORBOX_KEY is None and time.time() - TORBOX_KEY_AT > 60:
            TORBOX_KEY_AT = time.time()
            try:
                with open(TORBOX) as f:
                    TORBOX_KEY = (json.load(f).get('key') or '').strip() or None
            except Exception:
                TORBOX_KEY = None
        return TORBOX_KEY


def _rd_key():
    """The Real-Debrid key from its own file, or None when the file is missing or empty. Re-read after 60 s."""
    global RD_KEY, RD_KEY_AT
    with _RD_KEY_LOCK:
        if RD_KEY is None and time.time() - RD_KEY_AT > 60:
            RD_KEY_AT = time.time()
            try:
                with open(REALDEBRID) as f:
                    RD_KEY = (json.load(f).get('key') or '').strip() or None
            except Exception:
                RD_KEY = None
        return RD_KEY


def _scrub_key(text):
    for key in (_rd_key(), _torbox_key()):
        if key and key in text:
            text = text.replace(key, '***')
    return text[:200]


def torbox(method, path, fields=(), files=(), timeout=20, json_body=None):
    """One TorBox API call: a multipart form, or one JSON body when json_body is given (controltorrent wants
    JSON). Raises RuntimeError with TorBox's own reason, scrubbed and cut to 200 chars."""
    key = _torbox_key()
    if not key:
        raise RuntimeError('the debrid service is not set up')
    boundary = '----homelab' + secrets.token_hex(16)
    buf = io.BytesIO() if (fields or files) else None
    for name, value in fields:
        buf.write(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value)).encode())
    for item in files:
        name, data = item[0], item[-1]
        if len(item) == 3:
            buf.write(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n'
                       % (boundary, name, item[1])).encode())
        else:
            buf.write(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n' % (boundary, name)).encode())
        buf.write(data)
        buf.write(b'\r\n')
    hdrs = {'Authorization': 'Bearer ' + key}
    payload = None
    if json_body is not None:
        payload = json.dumps(json_body).encode()
        hdrs['Content-Type'] = 'application/json'
    elif buf is not None:
        buf.write(('--%s--\r\n' % boundary).encode())
        payload = buf.getvalue()
        hdrs['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    req = urllib.request.Request(TORBOX_API + path, data=payload, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('detail') or detail
        except ValueError:
            pass
        raise RuntimeError(_scrub_key(detail or ('TorBox answered HTTP %d' % exc.code)))
    try:
        ans = json.loads(raw)
    except ValueError:
        raise RuntimeError('TorBox answered with something that is not JSON')
    if not isinstance(ans, dict) or ans.get('success') is not True:
        detail = str(ans.get('detail') or ans.get('error') or 'TorBox refused the request') if isinstance(ans, dict) else 'TorBox refused the request'
        raise RuntimeError(_scrub_key(detail))
    return ans


class RDRefusal(RuntimeError):
    """Real-Debrid will not do this torrent: the fallback provider gets it."""


class RDAccountError(RuntimeError):
    """A bad key or account trouble at Real-Debrid: not this torrent's fault, and this grab goes to the fallback."""


def realdebrid(method, path, fields=None, raw=None, timeout=20):
    """One Real-Debrid API call. Raises RDRefusal, RDAccountError or RuntimeError with the service's own
    reason, scrubbed of any key and cut to 200 characters."""
    key = _rd_key()
    if not key:
        raise RuntimeError('the debrid service is not set up')
    body = None
    hdrs = {'Authorization': 'Bearer ' + key}
    if raw is not None:
        body = raw
        hdrs['Content-Type'] = 'application/x-bittorrent'
    elif fields:
        body = urllib.parse.urlencode(fields).encode()
        hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(RD_API + path, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw_in = r.read()
    except urllib.error.HTTPError as exc:
        text, code = '', None
        try:
            ans = json.loads(exc.read().decode('utf-8', 'replace'))
            text = str(ans.get('error') or '')
            code = ans.get('error_code')
        except ValueError:
            pass
        if code is None and exc.code == 401:
            code = 8
        if code in _RD_TORRENT_REFUSALS:
            raise RDRefusal(_scrub_key(text or _RD_TORRENT_REFUSALS[code]))
        if code in _RD_ACCOUNT_REFUSALS:
            raise RDAccountError(_scrub_key(text or _RD_ACCOUNT_REFUSALS[code]))
        if code in _RD_RATE_LIMITS or exc.code == 429:
            raise RDAccountError('Real-Debrid rate limit reached')
        raise RuntimeError(_scrub_key(text or ('Real-Debrid answered HTTP %d' % exc.code)))
    if not raw_in.strip():
        return {}
    try:
        ans = json.loads(raw_in)
    except ValueError:
        raise RuntimeError('Real-Debrid answered with something that is not JSON')
    if not isinstance(ans, dict):
        raise RuntimeError('Real-Debrid answered with something that is not an object')
    return ans


def _rd_add_torrent(kind, payload):
    """Add this torrent at Real-Debrid and return (id, hash). Choosing the files comes in the tick step."""
    if kind == 'magnet':
        ans = realdebrid('POST', '/torrents/addMagnet', fields={'magnet': payload})
    else:
        ans = realdebrid('PUT', '/torrents/addTorrent', raw=payload)
    return ans.get('id'), str(ans.get('hash') or '')


def _tb_add_torrent(kind, payload):
    """Add this torrent at TorBox and return (id, hash)."""
    if kind == 'magnet':
        ans = torbox('POST', '/torrents/createtorrent', fields=[('magnet', payload)])
    else:
        ans = torbox('POST', '/torrents/createtorrent', files=[('file', payload)])
    data = ans.get('data') if isinstance(ans.get('data'), dict) else {}
    return (data.get('torrent_id') or data.get('id')), str(data.get('hash') or '')


# Real-Debrid first, TorBox second, always in this order; a provider without a key file is skipped.
_PROVIDERS = (('realdebrid', 'Real-Debrid', 'rd', _rd_key, _rd_add_torrent),
              ('torbox', 'TorBox', 'torbox', _torbox_key, _tb_add_torrent))


def _indexer_torrent(hit):
    """The torrent of a Prowlarr result: a magnet, or the .torrent bytes the indexer serves.

    The magnet wins when it is there and usable. Otherwise the downloadUrl (Prowlarr's own download
    endpoint on 192.168.0.13:9696) is fetched with redirects off: a redirect to a magnet: link is
    that magnet, a body that starts a bencoded .torrent is the file itself. Anything else is not
    usable, and so is a file over the size cap.
    """
    magnet = hit.get('magnetUrl') or ''
    if magnet.startswith('magnet:') and MAGNET.fullmatch(magnet):
        return 'magnet', magnet
    url = hit.get('downloadUrl') or ''
    if not url.startswith(('http://', 'https://')):
        raise ValueError('the indexer did not give a usable torrent for this result.')
    code, location, body = _indexer_get(url)
    if location.startswith('magnet:') and MAGNET.fullmatch(location):
        return 'magnet', location
    if body[:11] == b'd8:announce' or (body[:1] == b'd' and b'4:info' in body):
        if len(body) > _TORBOX_MAX_TORRENT_BYTES:
            raise ValueError('the indexer did not give a usable torrent for this result.')
        return 'torrent', body
    raise ValueError('the indexer did not give a usable torrent for this result.')


def _row_hash(d):
    """The torrent hash of a preparing row: its debrid object, or the torbox one step 1 wrote first."""
    debrid = d.get('debrid') if isinstance(d.get('debrid'), dict) else {}
    old = d.get('torbox') if isinstance(d.get('torbox'), dict) else {}
    return str(debrid.get('hash') or old.get('hash') or '').lower()


def grab_debrid(token):
    """A Debrid-tab Get: add the torrent at the debrid services and remember it as a 'preparing' row.

    Real-Debrid is tried first because its downloads are not capped; TorBox gets the same torrent when
    Real-Debrid will not do it or its account is in the way. Both refusing: a 503 naming both reasons.
    A second Get for a hash that already has a 'preparing' row answers ok without adding it again.
    """
    with RESULTS_LOCK:
        hit = RESULTS.get(token)
    if not hit:
        raise LookupError
    info_hash = (hit.get('infoHash') or '').lower()
    with _STATE_LOCK:
        state = _load_state()
        existing = next((d for d in state['downloads']
                         if d.get('state') == 'preparing' and info_hash and _row_hash(d) == info_hash), None)
    if existing:
        return dict(ok=True, title=existing.get('title') or hit.get('title'), via='debrid')
    kind, payload = _indexer_torrent(hit)
    reasons, tried = [], []
    for name, display, prefix, key_get, add in _PROVIDERS:
        if not key_get():
            continue
        tried.append(name)
        try:
            torrent_id, torrent_hash = add(kind, payload)
        except (RDRefusal, RDAccountError, RuntimeError, ValueError) as e:
            reasons.append('%s: %s' % (display, _scrub_key(str(e))))
            continue
        except Exception as e:
            reasons.append('%s: %s' % (display, _scrub_key(str(e))))
            continue
        if not torrent_id:
            reasons.append('%s: it did not answer with a torrent id' % display)
            continue
        title = _clean_title(hit.get('title') or '') or ('%s-%s' % (prefix, torrent_id))
        row = {'name': '%s-%s' % (prefix, torrent_id), 'state': 'preparing', 'provider': name, 'title': title,
               'kind': None,
               'debrid': {'id': torrent_id, 'hash': str(torrent_hash or info_hash).lower()},
               'release': hit.get('title'), 'started': int(time.time()), 'tried': list(tried),
               'prep': {'progress': 0, 'seeds': None, 'files': None, 'provider': display},
           'prep_at': int(time.time()),   # the stall clock the fallback step watches
               'dest': None, 'bytes': 0, 'scanned': False, 'scan_error': None,
               # The provider that refused first, in the row's own details: Ryan sees why this torrent
               # did not stay at Real-Debrid.
               'error': (_scrub_key(reasons[0]) if reasons else None)}
        with _STATE_LOCK:
            state = _load_state()
            state['downloads'].append(row)
            _save_state(state)
        # The private half of the row: the same torrent, so the fallback service can be given it later.
        _sidecar_store(row['name'], kind, payload)
        return dict(ok=True, title=title, via='debrid')
    detail = '. '.join(reasons)
    raise ValueError(_scrub_key(detail) if reasons else 'no debrid key file is set up')


def _reply(handler, code, data):
    raw = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json'); handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Content-Length', str(len(raw))); handler.end_headers(); handler.wfile.write(raw)


POSTER = re.compile(r'/api/media/poster/([0-9a-f]{32})')


def poster(item_id):
    """A Jellyfin poster, fetched with the backend's own Jellyfin header (kept in the dashboard process, never sent out)."""
    import __main__
    hdr = getattr(__main__, '_JF_HDR', None)
    if not hdr: raise LookupError
    req = urllib.request.Request(f'http://192.168.0.17:8096/Items/{item_id}/Images/Primary?fillHeight=270&quality=82', headers=hdr)
    with urllib.request.urlopen(req, timeout=8) as r:
        kind = r.headers.get('Content-Type', '')
        body = r.read(2_000_000)
    if not kind.startswith('image/'): raise LookupError
    return kind, body


def _download_rpc(payload, timeout=45):
    """One JSON request to the n100 download worker, one JSON answer back.

    This is the only thing the download key can run on .3: its forced command is
    /usr/local/sbin/homelab-download-rpc, which accepts no shell, only a JSON request that the
    worker validates again before it touches anything. Commands: ping, start, check, stop.
    """
    cmd = ['ssh', '-i', _DOWNLOAD_KEY, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
           '-o', 'UserKnownHostsFile=' + _PROXMOX_KNOWN, '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=6', '-n', _PROXMOX_TARGET, json.dumps(payload, separators=(',', ':'))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('the download worker on the n100 did not answer in time')
    text = (r.stdout or '').strip()
    if text:
        try:
            out = json.loads(text.splitlines()[-1])
        except ValueError:
            out = None
        if isinstance(out, dict):
            if out.get('ok'):
                return out
            raise RuntimeError(str(out.get('error') or 'the download worker refused')[:200])
    raise RuntimeError((r.stderr or 'the download worker did not answer').strip()[:200] or 'no answer')


# The same rules the n100 worker enforces, applied here first so the panel can explain a refusal.
# The worker (homelab_download_lib.py) has the last word; tests/test_debrid_parity.py keeps the two equal.
_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# A TorBox nexus link is /dld/<uuid>?token=...: the real file name is only in the server's own
# Content-Disposition header. Some CDNs answer HEAD with no headers at all, so the probe is a GET
# for the first byte only (Range: bytes=0-0), whose body is thrown away.
_CONTENT_DISPOSITION = re.compile(r'filename\*\s*=\s*([A-Za-z0-9#_-]+)\'\'([^;]+)', re.I)
_FILENAME_PLAIN = re.compile(r'filename\s*=\s*(?:"([^"]*)"|([^";]+))', re.I)
_PROBE_REDIRECTS = 4


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed one hop at a time, so every hop passes _safe_url (https, not the LAN)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_opener():
    opener = urllib.request.build_opener(_NoRedirect)
    opener.addheaders = [('User-Agent', 'homelab-dashboard/1.0')]
    return opener


def _name_from_url(url):
    """The file name in the link. Decoded before the split, so %2F cannot become a directory separator."""
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path).rsplit('/', 1)[-1].strip()


def _safe_url(url):
    """Structure only: https, a real host, a path. Whether that host is on this LAN is the
    worker's call (it has DNS and the LAN list); the dashboard never runs a lookup."""
    if not isinstance(url, str) or len(url) > 4000:
        raise ValueError('paste a direct https download link')
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != 'https' or not parts.netloc or not parts.path.strip('/'):
        raise ValueError('paste a direct https download link')
    if _lan_host(parts.hostname or ''):
        raise ValueError('this link points at the LAN, not at a public download')
    return url.strip()


# Same LAN rule as the worker (scripts/homelab_download_lib.py), without any lookup: this fleet
# never downloads from itself. What resolves to the LAN through DNS is the worker's call.
_LAN_NAMES = ('localhost', 'localhost.localdomain', 'pi.hole', 'proxmox', 'unraid', 'agents', 'lan')
_LAN_STARTS = ('10.', '127.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.',
               '172.22.', '172.23.', '192.168.', '100.64.', '169.254.', '0.')


def _lan_host(host):
    host = (host or '').lower()
    if host in _LAN_NAMES or host.startswith(_LAN_STARTS):
        return True
    try:
        import ipaddress
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _safe_name(name):
    if not isinstance(name, str) or len(name) > 200 or not name.strip():
        raise ValueError('the link does not end in a usable file name')
    name = name.strip()
    if name in ('.', '..') or name.startswith(('.', '-')) or _NAME_BAD.search(name):
        raise ValueError('the file name has characters a download cannot use')
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT or not stem.strip('. '):
        raise ValueError('this link is not a media file (.mkv, .mp4, .epub, ...)')
    return name


def _media_name(name):
    """A file name a CDN answered with, checked against the same rules _safe_name uses before it is
    trusted: no separators, no leading dot or dash, a real stem, a known media extension."""
    if not isinstance(name, str):
        return None
    name = urllib.parse.unquote(name.strip())
    if not name or len(name) > 200 or name in ('.', '..') or name.startswith(('.', '-')) or _NAME_BAD.search(name):
        return None
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT or not stem.strip('. '):
        return None
    return name


def _header_name(headers):
    """The file name in a Content-Disposition header. RFC 5987 (filename*=UTF-8''...) wins,
    because it carries the real characters; the plain filename= is the fallback."""
    raw = (headers.get('Content-Disposition') or '').strip()
    if not raw:
        return None
    m = _CONTENT_DISPOSITION.search(raw)
    if m:
        return urllib.parse.unquote(m.group(2).strip().rstrip(';').strip('"').strip())
    m = _FILENAME_PLAIN.search(raw)
    if m:
        return (m.group(1) if m.group(1) is not None else m.group(2) or '').strip()
    return None


def _discover_name(url):
    """Ask the server what the file is called, for links whose path is an id and not a name.

    Every redirect hop is re-checked with _safe_url before it is requested. The reply only has to
    name a media file: the worker on .3 validates the name again before it writes anything.
    Raises ValueError with what stopped us, so the panel can say why.
    """
    target, last_error = url, None
    for _ in range(_PROBE_REDIRECTS + 1):
        try:
            req = urllib.request.Request(target, headers={'Range': 'bytes=0-0'})
            with _probe_opener().open(req, timeout=15) as r:
                found = _media_name(_header_name(r.headers))
                if found:
                    return found
                last_error = 'the server did not say what the file is called'
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                target = _safe_url(urllib.parse.urljoin(target, exc.headers.get('Location') or ''))
                continue
            last_error = 'the server refused the name lookup (HTTP %d)' % exc.code
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            last_error = 'the download server did not answer the name lookup'
            break
    raise ValueError(last_error or 'the link does not name a media file')


def _safe_show_dir(title):
    if not isinstance(title, str):
        raise ValueError('the show name cannot be used as a folder')
    folder = re.sub(r'\s+', ' ', title).strip(' .-')
    if not folder or len(folder) > 80 or _NAME_BAD.search(folder) or folder in ('.', '..'):
        raise ValueError('the show name cannot be used as a folder')
    return folder


def _norm(path):
    return os.path.normpath(path.rstrip('/'))


def _safe_dest(dest):
    """Movies, or one show folder under Shows. No '..', no backslash, no relative path, no symlink games.
    The worker repeats this with realpath() on .3, where the share is actually mounted."""
    if not isinstance(dest, str) or not dest.startswith('/') or chr(92) in dest or '\x00' in dest:
        raise ValueError('the download folder must be a full path')
    if any(part in ('.', '..') for part in dest.split('/')):  # refuse traversal, do not normalize it away
        raise ValueError('the download folder cannot contain . or ..')
    norm = _norm(dest)
    if len(norm) > 140:
        raise ValueError('the download folder has a strange path in it')
    if norm != MOVIES_DIR and not norm.startswith(SHOWS_DIR + '/'):  # Shows itself is not a download folder
        raise ValueError('the download folder must be Movies or a show folder under Shows')
    return norm


_TECH_TAIL = {'1080p', '2160p', '1440p', '720p', '480p', '4k', 'uhd', 'hd', 'x264', 'x265', 'h264', 'h265',
              'hevc', 'av1', 'hdr', 'hdr10', 'hdr10+', '10bit', '8bit', 'dts', 'dts-hd', 'truehd', 'ac3',
              'eac3', 'atmos', 'remux', 'proper', 'internal', 'repack', 'multi', 'dual', 'subtitles',
              'nfo', 'web-dl', 'webrip', 'bluray', 'bdrip', 'dvdrip', 'hdtv', 'imax'}


def _title_and_year(text):
    """A human title out of a file name, plus the release year the name states.
    Dots become spaces, cut at the release year, then at the first release tag, then drop tech tags.
    Keeps a title that is only a year (1917) and one that starts with a year (2001: A Space Odyssey).
    The year is kept as well: "Dune.2021" alone says film or series nothing, but "Dune 2021" does."""
    t = re.sub(r'[._]+', ' ', text or '').strip()
    year = None
    for y in reversed(list(YEAR.finditer(t))):
        head = t[:y.start()].strip()
        if head:
            t, year = head, int(y.group(0))
            break
    cut = _TAG_SEGMENT.search(t)              # 'Severance S02 2022' and 'Some Show 1x04' name no title past the tag
    if cut:
        head = t[:cut.start()].strip()
        if head:
            t = head
    words = t.split()
    while words and (words[-1].lower().strip('()-') in _TECH_TAIL or _TAG_SEGMENT.fullmatch(words[-1])):
        words.pop()
    return ' '.join(words).strip(' -()'), year


def _clean_title(text):
    return _title_and_year(text)[0]


def _norm_title(text):
    """Compare form of a title: 'Dune: Part One' and 'dune  part, one' both become 'dune part one'."""
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


_TAG_SEGMENT = re.compile(r'(?:^|(?<=\s))(?:[SsEe]\d{1,3}|\d{1,2}[xX]\d{1,3}|16x10|16x9|21x9|4x3)(?=$|\s)')


# Picture shapes that read as 16x10 = season 16 episode 10. They are named here because no amount of
# pattern cleverness tells an aspect ratio from a two-digit season and episode.
_ASPECT = {'16x9', '16x10', '16x11', '21x9', '21x10', '4x3', '3x2', '5x4', '4x5', '1x1', '2x1', '9x16', '3x4',
           '32x9', '64x36', '64x27', '48x27', '32x18', '96x54', '128x72', '256x144', '16x6', '16x7'}


def _after_tech_tag(stem, at):
    """True when a tech tag segment (2160p, x264, BluRay...) sits before position at.
    Something in that place is a release tag, so an NxN or E-9 match found there names nothing."""
    return any(seg.lower().strip('()-') in _TECH_TAIL for seg in stem.split('.')[:stem.count('.', 0, at)])


def classify(name):
    """From a media file name: film or series, the title, the year, and any season/episode the name states.

    Season/episode evidence in the name is never thrown away. ambiguous=True means the name states
    nothing on its own, so add_debrid asks the library and then SearXNG. A word like "season" inside a
    title is not evidence, and neither is a release tag after the tech tags (16x9, 21x9, E-9).
    """
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT:
        ext = '.mkv'
    title, year = _title_and_year(stem)

    def as_show(match, season, episode):
        head = _clean_title(stem[:match.start()]) or name
        return dict(kind='show', title=head, season=season, episode=episode, year=year, ext=ext,
                    show_dir=_safe_show_dir(head), ambiguous=False)

    m = SE_DOT.search(stem)
    if m:
        return as_show(m, int(m.group(1)), int(m.group(2)))
    m = SEASON_ONLY.search(stem)
    if m:
        return as_show(m, int(m.group(1)), None)
    for pat in (EP_WORD, EP_BARE, SE_X):        # an episode with no season stated: season 1
        m = pat.search(stem)
        if not m or (pat is not EP_WORD and _after_tech_tag(stem, m.start())):
            continue
        if pat is SE_X and m.group(0).lower() in _ASPECT:
            continue                            # 16x10 and 64x36 are picture shapes, not 1x10 and 6x43
        groups = m.groups()
        if len(groups) > 1:
            return as_show(m, int(groups[0]), int(groups[1]))
        return as_show(m, 1, int(groups[0]))
    return dict(kind='movie', title=title or stem, season=None, episode=None, year=year, ext=ext,
                show_dir=None, ambiguous=True)


_SHOW_HINT = re.compile(r'\b(?:tv|television|web|streaming|animated|limited|drama|comedy|reality|crime|'
                        r'medical|sci[- ]?fi|science[- ]?fiction|anthology|documentary|mini)[-. ]*series\b'
                        r'|\bminiser(?:es|ies)\b|\bepisodic\b|\bseason\s*\d+\s+(?:premiere|premiered|debuted)', re.I)
_MOVIE_HINT = re.compile(r'\b(?:film|movie)s?\b', re.I)
# A show page still talks about films - "based on the 1984 film", "the film series". Those say nothing
# about the title being searched, so they are erased before the page is counted as evidence.
_MOVIE_NOISE = re.compile(r'(?:based on|adaptation of|spin[- ]?off|original|upcoming|planned|cancelled|'
                          r'proposed)[^.]{0,90}?\b(?:film|movie)s?\b|\bfilm[- ]*series\b|\bfilm adaptations?\b', re.I)
# Franchise, novel, list-of-episodes and character pages hold both films and series: they answer nothing.
_RESULT_NOISE = re.compile(r'\b(?:franchise|novels?|books?|disambiguat\w*|list of|characters?|soundtrack|'
                           r'video game|awards?|film[- ]*series)\b', re.I)
_PAREN = re.compile(r'^(?P<main>[^()]+)\((?P<inside>[^()]+)\)')
_PAREN_YEAR = re.compile(r'\b(19\d\d|20\d\d)\b')


def searxng_results(title):
    """Top SearXNG snippets for a title: text to match against, never instructions to follow."""
    q = urllib.parse.urlencode({'q': title, 'format': 'json'})
    with urllib.request.urlopen(SEARXNG + '?' + q, timeout=8) as r:
        data = json.load(r)
    return [((res.get('title') or '') + ' ' + (res.get('content') or '')) for res in (data.get('results') or [])[:6]]


def _title_and_paren(text):
    """Split a result title into ('dune', '2021 film'): the site tail and any parenthetical are read off.
    A result with no parenthetical returns ('dune', ''), which is counted instead of read outright."""
    head = re.split(r'\s+[-|\u2013]\s*', text.strip())[0]
    m = _PAREN.match(head)
    if not m:
        return _norm_title(re.split(r'\s+(?:wikipedia|imdb|netflix|amazon|letterboxd)\b', head, flags=re.I)[0]), ''
    return _norm_title(m.group('main')), m.group('inside').lower()


def searxng_ground(title, year=None):
    """'show', 'movie' or None, from what the top results call the title.

    A result titled like the article for this exact title says what it is: 'Dune (2021 film)'
    answers film, 'The Crown (TV series)' answers series, and a stated year that is not the year in the
    file name means the page is about a different work, so it is dropped. Only when no page names the
    title outright are snippets counted, and then a tie answers nothing (the panel asks the library).
    A bare word like "season" never makes a show out of a film: the 2011 horror film Season of the
    Witch stays a film.
    """
    if not title:
        return None
    query = title + (' ' + str(year) if year else '')     # 'Dune 2021' finds the film page; 'Dune' finds both
    try:
        results = searxng_results(query)
    except Exception:
        return None
    want = _norm_title(title)
    shows = movies = 0
    for text in results:
        main, paren = _title_and_paren(text)
        if _RESULT_NOISE.search(paren) or _RESULT_NOISE.search(main):
            continue
        if main == want and paren:
            stated = _PAREN_YEAR.search(paren)
            if stated and int(stated.group(0)) != (year or int(stated.group(0))):
                continue                        # same name, a different work: answers nothing here
            if year and str(year) not in text:
                continue                        # the page does not speak about the year in the file name
            if re.search(r'\b(?:film|movie)s?\b', paren):
                return 'movie'
            if re.search(r'\bseries\b|\bminiser(?:es|ies)\b', paren):
                return 'show'
        body = _MOVIE_NOISE.sub(' ', text)
        shows += 1 if _SHOW_HINT.search(body) else 0
        movies += 1 if _MOVIE_HINT.search(body) else 0
    if shows > movies and shows >= 2:
        return 'show'
    if movies > shows and movies >= 2:
        return 'movie'
    return None


def _jellyfin_items(params, timeout=6):
    """Ask Jellyfin for library items, with the dashboard process's own header. None if it does not answer."""
    import __main__
    hdr = getattr(__main__, '_JF_HDR', None)
    if not hdr:
        return None                                  # no header in this process: nothing to ask
    req = urllib.request.Request(JF_ITEMS + '?' + urllib.parse.urlencode(params), headers=dict(hdr))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return None
    items = data.get('Items') if isinstance(data, dict) else None
    return items if isinstance(items, list) else None


def library_hint(title):
    """'show', 'movie' or None: what Ryan's library already calls this title.

    Jellyfin has already decided this for every title on the shelves, and it is the answer that matters
    (a new season of a show that lives in Shows must not land in Movies). Only an exact title match
    counts; a title owned both ways answers nothing and the search engine is asked instead.
    """
    if not title:
        return None
    items = _jellyfin_items({'SearchTerm': title, 'IncludeItemTypes': 'Movie,Series', 'Recursive': 'true',
                             'Limit': 25})
    if not items:
        return None
    want = _norm_title(title)
    kinds = set()
    for it in items:
        if not isinstance(it, dict) or _norm_title(it.get('Name') or '') != want:
            continue
        item_type = (it.get('Type') or '').lower()
        if item_type == 'movie':
            kinds.add('movie')
        elif item_type in ('series', 'season', 'episode'):
            kinds.add('show')
    return kinds.pop() if len(kinds) == 1 else None


def _migrate_row(d):
    # Rows from an older deploy miss fields the panel now uses; default them here so
    # every row reaching the frontend has the same shape.
    d.setdefault('kind', 'movie')
    d.setdefault('title', d['name'])
    d.setdefault('year', None)
    d.setdefault('kind_source', 'name')
    d.setdefault('url', None)      # the pasted link, kept only in the state file (it carries an access token)
    d.setdefault('retried', False)
    d.setdefault('scanned', False)
    d.setdefault('scan_error', None)
    d.setdefault('error', None)
    d.setdefault('grounded', None)
    d.setdefault('provider', None)
    d.setdefault('debrid', None)
    d.setdefault('tried', [])
    d.setdefault('prep', None)
    d.setdefault('unit', 'dl-' + d['name'])
    d.setdefault('state', 'unknown')
    d.setdefault('started', 0)
    d.setdefault('state_since', 0)
    d.setdefault('bytes', 0)
    d.setdefault('total_bytes', 0)
    d.setdefault('speed', 0)
    d.setdefault('eta', '')
    if d['state'] not in _DEBRID_STATE_WORDS:
        d['state'] = 'unknown'
    return d


def _load_state():
    """Read the download rows. Older deploys kept the file in /etc/homelab-dashboard; move it forward."""
    path = DEBRID_STATE if os.path.exists(DEBRID_STATE) else DEBRID_STATE_OLD
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        return {'downloads': []}
    # The rows now hold the pasted link, access token and all: the file must never be world-readable.
    # Fix an older file's mode the first time it is read (they were written 0644 before this rule).
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    rows = state.get('downloads') if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return {'downloads': []}
    return {'downloads': [_migrate_row(d) for d in rows if isinstance(d, dict) and d.get('name') and (d.get('dest') or d.get('state') == 'preparing')]}


def _save_state(state):
    """Write the rows in one step: tmp file, fsync, rename. Only ever called with _STATE_LOCK held.

    The mode is set on the tmp file before a single byte is written, so this file is never
    world-readable while it holds anything: it carries the pasted links with their access tokens.
    """
    state['downloads'] = state['downloads'][-_DEBRID_KEEP:]
    state['updated'] = int(time.time())
    os.makedirs(os.path.dirname(DEBRID_STATE), exist_ok=True)
    tmp = DEBRID_STATE + '.tmp'
    with open(tmp, 'w') as f:
        os.chmod(tmp, 0o600)
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DEBRID_STATE)


def _jellyfin_scan():
    """Ask Jellyfin to scan the library, using the dashboard process's own header. True only if it accepted."""
    import __main__
    hdr = getattr(__main__, '_JF_HDR', None) or {}
    req = urllib.request.Request(JF_SCAN, data=b'', method='POST', headers=dict(hdr))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        return e.code in (200, 202, 204)
    except Exception:
        return False


def plan_debrid(url, kind=None, name=None):
    """Everything a debrid download does before it starts the worker: link checks, the file name,
    film-or-series, the destination folder. Nothing is sent, nothing is written.

    kind ('movie' or 'show') wins over everything: it skips classify's ambiguity, the library and the
    search engine. Any other value is a ValueError (400). Returns the plan the start and the preview share.
    """
    if not isinstance(url, str) or not DEBRID_HTTP.fullmatch(url.strip()):
        raise ValueError('paste a direct https download link')
    if kind is not None and kind not in ('movie', 'show'):
        raise ValueError("kind must be 'movie' or 'show'")
    url = _safe_url(url)
    if name:
        # A debrid service names its own files: a torrent's file name is already known, and the
        # download link it hands out does not name the file at all.
        name = _safe_name(name)
    else:
        try:
            name = _safe_name(_name_from_url(url))  # _safe_name already requires a media extension
        except ValueError:
            # A TorBox nexus link is /dld/<uuid>?token=...: the name is only in the server's headers.
            try:
                name = _safe_name(_discover_name(url))
            except ValueError as exc:
                raise ValueError('this link does not name a media file (.mkv, .mp4, .epub, ...) - %s' % exc)
    info = classify(name)
    grounded = None
    if kind:
        source = 'user'
        if kind == 'show':
            info['kind'], info['show_dir'] = 'show', _safe_show_dir(info['title'])
        else:
            info['kind'], info['show_dir'] = 'movie', None
    else:
        source = 'name'                                   # the file name itself stated a season/episode
        if info['ambiguous']:
            grounded = library_hint(info['title'])
            if grounded:
                source = 'library'
            else:
                grounded = searxng_ground(info['title'], info['year'])
                if grounded:
                    source = 'search'
            if grounded == 'show':
                info['kind'], info['show_dir'] = 'show', _safe_show_dir(info['title'])
    show_dir = info['show_dir'] or _safe_show_dir(info['title'])
    dest = _safe_dest(MOVIES_DIR if info['kind'] == 'movie' else os.path.join(SHOWS_DIR, show_dir))
    return dict(url=url, name=name, info=info, dest=dest, source=source, grounded=grounded)


def preview_debrid(url, kind=None):
    """Say where a pasted link would be saved, without starting it: no worker call, no state file.

    The panel treats any answer without 'preview': true as a failed check, so it is always in the answer.
    """
    plan = plan_debrid(url, kind)
    info = plan['info']
    return dict(ok=True, preview=True, kind=info['kind'], title=info['title'], season=info['season'],
                episode=info['episode'], year=info['year'], dest=plan['dest'], name=plan['name'],
                kind_source=plan['source'], grounded=plan['grounded'])


def add_debrid(url, kind=None):
    """Start a debrid download on the n100 and remember it. Raises ValueError (bad input) or RuntimeError (worker)."""
    plan = plan_debrid(url, kind)
    info = plan['info']
    dest, name, source, grounded = plan['dest'], plan['name'], plan['source'], plan['grounded']
    out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'start', 'url': plan['url'], 'dest': dest, 'name': name})
    row = {'dest': dest, 'name': name, 'url': plan['url'], 'unit': out.get('unit') or 'dl-' + name, 'kind': info['kind'],
           'title': info['title'], 'season': info['season'], 'episode': info['episode'],
           'year': info['year'], 'kind_source': source,
           'state': out.get('state') if out.get('state') in _DEBRID_STATE_WORDS else 'downloading',
           'bytes': 0, 'started': int(time.time()), 'scanned': False, 'scan_error': None, 'error': None,
           'grounded': grounded}
    with _STATE_LOCK:
        state = _load_state()
        old = next((d for d in state['downloads'] if d['dest'] == dest and d['name'] == name), None)
        if old is not None:
            state['downloads'].remove(old)
        state['downloads'].append(row)
        _save_state(state)
    return dict(ok=True, kind=info['kind'], title=info['title'], season=info['season'], episode=info['episode'],
                grounded=grounded, kind_source=source, dest=dest, name=name, state=row['state'], unit=row['unit'],
                message=out.get('message') or ('started ' + name))


def _debrid_tick():
    """Refresh rows from the n100 worker, then ask Jellyfin once for everything that just finished.

    A row is only marked scanned after Jellyfin accepted the scan; otherwise scan_error is stored and
    the next tick tries again. Runs in the background loop, never in a request.
    """
    _age_out_rows()
    with _STATE_LOCK:
        state = _load_state()
        due = [d for d in state['downloads'] if d['state'] != 'preparing'
                and (d['state'] not in _DEBRID_SCAN_UNTIL or not d.get('scanned'))]
        jobs = [{'dest': d['dest'], 'name': d['name']} for d in due[-_DEBRID_CHECK_MAX:]]
    _debrid_prep_tick(time.time())
    if not jobs:
        return
    try:
        out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'check', 'jobs': jobs}, timeout=90)
    except Exception:
        return
    answers = {(j.get('dest'), j.get('name')): j for j in (out.get('jobs') or []) if isinstance(j, dict)}
    with _STATE_LOCK:
        state = _load_state()
        changed = False
        for d in state['downloads']:
            j = answers.get((d['dest'], d['name']))
            if not j:
                continue
            state_word = j.get('state') if j.get('state') in _DEBRID_STATE_WORDS else 'unknown'
            moved = (d.get('state') != state_word
                     or int(d.get('bytes') or 0) != int(j.get('bytes') or 0)
                     or int(d.get('total_bytes') or 0) != int(j.get('total_bytes') or 0))
            if moved:
                if d.get('state') != state_word:
                    d['state_since'] = int(time.time())
                d['state'], d['bytes'] = state_word, int(j.get('bytes') or 0)
                d['total_bytes'], d['speed'] = int(j.get('total_bytes') or 0), int(j.get('speed_bytes') or 0)
                d['eta'] = str(j.get('eta') or '')[:40]
                d['sampled'] = time.time()    # when these numbers were true: the panel moves the bar on from here
                changed = True
            if j.get('error') and d.get('error') != str(j['error'])[:200]:
                d['error'] = str(j['error'])[:200]
                changed = True
            if (d.get('retried') and d.get('state') == 'failed'
                    and _EXPIRED_LINK.search(str(j.get('error') or ''))):
                # aria2c refused the link itself after a retry: the access token has run out. Keep
                # the row failed, say it plainly, and drop the stored link so can_retry goes false.
                d['error'] = 'the link has expired; paste a fresh one'
                d['url'], d['retried'] = None, False
                changed = True
        ready = [d['name'] for d in state['downloads']
                 if d.get('state') in _DEBRID_SCAN_UNTIL and not d.get('scanned')]
        if changed:
            _save_state(state)
    if not ready:
        return
    # The library scan is an HTTP call of its own: it runs with the state lock free, so a slow
    # Jellyfin cannot block a panel read or another write for its whole timeout.
    ok = _jellyfin_scan()
    with _STATE_LOCK:
        state = _load_state()
        want = set(ready)
        touched = False
        for d in state['downloads']:
            if d['name'] in want and d.get('state') in _DEBRID_SCAN_UNTIL and not d.get('scanned'):
                if ok:
                    d['scanned'], d['scan_error'] = True, None
                else:
                    d['scan_error'] = 'the library scan did not start'
                touched = True
        if touched:
            _save_state(state)


# --- Flow step 2: an 'At debrid' row, watched at the debrid service by the tick -------------------
#
# A preparing row is not a download yet: its files are still being fetched by Real-Debrid or TorBox.
# The tick reads it at that service (never at the download worker), copies progress, seeders and the
# media file count into prep, chooses the files at Real-Debrid, and moves the torrent to the fallback
# service when Real-Debrid is dead on it. The magnet or .torrent bytes a row might need for that live
# in a sidecar file of their own: a panel row never carries them.

_DEBRID_PREP_READ = 10           # seconds between two reads of one preparing row
_DEBRID_PREP_ROWS = 6            # preparing rows read in one tick
_DEBRID_PREP_STALL = 6 * 3600    # no progress at Real-Debrid for this long: the fallback takes the torrent
_DEBRID_PREP_AGING = 24 * 3600   # neither service finished in this long: the row fails
_DEBRID_PREP_KEEP = 20
_DEBRID_FILES_MAX = 60                 # files of one torrent; more than this, the first 60 by name
_DEBRID_MIN_BYTES = 50 * 1024 * 1024   # a smaller media file is an extra when a bigger one exists
_DEBRID_HANDOVER = 2                   # files handed to the download worker per tick (flow step 3)
_RD_CALL_SPACING = 1.0                 # Real-Debrid: one call per second at least (250 a minute)
_TB_CALL_SPACING = 3.0                 # TorBox requestdl: throttled at about 20 a minute
_RD_TORRENT_DEAD = ('magnet_error', 'error', 'virus', 'dead')
_TB_TORRENT_DEAD = ('error', 'failed', 'expired', 'canceled')


def _sidecar_path():
    """The sidecar sits next to the state file, so it follows wherever the state file lives."""
    return DEBRID_STATE + '.prep'


def _sidecar_load():
    """The private half of the preparing rows: how to hand the same torrent to another service."""
    try:
        with open(_sidecar_path()) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _sidecar_save(data):
    """One write, 0600, in the same tmp-fsync-rename shape the state file uses: it can hold a magnet
    and 5 MB of torrent bytes."""
    sidecar = _sidecar_path()
    os.makedirs(os.path.dirname(sidecar), exist_ok=True)
    tmp = sidecar + '.tmp'
    with open(tmp, 'w') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, separators=(',', ':'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, sidecar)
    try:
        os.chmod(sidecar, 0o600)
    except OSError:
        pass


def _sidecar_store(name, kind, payload):
    """Remember the torrent of one preparing row, keyed by that row's name."""
    data = _sidecar_load()
    item = {'kind': kind}
    if kind == 'magnet':
        item['magnet'] = payload
    else:
        item['torrent'] = base64.b64encode(payload).decode()
    data[name] = item
    _sidecar_save(dict(list(data.items())[-_DEBRID_PREP_KEEP:]))


def _sidecar_payload(name):
    """(kind, payload) for one row, or None when nothing was stored for it."""
    item = _sidecar_load().get(name)
    if not isinstance(item, dict):
        return None
    if item.get('kind') == 'magnet' and item.get('magnet'):
        return 'magnet', item['magnet']
    if item.get('kind') == 'torrent' and item.get('torrent'):
        try:
            return 'torrent', base64.b64decode(item['torrent'])
        except Exception:
            return None
    return None


def _sidecar_forget(name):
    """Drop one row's torrent: its story is closed, so the bytes and the magnet go with it."""
    data = _sidecar_load()
    if data.pop(name, None) is not None:
        _sidecar_save(data)


def _row_provider(d):
    """Which service a row is at now. Rows step 1 wrote before this flow only imply it."""
    provider = d.get('provider')
    if provider in ('realdebrid', 'torbox'):
        return provider
    return 'torbox' if isinstance(d.get('torbox'), dict) else None


def _row_torrent_id(d):
    """The service's own torrent id for a row, from its debrid object or the older torbox one."""
    for holder in ('debrid', 'torbox'):
        item = d.get(holder)
        if isinstance(item, dict):
            for field in ('id', 'torrent_id'):
                value = item.get(field)
                if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                    return int(value)
    return None


def _rd_view(info):
    """One Real-Debrid torrent, in the shape the tick uses. progress becomes 0 to 1."""
    if not isinstance(info, dict):
        info = {}
    files = []
    for f in info.get('files') or []:
        if not isinstance(f, dict):
            continue
        path = str(f.get('path') or '')
        files.append({'id': f.get('id'), 'name': path.rsplit('/', 1)[-1], 'bytes': int(f.get('bytes') or 0),
                      'infected': False})
    try:
        progress = min(1.0, max(0.0, float(info.get('progress') or 0) / 100.0))
    except (TypeError, ValueError):
        progress = 0.0
    seeds = info.get('seeders')
    return {'status': str(info.get('status') or ''),
            'progress': progress,
            'seeds': int(seeds) if seeds is not None else None,
            'files': files,
            'links': [str(x) for x in (info.get('links') or []) if isinstance(x, str)],
            'selected': [f['id'] for f in (info.get('files') or []) if isinstance(f, dict) and f.get('selected')],
            'name': str(info.get('filename') or info.get('original_filename') or ''),
            'bytes': int(info.get('original_bytes') or info.get('bytes') or 0),
            'ready': str(info.get('status') or '') == 'downloaded'}


def _tb_view(data):
    """One TorBox torrent, in the same shape. Its progress is already 0 to 1; ready means the
    files are finished and present."""
    if not isinstance(data, dict):
        data = {}
    files = []
    for f in data.get('files') or []:
        if not isinstance(f, dict):
            continue
        files.append({'id': f.get('id'),
                      'name': str(f.get('name') or f.get('short_name') or ''),
                      'bytes': int(f.get('size') or 0),
                      'infected': bool(f.get('infected'))})
    try:
        progress = min(1.0, max(0.0, float(data.get('progress') or 0)))
    except (TypeError, ValueError):
        progress = 0.0
    seeds = data.get('seeds')
    return {'status': str(data.get('download_state') or ''),
            'progress': progress,
            'seeds': int(seeds) if seeds is not None else None,
            'files': files,
            'links': [],
            'name': str(data.get('name') or ''),
            'bytes': int(data.get('size') or 0),
            'ready': bool(data.get('download_finished') and data.get('download_present'))}


def _media_pick(files):
    """The media files of a torrent, in the order the worker gets them.

    A name must end in a known media extension; a name with 'sample' in it goes, and so does a file
    TorBox marked infected. When at least one media file is 50 MB or bigger, the smaller ones go too:
    they are trailers and bonus tracks. More than 60: the first 60 by name, and how many were left out.
    """
    keep = [f for f in files
            if isinstance(f.get('name'), str) and _is_media_name(f['name'])
            and 'sample' not in f['name'].lower() and not f.get('infected')]
    if not keep:
        return [], 0, 0
    biggest = max(int(f.get('bytes') or 0) for f in keep)
    small = 0
    if biggest >= _DEBRID_MIN_BYTES:
        big = [f for f in keep if int(f.get('bytes') or 0) >= _DEBRID_MIN_BYTES]
        if big:
            small = len(keep) - len(big)
            keep = big
    keep.sort(key=lambda f: f['name'].lower())
    overflow = max(0, len(keep) - _DEBRID_FILES_MAX)
    return keep[:_DEBRID_FILES_MAX], small, overflow


def _is_media_name(name):
    return os.path.splitext(name)[1].lower() in MEDIA_EXT


def _read_provider(provider, torrent_id):
    """One read of a preparing row's torrent at its own service. Raises what the service raises."""
    if provider == 'realdebrid':
        return _rd_view(realdebrid('GET', '/torrents/info/%d' % torrent_id))
    ans = torbox('GET', '/torrents/mylist?id=%d&bypass_cache=true' % torrent_id)
    data = ans.get('data') if isinstance(ans, dict) else None
    entry = (data[0] if isinstance(data, list) and data else {})
    return _tb_view(entry)


def _prep_display(provider):
    return 'Real-Debrid' if provider == 'realdebrid' else 'TorBox'


_RD_CALL_AT = 0.0
_TB_CALL_AT = 0.0


def _sleep(seconds):
    """One wait between service calls. A test replaces it to see the spacing without feeling it."""
    time.sleep(seconds)


def _pace(kind, now):
    """Hold the distance that service demands between its own calls: Real-Debrid 250 a minute,
    TorBox's download-link call about 20 a minute. Returns the time the call now happens at."""
    global _RD_CALL_AT, _TB_CALL_AT
    if kind == 'rd':
        spacing, last = _RD_CALL_SPACING, _RD_CALL_AT
    else:
        spacing, last = _TB_CALL_SPACING, _TB_CALL_AT
    if last and now - last < spacing:
        wait = spacing - (now - last)
        _sleep(wait)
        now = now + wait
    if kind == 'rd':
        _RD_CALL_AT = now
    else:
        _TB_CALL_AT = now
    return now


def _rd_link(torrent_id, hoster_link, now):
    """Real-Debrid's own step: one hoster link becomes one https download link."""
    now = _pace('rd', now)
    ans = realdebrid('POST', '/unrestrict/link', fields={'link': hoster_link})
    link = str(ans.get('download') or '') if isinstance(ans, dict) else ''
    if not link:
        raise RuntimeError('Real-Debrid did not answer with a download link')
    return link, str(ans.get('filename') or ''), int(ans.get('filesize') or 0)


def _tb_link(torrent_id, file_id, now):
    """TorBox's download link for one file. The key rides in the token parameter, and redirect=true is
    never asked for: that answer is a permanent link with the key inside it."""
    key = _torbox_key()
    if not key:
        raise RuntimeError('the debrid service is not set up')
    now = _pace('tb', now)
    ans = torbox('GET', '/torrents/requestdl?token=%s&torrent_id=%d&file_id=%d' % (key, torrent_id, file_id))
    data = ans.get('data') if isinstance(ans, dict) else None
    link = str(data.get('download') or data.get('link') or '') if isinstance(data, dict) else str(data or '')
    if not DEBRID_HTTP.fullmatch(link):
        raise RuntimeError('TorBox did not answer with a download link')
    return link, '', 0


_ARCHIVE_EXT = ('.rar', '.zip', '.7z', '.tar', '.gz', '.001', '.r00')
_TORRENT_DELETE_WHEN_DONE = False   # a finished torrent stays in the account: Ryan may want the files again


def _plan_handover(row, file_name, link):
    """Plan one torrent file as a pasted link would be planned, and fix the names that need the release.

    A provider's file name is handed straight to plan_debrid: the download link it gives out names
    nothing. When the name is only a tag (S02E03.mkv from a pack), the show, the year and film-or-series
    come from the torrent's release name, while the season and episode stay as the file name stated them.
    """
    plan = plan_debrid(link, name=file_name)
    info = plan['info']
    stem = os.path.splitext(file_name)[0]
    bare = not info['title'] or info['title'].lower() == file_name.lower() or info['title'].lower() == stem.lower()
    if not bare:
        return plan
    release = str(row.get('release') or row.get('title') or '').rsplit('/', 1)[-1]
    rel = classify(os.path.basename(release))
    title = rel['title'] or info['title']
    year = rel['year'] or info['year']
    # The file name stated a season or an episode: that is series evidence whatever the release name
    # says, so the release only lends its title and its year.
    if info['kind'] == 'show' or rel['kind'] == 'show':
        show_dir = _safe_show_dir(title)
        plan['dest'] = _safe_dest(os.path.join(SHOWS_DIR, show_dir))
        plan['info'] = dict(info, kind='show', title=title, year=year, show_dir=show_dir)
    else:
        plan['dest'] = _safe_dest(MOVIES_DIR)
        plan['info'] = dict(info, kind='movie', title=title, year=year, show_dir=None)
    plan['source'] = 'torrent'
    return plan


def _rd_link_for(view, torrent_id, wanted, now):
    """Real-Debrid's hoster link for one selected file: links[] follows the selected ids, and the
    answer is checked against that file by name and size."""
    selected, links = view.get('selected') or [], view.get('links') or []
    # links[] follows the selected file ids in order, so file N sits at index N-1
    index = selected.index(wanted['id']) if wanted.get('id') in selected else None
    if index is None or index >= len(links):
        raise ValueError('Real-Debrid gave no hoster link for this file')
    link, name, size = _rd_link(torrent_id, links[index], now)   # links[index] is the hoster link a retry re-asks
    answer_name = name.lower()
    if answer_name.endswith(_ARCHIVE_EXT):
        raise ValueError('packed into an archive: %s' % name[-8:])
    if answer_name != str(wanted['name']).lower() and size and size != int(wanted.get('bytes') or 0):
        raise ValueError('Real-Debrid answered with a different file')
    return links[index], link, name, size


def _retry_link(d):
    """A fresh download link for a row that came from a torrent.

    Real-Debrid is asked to unrestrict the hoster link that row kept; TorBox answers requestdl again from the
    torrent and file ids it kept. Both wait their turn (_pace), and neither link is ever shown to the panel.
    """
    debrid, provider = d.get('debrid'), _row_provider(d) or 'realdebrid'
    torrent_id = _row_torrent_id(d)
    if not isinstance(debrid, dict) or not torrent_id:
        raise ValueError('that row kept no service call to re-ask; paste a fresh link')
    if provider == 'realdebrid':
        if not debrid.get('link'):
            raise ValueError('that row kept no Real-Debrid link to re-ask; paste a fresh link')
        link, _name, _size = _rd_link(torrent_id, str(debrid['link']), time.time())
    else:
        if not debrid.get('file'):
            raise ValueError('that row kept no TorBox file id to re-ask; paste a fresh link')
        link, _name, _size = _tb_link(torrent_id, int(debrid['file']), time.time())
    return _safe_url(link)


def _row_key(name):
    """A row's key, which is not always a file name: a watching row is called rd-9 or torbox-3 until its
    files arrive. Short, one line, no path - that is all Cancel needs, since the name only picks the row."""
    name = str(name).strip()
    if not 1 <= len(name) <= 200 or any(c in name for c in ('\r', '\n', '/', chr(92))) or '..' in name:
        raise ValueError('that name cannot be a row in the list')
    return name


def cancel_debrid(name):
    """Cancel on an 'At debrid' row: free the torrent at its service, then drop the row and its sidecar.

    Only a preparing row answers to Cancel: a row that already handed its files over has download rows of their
    own, and Stop is the control for those. A refusal from the service does not keep the row here - the panel
    list is what Cancel clears, and the torrent itself can still be freed in the service's own site - so the
    reason rides in the reply text. The service call sits outside _STATE_LOCK, as everywhere in this file.
    """
    name = _row_key(name)
    with _STATE_LOCK:
        row = next((d for d in _load_state()['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that row is not in the list anymore')
        if row.get('state') != 'preparing':
            raise ValueError('that row is %s; Cancel is for a row still waiting at its service' % (row.get('state') or 'unknown'))
        provider, torrent_id = _row_provider(row), _row_torrent_id(row)
    reason = None
    if torrent_id:
        try:
            _free_torrent(provider, torrent_id)
        except Exception as exc:
            reason = _scrub_key(str(exc))
    with _STATE_LOCK:
        state = _load_state()
        keep = [d for d in state['downloads'] if d['name'] != name]
        gone = len(state['downloads']) - len(keep)
        if gone:
            state['downloads'] = keep
            _save_state(state)
    _sidecar_forget(name)
    message = 'stopped ' + name
    if reason:
        message += ('; %s did not free its torrent: %s' % ('Real-Debrid' if provider == 'realdebrid' else 'TorBox', reason))
    return dict(ok=True, name=name, message=message, cancelled=gone)


def _free_torrent(provider, torrent_id):
    """Ask the service to drop a torrent it is still holding for this row."""
    if provider == 'realdebrid':
        realdebrid('DELETE', '/torrents/delete/%d' % int(torrent_id), timeout=20)
        return
    torbox('POST', '/torrents/controltorrent', json_body={'torrent_id': int(torrent_id), 'operation': 'delete'})


def _prep_reasons(d):
    """Every reason the services gave this row, oldest first, for the text Ryan sees."""
    out = []
    for part in (d.get('error'), d.get('prep_error')):
        if part and str(part) not in out:
            out.append(str(part)[:200])
    return out


def _debrid_prep_tick(now):
    """Flow steps 2 and 3 once: at most 6 preparing rows read, no row twice inside 10 s, and at
    most 2 files handed to the download worker for the whole tick. The two limits are separate: a row
    is still read once the hand-over budget is spent, so its progress keeps moving in the panel.
    Runs in the tick loop."""
    with _STATE_LOCK:
        rows = [d for d in _load_state()['downloads'] if d.get('state') == 'preparing'][-_DEBRID_PREP_ROWS:]
    budget = _DEBRID_HANDOVER
    for row in rows:
        try:
            if now - float(row.get('read') or 0) >= _DEBRID_PREP_READ:
                budget = max(0, budget - _prep_refresh(row['name'], now, budget))
        except Exception:
            pass   # one bad row must not stop the others, and never stop the download tick


def _prep_close(row, now, text, solo=False):
    """End a preparing row: it failed, and the reason is what the services said about it.

    Every reason the row collected goes in, oldest first, so a row that moved on to the fallback still
    says why the first service let go. solo=True keeps only this text: the 24 hour clock says it all.
    """
    parts = [text] if solo else (_prep_reasons(row) + ([text] if text and text not in _prep_reasons(row) else []))
    row['state'], row['state_since'] = 'failed', int(now)
    row['error'] = _scrub_key('; '.join(str(p) for p in parts)) or 'the debrid service did not finish this torrent'
    _sidecar_forget(row['name'])


def _prep_note(row, text):
    """Keep a service's reason on the row without ending its story: the 24 hour clock still runs."""
    if text:
        row['prep_error'] = str(text)[:200]


def _prep_update(name, mutate, add_rows=None):
    """Write one preparing row: it may grow, retire, or hand off new download rows beside it.

    The state lock is held only around the rows themselves; every service call and every worker call
    happened before this.
    """
    with _STATE_LOCK:
        state = _load_state()
        row = next((d for d in state['downloads'] if d['name'] == name), None)
        if row is None or row.get('state') != 'preparing':
            return False
        mutate(row)
        if add_rows:
            keys = {(d['dest'], d['name']) for d in add_rows}
            state['downloads'] = [d for d in state['downloads'] if (d.get('dest'), d.get('name')) not in keys]
            state['downloads'].extend(add_rows)
        if row.pop('_drop', False):
            state['downloads'] = [d for d in state['downloads'] if d['name'] != name]
        _save_state(state)
    return True


def _handover_files(row, view, media, provider, now, limit):
    """Flow step 3: up to two files of a ready torrent go to the download worker in one tick.

    Each link is asked for at the service at the last moment, because debrid links expire; the file is
    then planned as if that link had been pasted (with the service's own file name) and started on the n100.
    The new row keeps the provider's link so a retry can ask for a fresh one (flow step 4), and it keeps
    no url of its own: aria2c is told the link only when it runs, and those links go dead.
    """
    torrent_id = _row_torrent_id(row)
    handed = set(row.get('handed') or [])
    todo = [f for f in media if f.get('id') not in handed][:max(0, int(limit))]
    out, ids, errors, archive = [], [], [], None
    for wanted in todo:
        try:
            if provider == 'realdebrid':
                service, link, answer_name, _size = _rd_link_for(view, torrent_id, wanted, now)
            else:
                # TorBox re-asks by ids, so no service link of its own is kept
                service = None
                link, answer_name, _size = _tb_link(torrent_id, wanted['id'], now)
            plan = _plan_handover(row, wanted['name'], link)
            info = plan['info']
            started = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'start', 'url': link,
                                     'dest': plan['dest'], 'name': plan['name']})
            out.append({'dest': plan['dest'], 'name': plan['name'],
                        'unit': started.get('unit') or ('dl-' + plan['name']),
                        'kind': info['kind'], 'title': info['title'], 'season': info['season'],
                        'episode': info['episode'], 'year': info['year'], 'kind_source': plan['source'],
                        'state': started.get('state') if started.get('state') in _DEBRID_STATE_WORDS else 'downloading',
                        'bytes': 0, 'total_bytes': int(wanted.get('bytes') or 0), 'speed': 0, 'eta': '',
                        'started': int(now), 'scanned': False, 'scan_error': None, 'error': None,
                        'grounded': None, 'provider': provider,
                        'debrid': {'id': torrent_id, 'file': wanted.get('id'), 'link': service}})
            ids.append(wanted.get('id'))
        except ValueError as exc:
            reason = _scrub_key(str(exc))
            if reason.startswith('packed into an archive'):
                archive = reason
                break
            errors.append(reason)
        except Exception as exc:
            errors.append(_scrub_key(str(exc)))
    return out, ids, errors, archive


def _prep_refresh(name, now, budget=2):
    """One preparing row: read it at its service, then write what that answer said.

    Anything else a service call is needed for - choosing the files at Real-Debrid, moving the torrent
    to the fallback - happens before the row is written: the state lock is never held across a call
    to Real-Debrid or TorBox.
    """
    with _STATE_LOCK:
        row = next((d for d in _load_state()['downloads'] if d['name'] == name), None)
        row = dict(row) if row is not None else None
    if row is None or row.get('state') != 'preparing':
        return 0    # the row left the list (Cancel, Clear, another tick): it handed over nothing
    provider, torrent_id = _row_provider(row), _row_torrent_id(row)
    if provider is None or torrent_id is None:
        _prep_update(name, lambda d: _prep_close(d, now, 'this row names no torrent to watch'))
        return 0
    view, read_error = None, None
    try:
        view = _read_provider(provider, torrent_id)
    except Exception as exc:
        read_error = _scrub_key(str(exc))
    if view is None:
        _prep_update(name, lambda d: _prep_note(d, read_error))
        return 0

    media, small, overflow = _media_pick(view['files'])
    select_ids, select_error = None, None
    if provider == 'realdebrid' and view['status'] == 'waiting_files_selection':
        # Real-Debrid starts nothing until the files are chosen: hand it the media ids now.
        if not media:
            select_error = 'no media file in this torrent'
        else:
            ids = ','.join(str(f['id']) for f in media if f.get('id') is not None)
            try:
                realdebrid('POST', '/torrents/selectFiles/%d' % torrent_id, fields={'files': ids})
                select_ids = [f['id'] for f in media]
            except Exception as exc:
                select_error = _scrub_key(str(exc))

    # What this answer means. Only a Real-Debrid row can be handed to the fallback: a TorBox row that
    # goes wrong has nowhere left to go, and the 24 hour clock ends either kind.
    close_it, move, why, solo = False, False, None, False
    started = float(row.get('started') or 0)
    if started > 1e9 and now - started > _DEBRID_PREP_AGING:
        close_it, solo = True, True
        why = 'the debrid services did not finish this torrent in 24 hours'
    elif provider == 'realdebrid':
        if view['status'] in _RD_TORRENT_DEAD:
            move, why = True, 'Real-Debrid: %s' % view['status']
        elif select_error:
            move, why = True, 'Real-Debrid: %s' % select_error
        elif (view['progress'] <= float(row.get('prep_progress') or 0)
              and now - float(row.get('prep_at') or row.get('started') or now) > _DEBRID_PREP_STALL):
            move, why = True, 'Real-Debrid: no progress for 6 hours'
    elif view['status'] in _TB_TORRENT_DEAD:
        close_it, why = True, 'TorBox: %s' % view['status']

    # Ready: the files go to the download worker, at most two per tick, links asked for only now.
    started_rows, started_ids, hand_errors, archive = [], [], [], None
    if view['ready'] and not move and not close_it:
        started_rows, started_ids, hand_errors, archive = _handover_files(row, view, media, provider, now, budget)
        if archive:
            # One hoster link for several files: the torrent is packed up. Real-Debrid will not split it,
            # so the fallback service is given the same torrent and nothing is handed over here.
            move, why = True, 'Real-Debrid: %s' % archive
            started_rows, started_ids, hand_errors = [], [], []

    # Moving to the fallback is its own pair of calls, made before the row changes.
    moved_id, moved_error = None, None
    if move:
        payload = _sidecar_payload(name)
        if payload is None:
            moved_error = 'the torrent for the fallback is gone'
        else:
            try:
                realdebrid('DELETE', '/torrents/delete/%d' % torrent_id)
            except Exception:
                pass   # already gone at Real-Debrid: TorBox can take the torrent either way
            try:
                moved_id, moved_hash = _tb_add_torrent(payload[0], payload[1])
                if not moved_id:
                    moved_id, moved_error = None, 'TorBox did not answer with a torrent id'
            except Exception as exc:
                moved_id, moved_error = None, _scrub_key(str(exc))

    def apply(d):
        d['read'] = now
        prep = dict(d.get('prep') or {})
        prep['progress'] = view['progress']
        prep['seeds'] = view['seeds']
        prep['files'] = len(media)
        prep['provider'] = _prep_display(_row_provider(d) or provider)
        d['prep'] = prep
        d['ready'] = bool(view['ready'])
        if small:
            d['files_small'] = small
        if overflow:
            d['files_cut'] = overflow
        if select_ids is not None:
            d['selected'] = select_ids
        if read_error:
            _prep_note(d, read_error)
            return
        if started_ids:
            d['handed'] = list(d.get('handed') or []) + started_ids
            left = len(media) - len(d['handed'])
            d['prep']['files'] = max(0, left)
            if left <= 0 and not move and not close_it:
                # Every file of this torrent is on its way to the share: the row that watched it can go.
                d['_drop'] = True
                _sidecar_forget(d['name'])
        if hand_errors:
            _prep_note(d, hand_errors[0])
        if close_it:
            _prep_close(d, now, why, solo=solo)
            return
        if move:
            if moved_error:
                _prep_close(d, now, '%s; %s' % (why or '', moved_error))
                return
            d['provider'] = 'torbox'
            d['debrid'] = {'id': moved_id, 'hash': _row_hash(d)}
            d['tried'] = list(d.get('tried') or []) + ['torbox']
            d['prep']['provider'] = 'TorBox'   # the row now lives at the fallback service
            d['error'] = _scrub_key(why or '')
            _prep_note(d, why)
            return
        if view['progress'] > float(d.get('prep_progress') or 0):
            d['prep_progress'], d['prep_at'] = view['progress'], now

    _prep_update(name, apply, add_rows=started_rows)
    return len(started_rows)


def _aged_out(row, now) -> bool:
    """True when a download's story has been closed for 7 days.

    A running download never ages out, however old it is: it is still doing something, and its row
    is how you watch it. The clock is the last state change (state_since), else the row's own start.
    A row with no usable timestamp is kept, and so is one whose start is a placeholder from an
    older state file, because there is nothing honest to age against.
    """
    if row.get('state') == 'downloading':
        return False
    since = row.get('state_since') or row.get('started')
    try:
        since = float(since)
    except (TypeError, ValueError):
        return False
    if since < 1e9:
        return False
    return now - since > _DEBRID_AGE


def _age_out_rows() -> int:
    """Drop aged rows, and only when the list actually changes.

    This is its own step of the tick, before anything needs a worker check: a finished, scanned
    download is not due for anything, so waiting for another row to keep the loop busy would leave
    old history on the panel forever.
    """
    with _STATE_LOCK:
        state = _load_state()
        now = time.time()
        kept = [d for d in state['downloads'] if not _aged_out(d, now)]
        gone = len(state['downloads']) - len(kept)
        if gone:
            state['downloads'] = kept
            _save_state(state)
    return gone


def _debrid_rows(state):
    """Rows for the homepage, from the state file only: no SSH per row. The tick loop keeps them fresh."""
    out = []
    for d in state['downloads'][-_DEBRID_ROWS:][::-1]:
        st = d.get('state') or 'unknown'
        out.append(dict(name=d['name'], kind=d['kind'], title=d['title'], season=d.get('season'),
                        episode=d.get('episode'), dest=d['dest'], state=st, done=st in _DEBRID_SCAN_UNTIL,
                        bytes=int(d.get('bytes') or 0), total_bytes=int(d.get('total_bytes') or 0),
                        speed=int(d.get('speed') or 0), eta=d.get('eta') or '',
                        scanned=bool(d.get('scanned')), scan_error=d.get('scan_error'),
                        error=d.get('error'), grounded=d.get('grounded'), started=d.get('started'),
                        prep=d.get('prep'),
                        sampled=d.get('sampled'),
                        # The link stays inside the backend: the panel only learns whether a retry is possible.
                        can_retry=(bool(d.get('url')) or bool(d.get('debrid'))) and st in _RETRY_STATES,
                        age=max(0, int(time.time()) - int(d.get('started') or 0))))
    return out


def stop_debrid(name):
    """Stop one running download on the n100 by file name, so a stuck row can be cleared.

    The partial file stays on the share: pasting the same link again resumes it. Raises
    ValueError (not in the list, bad name) or RuntimeError (the worker refused).

    The state lock is held around the rows only, never around the worker call: one Stop may wait
    the full 40 s, and it must not freeze a panel read or the tick loop for that long. The row is
    looked up again after the call, because the tick loop can rewrite the state meanwhile.
    """
    name = _safe_name(name)
    with _STATE_LOCK:
        if not any(d['name'] == name for d in _load_state()['downloads']):
            raise ValueError('that download is not in the list anymore')
    out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'stop', 'name': name}, timeout=40)
    with _STATE_LOCK:
        state = _load_state()
        row = next((d for d in state['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that download left the list while it was being stopped')
        row['state'], row['state_since'] = 'stopped', int(time.time())
        row['scanned'], row['scan_error'] = True, None   # stopped on purpose: partial file, no scan (a retry resets this)
        _save_state(state)
    return dict(ok=True, name=name, unit=out.get('unit') or 'dl-' + name,
                message=out.get('message') or ('stopped ' + name))


def retry_debrid(name):
    """Restart a failed, stopped or interrupted download with the link its row stored.

    The same start call add_debrid() makes: the worker revalidates everything and aria2c continues
    the partial file (its .aria2 control file is what makes --continue safe). A row that came from a torrent
    stores no url of its own - those links go dead - so retry asks the provider it names for a fresh one.
    Raises ValueError (no such row, nothing to re-ask, or a state that cannot be retried) or RuntimeError
    (the worker, or the service refusing).

    The state lock is held around the rows only, never around the worker call (same pattern as
    stop_debrid); the row is looked up again after the call because the tick loop can rewrite it.
    """
    name = _safe_name(name)
    with _STATE_LOCK:
        row = next((d for d in _load_state()['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that download is not in the list anymore')
        if row.get('state') not in _RETRY_STATES:
            raise ValueError('that download is %s; only failed, stopped or interrupted can be retried' % row.get('state'))
        if not row.get('url') and not _row_torrent_id(row):
            raise ValueError('no link is stored for that download; paste a fresh one')
        url, dest = row.get('url'), row['dest']
    if not url:
        url = _retry_link(row)   # fresh from the provider this row came from, at that service's own pace
    out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'start', 'url': url, 'dest': dest, 'name': name})
    state_word = out.get('state') if out.get('state') in _DEBRID_STATE_WORDS else 'downloading'
    with _STATE_LOCK:
        state = _load_state()
        row = next((d for d in state['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that download left the list while it was being retried')
        row['state'], row['state_since'] = state_word, int(time.time())
        row['error'], row['scanned'], row['scan_error'] = None, False, None   # open again: the scan queue re-arms
        row['started'] = int(time.time())
        row['retried'] = True   # the next failure is read against _EXPIRED_LINK with this row in mind
        row['unit'] = out.get('unit') or row.get('unit') or 'dl-' + name
        _save_state(state)
    return dict(ok=True, name=name, state=state_word,
                message=out.get('message') or ('restarted ' + name))


def clear_debrid() -> dict:
    """Empty the homepage list on purpose (Clear in the panel).

    Finished, failed and stopped rows leave the state file. Two kinds stay: a running download (its
    row is how you watch it, and dropping it would orphan the job) and a whole file Jellyfin has not
    been asked about yet (the state file is the tick's scan queue). Nothing on the share is touched,
    and the worker's job records and logs on the n100 stay, so homelab-download status still lists
    what ran.
    """
    with _STATE_LOCK:
        state = _load_state()
        keep = [d for d in state['downloads'] if d.get('state') == 'downloading'
                or d.get('state') == 'preparing'   # Clear is not Cancel: a row at its service stays
                or (d.get('state') in _DEBRID_SCAN_UNTIL and not d.get('scanned'))]
        cleared = len(state['downloads']) - len(keep)
        state['downloads'] = keep
        _save_state(state)
    message = 'Cleared %d download%s.' % (cleared, '' if cleared == 1 else 's')
    message += (' Kept %d downloading, at its service or waiting for Jellyfin.' % len(keep)) if keep else ' The files stay on the share.'
    return {'ok': True, 'cleared': cleared, 'kept': len(keep), 'message': message}

def debrid_status():
    with _STATE_LOCK:
        state = _load_state()
    # 'preview', 'retry' and 'dismiss' go live with the routes that answer them, in the same commits; the panel
    # only offers a control when its name is here, because an older backend would run the new request as a
    # real download.
    features = ['preview', 'retry', 'dismiss']
    # 'search' (the Debrid tab and its Cancel button) needs a service to send a torrent to. With no key file,
    # Get keeps working exactly as it always did - Prowlarr to RdtClient - and the tab would be a dead control.
    if _rd_key() or _torbox_key():
        features.append('search')
    return {'ok': True, 'downloads': _debrid_rows(state), 'updated': state.get('updated'),
            'features': features}


_DEBRID_LOOP = {'started': False}


_DEBRID_TICK_LIVE = 3
_DEBRID_TICK_IDLE = 25


def _debrid_live() -> bool:
    """True while any row is downloading, so the loop refreshes progress quickly."""
    try:
        with _STATE_LOCK:
            return any(d.get('state') == 'downloading' for d in _load_state()['downloads'])
    except Exception:
        return False


def start_debrid_loop():
    """Background loop (dashboard host only): pull download state from the n100 worker, fire a Jellyfin scan on finish."""
    if not os.path.exists(_DOWNLOAD_KEY) or _DEBRID_LOOP['started']:
        return False
    _DEBRID_LOOP['started'] = True

    def loop():
        while True:
            # Every 3 s while something downloads (the panel shows live progress; one check is ~0.2 s of SSH),
            # every 25 s otherwise (only finishes and library scans to catch).
            time.sleep(_DEBRID_TICK_LIVE if _debrid_live() else _DEBRID_TICK_IDLE)
            try:
                _debrid_tick()
            except Exception:
                pass

    threading.Thread(target=loop, name='debrid-scan', daemon=True).start()
    return True


def handle(handler, post=False):
    start_debrid_loop()
    url = urllib.parse.urlparse(handler.path)
    m = POSTER.fullmatch(url.path)
    if m and not post:
        try:
            kind, body = poster(m.group(1))
            handler.send_response(200); handler.send_header('Content-Type', kind)
            handler.send_header('Cache-Control', 'private, max-age=86400'); handler.send_header('Content-Length', str(len(body)))
            handler.end_headers(); handler.wfile.write(body)
        except Exception:
            handler.send_response(404); handler.send_header('Content-Length', '0'); handler.end_headers()
        return True
    if url.path == '/api/downloads' and not post:
        try: _reply(handler, 200, {'items': recent()})
        except Exception: _reply(handler, 503, {'error': 'RdtClient is not answering.'})
        return True
    if url.path == '/api/downloads' and post:
        # Removing a failed grab is an action like the others: same origin plus the dashboard action header.
        if not qwen_proxy.same_origin(handler, 'X-Dashboard-Action'):
            _reply(handler, 403, {'ok': False, 'error': 'Use the homepage to run this action.'}); return True
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192: raise ValueError
            body = json.loads(handler.rfile.read(length))
            if not isinstance(body, dict): raise ValueError
        except (ValueError, OSError):
            _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
        item_id = body.get('dismiss')
        if isinstance(item_id, str):
            try: _reply(handler, 200, dismiss_grab(item_id))
            except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
    if url.path == '/api/downloads/search' and not post:
        q = urllib.parse.parse_qs(url.query)
        query, kind = (q.get('q') or [''])[0].strip(), (q.get('kind') or ['torrent'])[0]
        if not 2 <= len(query) <= 200 or kind not in ('torrent', 'usenet'):
            _reply(handler, 400, {'error': 'Type at least two characters.'}); return True
        try: _reply(handler, 200, {'results': search(query, kind)})
        except Exception: _reply(handler, 503, {'error': 'Prowlarr did not answer the search.'})
        return True
    if url.path == '/api/debrid' and not post:
        try: _reply(handler, 200, debrid_status())
        except Exception: _reply(handler, 503, {'error': 'Could not read debrid downloads.'})
        return True
    if url.path == '/api/debrid' and post:
        # Stopping a download is an action like the others: same origin plus the dashboard action header.
        if not qwen_proxy.same_origin(handler, 'X-Dashboard-Action'):
            _reply(handler, 403, {'ok': False, 'error': 'Use the homepage to run this action.'}); return True
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192: raise ValueError
            body = json.loads(handler.rfile.read(length))
            if not isinstance(body, dict): raise ValueError
        except (ValueError, OSError):
            _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
        if body.get('clear') is True:
            try: _reply(handler, 200, clear_debrid())
            except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        cancel = body.get('cancel')
        if isinstance(cancel, str):
            try: _reply(handler, 200, cancel_debrid(cancel))
            except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        retry = body.get('retry')
        if isinstance(retry, str):
            try: _reply(handler, 200, retry_debrid(retry))
            except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        name = body.get('stop')
        if not isinstance(name, str):
            _reply(handler, 400, {'ok': False, 'error': 'Name the download to stop.'}); return True
        try: _reply(handler, 200, stop_debrid(name))
        except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
        except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
        return True
    if url.path != '/api/action/download' or not post: return False
    if not qwen_proxy.same_origin(handler, 'X-Dashboard-Action'):
        _reply(handler, 403, {'ok': False, 'error': 'Use the homepage to run this action.'}); return True
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 8192: raise ValueError
        body = json.loads(handler.rfile.read(length))
        if not isinstance(body, dict): raise ValueError
    except (ValueError, OSError):
        _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
    magnet, token, url = body.get('magnet'), body.get('result'), body.get('url')
    kind = body.get('kind')
    if kind is not None and kind not in ('movie', 'show'):
        _reply(handler, 400, {'ok': False, 'error': "kind must be 'movie' or 'show'."}); return True
    if isinstance(token, str):
        with RESULTS_LOCK:
            protocol = (RESULTS.get(token) or {}).get('protocol')
        # Only a torrent result can go to a debrid service. A Usenet result (an NZB) keeps going through
        # Prowlarr to SABnzbd, keys or not; before 11 Nov evening every Usenet Get with a key file 503'd here.
        if protocol == 'torrent' and (_rd_key() or _torbox_key()):
            # A Debrid-tab Get with either key file: Real-Debrid first, TorBox as its fallback (no RdtClient).
            try: _reply(handler, 200, grab_debrid(token))
            except LookupError: _reply(handler, 410, {'ok': False, 'error': 'That result expired. Search again.'})
            except ValueError as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        # No key file at all: Get behaves exactly as before, through Prowlarr and RdtClient.
        try: title = grab(token); _reply(handler, 200, {'ok': True, 'title': title})
        except LookupError: _reply(handler, 410, {'ok': False, 'error': 'That result expired. Search again.'})
        except Exception: _reply(handler, 503, {'ok': False, 'error': 'Prowlarr could not send it to the downloader.'})
        return True
    if isinstance(url, str):
        url = url.strip()
        if not DEBRID_HTTP.fullmatch(url):
            _reply(handler, 400, {'ok': False, 'error': 'That is not a download URL.'}); return True
        if body.get('preview'):
            # Preview only: nothing reaches the worker, the state file is not touched.
            try: _reply(handler, 200, preview_debrid(url, kind))
            except ValueError as e: _reply(handler, 400, {'ok': False, 'error': str(e)[:200]})
            except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
            return True
        try: _reply(handler, 200, add_debrid(url, kind))
        except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
        return True
    if isinstance(magnet, str):
        magnet = magnet.strip()
        if not MAGNET.fullmatch(magnet):
            _reply(handler, 400, {'ok': False, 'error': 'That is not a magnet link.'}); return True
        try: add_magnet(magnet); _reply(handler, 200, {'ok': True})
        except Exception: _reply(handler, 503, {'ok': False, 'error': 'RdtClient did not accept the magnet.'})
        return True
    _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
