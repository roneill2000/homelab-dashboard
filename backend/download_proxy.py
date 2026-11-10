"""Manual downloads from the dashboard: search Prowlarr's indexers, grab a result, or hand RdtClient a magnet.

Grabs go through Prowlarr, which sends torrents to RdtClient and NZBs to SABnzbd under the 'prowlarr' category,
the same path its own Search page uses. Magnets go straight to RdtClient in that category. The browser never sees
indexer URLs or API keys: search results are handed out as short-lived tokens.
"""
import http.cookiejar
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
# Grounding: SearXNG answers "film or series?" only when the file name is ambiguous.
# Library update: POST .17:8096/ScheduledTasks/Running/<Scan Media Library id>, using the
# dashboard process's own Jellyfin header (__main__._JF_HDR), the same one posters use.
# State: /var/lib/homelab-dashboard/debrid.json (older deploys kept it in /etc/homelab-dashboard).
DEBRID_HTTP = re.compile(r'https://[^\s"\'<>|\\]+')
SE_DOT = re.compile(r'\bS(\d{1,2})\s*[Ee]\s*(\d{1,3})\b')
SE_X = re.compile(r'\b(\d{1,2})x(\d{1,3})\b')
SEASON_ONLY = re.compile(r'\b(?:Season|Staffel)\s*[._-]\s*(\d{1,2})\b', re.I)
EP_ONLY = re.compile(r'\bE(?:pisode)?\s*[._-]\s*(\d{1,3})\b', re.I)
YEAR = re.compile(r'\b(19\d{2}|20\d{2})\b')
# The worker on .3 checks the same list (scripts/homelab_download_lib.py); tests/test_debrid_parity.py
# fails if one side adds or drops an extension without the other.
MEDIA_EXT = ('.mkv', '.mp4', '.m4v', '.mov', '.avi', '.wmv', '.flv', '.webm', '.m4a', '.mp3', '.flac', '.epub')
SEARXNG = 'http://192.168.0.13:8888/search'
JF_SCAN = 'http://192.168.0.17:8096/ScheduledTasks/Running/7738148ffcd07979c7ceb148e06b3aed'  # "Scan Media Library"
DEBRID_STATE = '/var/lib/homelab-dashboard/debrid.json'
DEBRID_STATE_OLD = '/etc/homelab-dashboard/debrid.json'
MOVIES_DIR = '/mnt/unraid/Movies'
SHOWS_DIR = '/mnt/unraid/Shows'
_DOWNLOAD_KEY = '/etc/homelab-dashboard/download_ed25519'
_PROXMOX_KNOWN = '/etc/homelab-dashboard/update_known_hosts'
_PROXMOX_TARGET = 'root@192.168.0.3'
_DEBRID_IF_VERSION = 1
_DEBRID_KEEP = 50      # rows kept in the state file
_DEBRID_ROWS = 15      # rows the homepage shows
_DEBRID_CHECK_MAX = 40 # downloads in one check call
_DEBRID_SCAN_UNTIL = ('finished', 'present')   # states that mean "ask Jellyfin to look"
_DEBRID_STATE_WORDS = ('downloading', 'finished', 'failed', 'stopped', 'interrupted', 'present', 'missing', 'unknown')
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
    out = [{k: t.get(k) for k in keep} | {'hash': (t.get('hash') or '').lower()} for t in items[:12]]
    active = [t for t in out if (t['progress'] or 0) < 1]
    if active:
        native = {(n.get('hash') or '').lower(): n for n in json.loads(rdt('/Api/Torrents'))}
        for t in active:
            n = native.get(t['hash'])
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
    for t in out: del t['hash']
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
            RESULTS[token] = {'at': now, 'guid': r['guid'], 'indexerId': r['indexerId'], 'title': r.get('title')}
            info = r.get('infoUrl') if str(r.get('infoUrl', '')).startswith(('https://', 'http://')) else None
            out.append({'id': token, 'title': r.get('title'), 'size': r.get('size'), 'seeders': r.get('seeders'),
                        'leechers': r.get('leechers'), 'grabs': r.get('grabs'), 'indexer': r.get('indexer'),
                        'published': r.get('publishDate'), 'info': info})
    key = (lambda x: x['seeders'] or 0) if kind == 'torrent' else (lambda x: x['grabs'] or 0)
    out.sort(key=key, reverse=True)
    return out[:60]


def add_magnet(magnet):
    rdt('/api/v2/torrents/add', {'urls': magnet, 'category': CATEGORY})


def grab(token):
    with RESULTS_LOCK:
        hit = RESULTS.get(token)
    if not hit: raise LookupError
    prowlarr('/api/v1/search', {'guid': hit['guid'], 'indexerId': hit['indexerId']})
    return hit['title']


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


def _clean_title(text):
    """A human title out of a file name: dots become spaces, cut at the release year, drop tech tags.
    Keeps a title that is only a year (1917) and one that starts with a year (2001: A Space Odyssey)."""
    t = re.sub(r'[._]+', ' ', text or '').strip()
    for y in reversed(list(YEAR.finditer(t))):
        head = t[:y.start()].strip()
        if head:
            t = head
            break
    words = t.split()
    while words and words[-1].lower().strip('()-') in _TECH_TAIL:
        words.pop()
    return ' '.join(words).strip(' -()')


def classify(name):
    """From a media file name: film or series, the title, and any season/episode the name states.

    Season/episode evidence in the name is never thrown away. ambiguous=True means the name states
    nothing on its own, so add_debrid asks SearXNG. A word like "season" inside a title is not evidence.
    """
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT:
        ext = '.mkv'
    m = SE_DOT.search(stem) or SE_X.search(stem) or EP_ONLY.search(stem)
    if m:
        groups = [g for g in m.groups() if g]
        season, episode = (int(groups[0]), int(groups[1])) if len(groups) > 1 else (1, int(groups[0]))
        title = _clean_title(stem[:m.start()]) or name
        return dict(kind='show', title=title, season=season, episode=episode, ext=ext,
                    show_dir=_safe_show_dir(title), ambiguous=False)
    ms = SEASON_ONLY.search(stem)
    if ms:
        title = _clean_title(stem[:ms.start()]) or name
        return dict(kind='show', title=title, season=int(ms.group(1)), episode=None, ext=ext,
                    show_dir=_safe_show_dir(title), ambiguous=False)
    title = _clean_title(stem) or stem
    return dict(kind='movie', title=title, season=None, episode=None, ext=ext, show_dir=None, ambiguous=True)


_SHOW_HINT = re.compile(r'\b(?:tv|television|web|streaming|mini[- ]?series|anthology)[ -]*series\b|\bminiseries\b|\bepisodic\b', re.I)
_MOVIE_HINT = re.compile(r'\b(?:film|movie)\b', re.I)


def searxng_results(title):
    """Top SearXNG snippets for a title: text to match against, never instructions to follow."""
    q = urllib.parse.urlencode({'q': title, 'format': 'json'})
    with urllib.request.urlopen(SEARXNG + '?' + q, timeout=8) as r:
        data = json.load(r)
    return [((res.get('title') or '') + ' ' + (res.get('content') or '')) for res in (data.get('results') or [])[:6]]


def searxng_ground(title):
    """'show', 'movie' or None, from what the top results call the title.

    Only strong phrases count. A bare word like "season" never makes a show out of a film: the 2011
    horror film Season of the Witch stays a film. Results are counted, and a tie answers nothing.
    """
    if not title:
        return None
    try:
        texts = searxng_results(title)
    except Exception:
        return None
    shows = sum(1 for t in texts if _SHOW_HINT.search(t))
    movies = sum(1 for t in texts if _MOVIE_HINT.search(t))
    if shows >= 2 and shows >= movies:
        return 'show'
    if movies >= 2 and movies > shows:
        return 'movie'
    return None


def _migrate_row(d):
    # Rows from an older deploy miss fields the panel now uses; default them here so
    # every row reaching the frontend has the same shape.
    d.setdefault('kind', 'movie')
    d.setdefault('title', d['name'])
    d.setdefault('scanned', False)
    d.setdefault('scan_error', None)
    d.setdefault('error', None)
    d.setdefault('grounded', None)
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
    rows = state.get('downloads') if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return {'downloads': []}
    return {'downloads': [_migrate_row(d) for d in rows if isinstance(d, dict) and d.get('dest') and d.get('name')]}


def _save_state(state):
    """Write the rows in one step: tmp file, fsync, rename. Only ever called with _STATE_LOCK held."""
    state['downloads'] = state['downloads'][-_DEBRID_KEEP:]
    state['updated'] = int(time.time())
    os.makedirs(os.path.dirname(DEBRID_STATE), exist_ok=True)
    tmp = DEBRID_STATE + '.tmp'
    with open(tmp, 'w') as f:
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


def add_debrid(url):
    """Start a debrid download on the n100 and remember it. Raises ValueError (bad input) or RuntimeError (worker)."""
    if not isinstance(url, str) or not DEBRID_HTTP.fullmatch(url.strip()):
        raise ValueError('paste a direct https download link')
    url = _safe_url(url)
    name = _safe_name(_name_from_url(url))  # _safe_name already requires a media extension
    info = classify(name)
    grounded = None
    if info['ambiguous']:
        grounded = searxng_ground(info['title'])
        if grounded == 'show':
            info['kind'], info['show_dir'] = 'show', _safe_show_dir(info['title'])
    show_dir = info['show_dir'] or _safe_show_dir(info['title'])
    dest = _safe_dest(MOVIES_DIR if info['kind'] == 'movie' else os.path.join(SHOWS_DIR, show_dir))
    out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'start', 'url': url, 'dest': dest, 'name': name})
    row = {'dest': dest, 'name': name, 'unit': out.get('unit') or 'dl-' + name, 'kind': info['kind'],
           'title': info['title'], 'season': info['season'], 'episode': info['episode'],
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
                grounded=grounded, dest=dest, name=name, state=row['state'], unit=row['unit'],
                message=out.get('message') or ('started ' + name))


def _debrid_tick():
    """Refresh rows from the n100 worker, then ask Jellyfin once for everything that just finished.

    A row is only marked scanned after Jellyfin accepted the scan; otherwise scan_error is stored and
    the next tick tries again. Runs in the background loop, never in a request.
    """
    with _STATE_LOCK:
        state = _load_state()
        due = [d for d in state['downloads'] if d['state'] not in _DEBRID_SCAN_UNTIL or not d.get('scanned')]
        jobs = [{'dest': d['dest'], 'name': d['name']} for d in due[-_DEBRID_CHECK_MAX:]]
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
                changed = True
            if j.get('error') and d.get('error') != str(j['error'])[:200]:
                d['error'] = str(j['error'])[:200]
                changed = True
        ready = [d for d in state['downloads'] if d.get('state') in _DEBRID_SCAN_UNTIL and not d.get('scanned')]
        if ready:
            ok = _jellyfin_scan()
            for d in ready:
                if ok:
                    d['scanned'], d['scan_error'] = True, None
                else:
                    d['scan_error'] = 'the library scan did not start'
            changed = True
        if changed:
            _save_state(state)


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
                        age=max(0, int(time.time()) - int(d.get('started') or 0))))
    return out


def stop_debrid(name):
    """Stop one running download on the n100 by file name, so a stuck row can be cleared.

    The partial file stays on the share: pasting the same link again resumes it. Raises
    ValueError (not in the list, bad name) or RuntimeError (the worker refused).
    """
    name = _safe_name(name)
    with _STATE_LOCK:
        state = _load_state()
        row = next((d for d in state['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that download is not in the list anymore')
        out = _download_rpc({'v': _DEBRID_IF_VERSION, 'cmd': 'stop', 'name': name}, timeout=40)
        row['state'], row['state_since'] = 'stopped', int(time.time())
        row['scanned'], row['scan_error'] = True, None   # stopped on purpose: never scanned, no retry
        _save_state(state)
    return dict(ok=True, name=name, unit=out.get('unit') or 'dl-' + name,
                message=out.get('message') or ('stopped ' + name))


def debrid_status():
    with _STATE_LOCK:
        state = _load_state()
    return {'ok': True, 'downloads': _debrid_rows(state), 'updated': state.get('updated')}


_DEBRID_LOOP = {'started': False}


def start_debrid_loop():
    """Background loop (dashboard host only): pull download state from the n100 worker, fire a Jellyfin scan on finish."""
    if not os.path.exists(_DOWNLOAD_KEY) or _DEBRID_LOOP['started']:
        return False
    _DEBRID_LOOP['started'] = True

    def loop():
        while True:
            time.sleep(25)
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
    if isinstance(url, str):
        url = url.strip()
        if not DEBRID_HTTP.fullmatch(url):
            _reply(handler, 400, {'ok': False, 'error': 'That is not a download URL.'}); return True
        try: _reply(handler, 200, add_debrid(url))
        except Exception as e: _reply(handler, 503, {'ok': False, 'error': str(e)[:200]})
        return True
    if isinstance(magnet, str):
        magnet = magnet.strip()
        if not MAGNET.fullmatch(magnet):
            _reply(handler, 400, {'ok': False, 'error': 'That is not a magnet link.'}); return True
        try: add_magnet(magnet); _reply(handler, 200, {'ok': True})
        except Exception: _reply(handler, 503, {'ok': False, 'error': 'RdtClient did not accept the magnet.'})
        return True
    if isinstance(token, str):
        try: title = grab(token); _reply(handler, 200, {'ok': True, 'title': title})
        except LookupError: _reply(handler, 410, {'ok': False, 'error': 'That result expired. Search again.'})
        except Exception: _reply(handler, 503, {'ok': False, 'error': 'Prowlarr could not send it to the downloader.'})
        return True
    _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
