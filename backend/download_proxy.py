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
_DEBRID_STATE_WORDS = ('downloading', 'finished', 'failed', 'stopped', 'interrupted', 'present', 'missing', 'unknown')
_RETRY_STATES = ('failed', 'stopped', 'interrupted')   # a stored link can be run again in these
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
    return {'downloads': [_migrate_row(d) for d in rows if isinstance(d, dict) and d.get('dest') and d.get('name')]}


def _save_state(state):
    """Write the rows in one step: tmp file, fsync, rename. Only ever called with _STATE_LOCK held.

    The mode is set on the tmp file before the rename, so the real file is never briefly world
    readable: it carries the pasted links with their access tokens.
    """
    state['downloads'] = state['downloads'][-_DEBRID_KEEP:]
    state['updated'] = int(time.time())
    os.makedirs(os.path.dirname(DEBRID_STATE), exist_ok=True)
    tmp = DEBRID_STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
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


def plan_debrid(url, kind=None):
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
                        sampled=d.get('sampled'),
                        # The link stays inside the backend: the panel only learns whether a retry is possible.
                        can_retry=bool(d.get('url')) and st in _RETRY_STATES,
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
    the partial file (its .aria2 control file is what makes --continue safe). Raises ValueError
    (no such row, no stored link, or a state that cannot be retried) or RuntimeError (worker).

    The state lock is held around the rows only, never around the worker call (same pattern as
    stop_debrid); the row is looked up again after the call because the tick loop can rewrite it.
    """
    name = _safe_name(name)
    with _STATE_LOCK:
        row = next((d for d in _load_state()['downloads'] if d['name'] == name), None)
        if row is None:
            raise ValueError('that download is not in the list anymore')
        if not row.get('url'):
            raise ValueError('no link is stored for that download; paste a fresh one')
        if row.get('state') not in _RETRY_STATES:
            raise ValueError('that download is %s; only failed, stopped or interrupted can be retried' % row.get('state'))
        url, dest = row['url'], row['dest']
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
                or (d.get('state') in _DEBRID_SCAN_UNTIL and not d.get('scanned'))]
        cleared = len(state['downloads']) - len(keep)
        state['downloads'] = keep
        _save_state(state)
    message = 'Cleared %d download%s.' % (cleared, '' if cleared == 1 else 's')
    message += (' Kept %d still downloading or waiting for Jellyfin.' % len(keep)) if keep else ' The files stay on the share.'
    return {'ok': True, 'cleared': cleared, 'kept': len(keep), 'message': message}

def debrid_status():
    with _STATE_LOCK:
        state = _load_state()
    # 'preview' and 'retry' go live with the routes that answer them, in the same commits; the panel only
    # offers a control when its name is here, because an older backend would run the new request as a
    # real download.
    return {'ok': True, 'downloads': _debrid_rows(state), 'updated': state.get('updated'),
            'features': ['preview', 'retry']}


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
    if isinstance(token, str):
        try: title = grab(token); _reply(handler, 200, {'ok': True, 'title': title})
        except LookupError: _reply(handler, 410, {'ok': False, 'error': 'That result expired. Search again.'})
        except Exception: _reply(handler, 503, {'ok': False, 'error': 'Prowlarr could not send it to the downloader.'})
        return True
    _reply(handler, 400, {'ok': False, 'error': 'Invalid request.'}); return True
