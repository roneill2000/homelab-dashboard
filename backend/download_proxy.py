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
import shlex
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

# Debrid direct-download path: a Real-Debrid/TorBox link is a plain HTTP URL, so we download it
# straight to Unraid with aria2c (full speed, 16 connections) via homelab-download on the n100,
# bypassing RdtClient. The backend classifies the file, files it, and fires a Jellyfin scan.
DEBRID_HTTP = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)
SE_DOT = re.compile(r'\bS(\d{1,2})\s*[Ee]\s*(\d{1,3})\b')
SE_X = re.compile(r'\b(\d{1,2})x(\d{1,3})\b')
YEAR = re.compile(r'\b(19\d{2}|20\d{2})\b')
MEDIA_EXT = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.webm')
SEARXNG = 'http://192.168.0.13:8888/search'
JF_SCAN = 'http://192.168.0.17:8096/ScheduledTasks/Running/7738148ffcd07979c7ceb148e06b3aed'
DEBRID_STATE = '/etc/homelab-dashboard/debrid.json'
MOVIES_DIR = '/mnt/unraid/Movies'
SHOWS_DIR = '/mnt/unraid/Shows'
_PROXMOX_KEY = '/etc/homelab-dashboard/update_ed25519'
_PROXMOX_KNOWN = '/etc/homelab-dashboard/update_known_hosts'
_PROXMOX_TARGET = 'root@192.168.0.3'


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


def _proxmox(cmd, timeout=30):
    """Run a command on the n100 with the same key the update flow uses."""
    return subprocess.run(
        ['ssh', '-i', _PROXMOX_KEY, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
         '-o', 'UserKnownHostsFile=' + _PROXMOX_KNOWN, '-o', 'StrictHostKeyChecking=yes',
         '-o', 'ConnectTimeout=6', _PROXMOX_TARGET, cmd],
        capture_output=True, text=True, timeout=timeout)


def _name_from_url(url):
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]).strip()


def _clean_title(title, drop_year=True):
    t = title.replace('.', ' ')
    if drop_year:
        t = YEAR.sub('', t)
    return re.sub(r'\s+', ' ', t).strip(' -')


def classify(name):
    """From a media filename: kind (movie/show), title, S/E, and the show directory name."""
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT:
        ext = '.mkv'
    m = SE_DOT.search(stem) or SE_X.search(stem)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        title = stem[:m.start()].strip(' .-_') or name
        kind, show_dir = 'show', _clean_title(title)
    else:
        season = episode = None
        y = YEAR.search(stem)
        title = stem[:y.start()].strip(' .-_') if y else stem
        kind, show_dir = 'movie', None
    return dict(kind=kind, title=title, season=season, episode=episode, ext=ext, show_dir=show_dir, name=name)


def searxng_ground(title):
    """Best-effort movie/show confirmation via SearXNG. Returns 'movie', 'show' or None."""
    try:
        q = urllib.parse.urlencode({'q': title, 'format': 'json'})
        with urllib.request.urlopen(SEARXNG + '?' + q, timeout=8) as r:
            data = json.load(r)
        text = ' '.join((res.get('title', '') + ' ' + res.get('content', '')) for res in data.get('results', [])[:5]).lower()
        if 'tv series' in text or 'season' in text or 'episode' in text:
            return 'show'
        if 'movie' in text or 'film' in text:
            return 'movie'
    except Exception:
        pass
    return None


def _load_state():
    try:
        with open(DEBRID_STATE) as f:
            return json.load(f)
    except Exception:
        return {'downloads': []}


def _save_state(s):
    try:
        with open(DEBRID_STATE, 'w') as f:
            json.dump(s, f)
    except Exception:
        pass


def _jellyfin_scan():
    import __main__
    hdr = getattr(__main__, '_JF_HDR', None) or {}
    req = urllib.request.Request(JF_SCAN, data=b'', method='POST', headers=dict(hdr))
    urllib.request.urlopen(req, timeout=10)


def _debrid_done(d):
    f = d['dest'] + '/' + d['name']
    r = _proxmox('test -e %s && ! test -e %s.aria2 && echo done' % (shlex.quote(f), shlex.quote(f + '.aria2')), timeout=25)
    return r.returncode == 0 and 'done' in r.stdout


def add_debrid(url):
    name = _name_from_url(url)
    info = classify(name)
    grounded = searxng_ground(info['title'])
    if grounded and grounded != info['kind']:
        info['kind'] = grounded
        info['show_dir'] = _clean_title(info['title']) if grounded == 'show' else None
    dest = MOVIES_DIR if info['kind'] == 'movie' else os.path.join(SHOWS_DIR, info['show_dir'] or _clean_title(info['title']))
    cmd = 'homelab-download debrid %s %s %s' % (shlex.quote(url), shlex.quote(dest), shlex.quote(name))
    r = _proxmox(cmd, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or 'download failed to start').strip())
    state = _load_state()
    state['downloads'] = [d for d in state['downloads'] if not (d['dest'] == dest and d['name'] == name)]
    state['downloads'].append({'dest': dest, 'name': name, 'kind': info['kind'], 'title': info['title'],
                               'season': info['season'], 'episode': info['episode'], 'started': int(time.time()), 'scanned': False})
    _save_state(state)
    return dict(ok=True, kind=info['kind'], title=info['title'], season=info['season'], episode=info['episode'],
                dest=dest, name=name, grounded=grounded, started=r.stdout.strip())


def _debrid_tick():
    """Fire a Jellyfin scan when a tracked debrid download finishes. Called from a background loop."""
    state = _load_state()
    changed = False
    for d in state['downloads']:
        if d.get('scanned'):
            continue
        if _debrid_done(d):
            try:
                _jellyfin_scan()
            except Exception:
                pass
            d['scanned'] = True
            changed = True
    if changed:
        _save_state(state)


def _debrid_rows(state):
    out = []
    for d in state['downloads'][-15:][::-1]:
        try:
            done = _debrid_done(d)
        except Exception:
            done = False
        out.append(dict(name=d['name'], kind=d['kind'], title=d['title'], season=d.get('season'),
                        episode=d.get('episode'), dest=d['dest'], done=done, scanned=d.get('scanned', False), started=d.get('started')))
    return out


def debrid_status():
    return {'ok': True, 'downloads': _debrid_rows(_load_state())}


_DEBRID_LOOP = {'started': False}


def start_debrid_loop():
    """Background loop that fires a Jellyfin scan when a debrid download finishes (dashboard host only)."""
    if not os.path.exists(_PROXMOX_KEY) or _DEBRID_LOOP['started']:
        return False
    _DEBRID_LOOP['started'] = True

    def loop():
        while True:
            try:
                _debrid_tick()
            except Exception:
                pass
            time.sleep(30)

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
