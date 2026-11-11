#!/usr/bin/env python3
"""Download worker on the n100 (.3): validation, job records, progress.

Everything that decides what may be written to the Unraid share lives here, on
the machine that writes it. Three things use it:

  homelab-download              shell front end people and agents type
  homelab-download-rpc          the dashboard's restricted JSON interface
  ... run ...                   the aria2c worker inside each systemd unit

The dashboard host never runs a shell on the n100: it sends one JSON object per
call to homelab-download-rpc, whose reply is one JSON object. Installed on .3
as /usr/local/sbin/homelab_download_lib.py; the copy in this repo
(scripts/homelab_download_lib.py) is the source of truth.
Repo tests: tests/test_download_lib.py, tests/test_debrid_parity.py.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SHARE_ROOT = '/mnt/unraid'
MOVIES_DIR = '/mnt/unraid/Movies'
SHOWS_DIR = '/mnt/unraid/Shows'
LOG_DIR = '/var/log/homelab-download'
JOB_DIR = '/var/lib/homelab-download/jobs'
UNIT_PREFIX = 'dl-'
VERSION = 1

# Job states. 'finished' means aria2c exited 0 and the file is on disk with no
# .aria2 control file left behind. Anything weaker is reported as it is.
STATES = ('downloading', 'finished', 'failed', 'stopped', 'interrupted', 'present', 'missing', 'unknown')
TERMINAL_SCANNABLE = ('finished', 'present')

# --file-allocation=none: aria2c otherwise reserves the whole target size up front, so a 34 GB
# download reads as 34 GB on disk seconds after it starts, and the size says nothing about how far
# it got. It also avoids writing that phantom size across the CIFS mount.
# --allow-overwrite=false: a finished file is never replaced. Resuming a partial file still works
# because --continue=true keys off the .aria2 control file, and start_debrid() already answers
# "already finished" for a whole file with no control file, so aria2c rarely sees the case at all.
# --max-redirect=0: run() follows redirects itself (each hop through safe_url); aria2c must not
# follow a chain this worker has not checked.
ARIA2_FLAGS = ('-x16', '-s16', '-k1M', '--continue=true', '--auto-file-renaming=false',
               '--allow-overwrite=false', '--file-allocation=none', '--summary-interval=3',
               '--max-redirect=0')
# Both sides check this list: the dashboard rejects a non-media link early, the worker has the final
# word on .3. tests/test_debrid_parity.py fails if one side changes it alone.
MEDIA_EXT = {'.mkv', '.mp4', '.m4v', '.mov', '.avi', '.wmv', '.flv', '.webm', '.m4a', '.mp3', '.flac', '.epub'}

# A name is one file inside an approved folder: no separators, no traversal, no
# hidden or option-looking name, a known media extension.
NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SHOW_DIR_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class _Tee:
    """Write to two streams, so one log line reaches both the unit's file and the journal."""

    def __init__(self, streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return False


class BadDownload(ValueError):
    """A download request this worker will not run."""


def _norm(path: str) -> str:
    return os.path.normpath(path.rstrip('/'))


def roots() -> list[str]:
    return [MOVIES_DIR, SHOWS_DIR]


def mount_ok() -> bool:
    return subprocess.run(['mountpoint', '-q', os.path.dirname(MOVIES_DIR) or '/']).returncode == 0


def name_from_url(url: str) -> str:
    """The file name in the URL. Decoded first, so %2F cannot become a path."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    return path.rsplit('/', 1)[-1].strip()


def safe_name(name) -> str:
    """One file name inside an approved folder: no separators, no traversal, no hidden or
    option-looking name. Used by every path, including the rate-limited wget downloads of any
    file type (a book, a subtitle, a test file)."""
    if not isinstance(name, str):
        raise BadDownload('no file name')
    name = name.strip()
    if not name or name in ('.', '..'):
        raise BadDownload('the file name is empty')
    if NAME_BAD.search(name) or name.startswith(('.', '-')):
        raise BadDownload('the file name has characters this download cannot use')
    if len(name) > 200:
        raise BadDownload('the file name is too long')
    if not os.path.splitext(name)[1]:
        raise BadDownload('the file name has no extension')
    return name


def media_name(name) -> str:
    """A file name the media library will accept: safe_name plus a known media extension."""
    name = safe_name(name)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in MEDIA_EXT or not stem.strip('.'):
        raise BadDownload('this link is not a media file (.mkv, .mp4, .epub, ...)')
    return name


def safe_show_dir(title) -> str:
    """A show folder under Shows, from a cleaned title."""
    if not isinstance(title, str):
        raise BadDownload('no show name')
    folder = re.sub(r'\s+', ' ', title).strip(' .-')
    if not folder or SHOW_DIR_BAD.search(folder) or len(folder) > 80:
        raise BadDownload('the show name cannot be used as a folder')
    return folder


def share_dest(dest) -> str:
    """A folder on the Unraid share, for the operator's own rate-limited downloads: any real
    folder under /mnt/unraid (Movies, Shows, Books, ...).

    Traversal tokens are refused outright instead of being normalized away, so a destination
    must already be literal. The realpath check then stops a symlink from leaving the share.
    """
    if not isinstance(dest, str) or '\x00' in dest or chr(92) in dest:  # no backslash on this share
        raise BadDownload('no destination folder')
    dest = dest.strip()
    if not dest.startswith('/'):
        raise BadDownload('the destination must be a full path')
    if any(part in ('.', '..') for part in dest.split('/')):
        raise BadDownload('the destination cannot contain . or ..')
    norm = _norm(dest)
    if len(norm) > 140:
        raise BadDownload('the destination path is too long')
    if norm != SHARE_ROOT and not norm.startswith(SHARE_ROOT + '/'):
        raise BadDownload('the destination must be a folder on the share (/mnt/unraid/...)')
    if os.path.realpath(norm) != norm:
        raise BadDownload('the destination is not a real folder on the share')
    return norm


def safe_dest(dest) -> str:
    """A folder the media library may write: Movies, or a folder under Shows. Stricter than
    share_dest, and the only thing the dashboard and the RPC may ask for."""
    norm = share_dest(dest)
    if norm == SHOWS_DIR or not (norm == MOVIES_DIR or norm.startswith(SHOWS_DIR + '/')):
        raise BadDownload('the download folder must be Movies or a folder under Shows')
    return norm


_LOCAL_NAMES = ('localhost', 'pi.hole', 'proxmox', 'unraid', 'agents')
# These hosts are never a public CDN. Checked before any lookup, so a LAN name costs no DNS time.
_LOCAL_HOST_STARTS = ('10.', '127.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.',
                      '172.22.', '172.23.', '192.168.', '100.64.', '169.254.', '0.')
_DNS_TTL = 300.0
_DNS_CACHE: dict[str, tuple[float, bool]] = {}


def _lookup(host: str):
    """One DNS lookup with a two second timeout, so a dead resolver cannot stall the worker."""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2.0)
    try:
        return socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    finally:
        socket.setdefaulttimeout(old)


def lan_host(host: str) -> bool:
    """A host that is certainly this LAN, decided with no lookup at all."""
    host = host.lower()
    if host in _LOCAL_NAMES or any(host.startswith(start) for start in _LOCAL_HOST_STARTS):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def private_host(host: str) -> bool:
    """True if the host points at this network or a non-routable address.

    lan_host answers for LAN names and LAN numbers first, so they cost no lookup.
    The DNS answer is cached five minutes: a Pi-hole block list can change, but not
    within one check of one download.
    """
    if lan_host(host):
        return True
    cached = _DNS_CACHE.get(host)
    if cached and time.time() - cached[0] < _DNS_TTL:
        return cached[1]
    try:
        infos = _lookup(host)
    except OSError:
        raise BadDownload('the download host does not resolve')
    result = False
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            result = True
            break
    _DNS_CACHE[host] = (time.time(), result)
    return result


def safe_url(url, resolve=True) -> str:
    if not isinstance(url, str) or len(url) > 4000:
        raise BadDownload('no download link')
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != 'https' or not parts.netloc or not parts.path.strip('/'):
        raise BadDownload('the link must be a direct https download')
    host = parts.hostname or ''
    if lan_host(host):
        raise BadDownload('this link points at the LAN, not at a public download')
    if resolve and private_host(host):
        raise BadDownload('the download host is not on the public internet')
    return url.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed one hop at a time, so every hop passes safe_url."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_opener():
    opener = urllib.request.build_opener(_NoRedirect)
    opener.addheaders = [('User-Agent', 'homelab-download/1.0')]
    return opener


_MAX_REDIRECT_HOPS = 4


def resolve_redirects(url: str) -> str:
    """The real download address, with at most 4 redirects, each hop checked by safe_url.

    safe_url only ever tests the first host. A debrid link usually 302s to a different CDN host,
    and aria2c used to follow any chain it was handed - possibly to a LAN address. run() hands
    aria2c --max-redirect=0, so this is the only thing that follows redirects. The probe is a GET
    for the first byte only (some CDNs answer HEAD with nothing useful); the byte is thrown away,
    aria2c downloads the final URL itself. A non-redirect error answer keeps the URL (the GET
    may still work); a hop that leaves the public internet fails the job in one sentence.
    """
    target = url
    for _ in range(_MAX_REDIRECT_HOPS + 1):
        req = urllib.request.Request(target, headers={'Range': 'bytes=0-0'})
        try:
            with _probe_opener().open(req, timeout=15):
                return target                      # an answer, not a redirect: this is the final URL
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                return target                      # 403 on the probe is not a redirect
            hop = urllib.parse.urljoin(target, exc.headers.get('Location') or '')
            try:
                target = safe_url(hop)
            except BadDownload:
                raise BadDownload('the link redirects to a host that is not on the public internet')
        except (urllib.error.URLError, TimeoutError, OSError):
            return target                      # probe cannot reach it; aria2c will state its own reason
    raise BadDownload('the link redirects more than four times')


def unit_name(name: str) -> str:
    """The systemd unit for a file name. Same rules the shell script always used: only
    characters systemd accepts, cut to 60 characters of the file name."""
    safe = re.sub(r'[^A-Za-z0-9._-]', '-', name)[:60]
    return UNIT_PREFIX + safe


def safe_unit(unit) -> str:
    """A unit name this worker could have made. Keeps job and log paths inside their folders."""
    if not isinstance(unit, str) or not unit.startswith(UNIT_PREFIX):
        raise BadDownload('this is not a download unit')
    safe = unit[len(UNIT_PREFIX):]
    if not safe or len(safe) > 80 or safe.startswith(('.', '-')) or not re.fullmatch(r'[A-Za-z0-9._-]+', safe):
        raise BadDownload('this is not a download unit')
    return unit


def unit_from_log(basename: str) -> str:
    return basename[:-4] if basename.endswith('.log') else basename


def job_path(unit: str) -> str:
    unit = safe_unit(unit)
    return os.path.join(JOB_DIR, unit + '.json')


def read_job(unit: str) -> dict | None:
    # job_path first: a unit name this worker could not have made is refused, not read.
    path = job_path(unit)
    try:
        with open(path, encoding='utf-8') as f:
            job = json.load(f)
        return job if isinstance(job, dict) else None
    except (OSError, ValueError):
        return None


def record_job(unit: str, state: str, dest: str, name: str, error: str = '') -> dict:
    """Record how a job ended, for jobs started outside this library (the wget path). That path
    may write any file type to any folder on the share, so it gets the share rules, not the
    media library rules."""
    dest, name = share_dest(dest), safe_name(name)
    # 'stopped' and 'running' are honest records too: the wget path writes one at the start.
    state = state if state in ('complete', 'failed', 'stopped', 'running') else 'failed'
    fields = {'state': state, 'dest': dest, 'name': name, 'finished': time.time()}
    if error:
        fields['error'] = str(error)[:200]
    return write_job(unit, **fields)


def write_job(unit: str, **fields) -> dict:
    job = read_job(unit) or {}
    job.update({'unit': unit})
    job.update(fields)
    os.makedirs(JOB_DIR, exist_ok=True)
    tmp = job_path(unit) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(job, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, job_path(unit))
    return job


def file_state(dest: str, name: str) -> tuple[bool, int, bool]:
    """(file present, bytes, aria2 control file present). The control file is
    one suffix on the file name: aria2c removes it when the file is complete."""
    path = os.path.join(dest, name)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return size > 0, size, os.path.exists(path + '.aria2')


def unit_active(unit: str) -> str:
    try:
        out = subprocess.run(['systemctl', 'is-active', unit],
                             capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return out or 'unknown'


def systemctl_units() -> list[str]:
    """dl-* units systemd knows about, running or not. A download can be listed without a log."""
    try:
        out = subprocess.run(['systemctl', 'list-units', '--all', '--no-legend', '--plain',
                              UNIT_PREFIX + '*.service'], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        first = line.split(' ', 1)[0].strip()
        if first.endswith('.service'):
            names.append(first[:-len('.service')])
    return names


def journal_lines(unit: str) -> list[str]:
    """systemd's own copy of one unit's output, for units that have no log file of their own.

    Downloads started before run() began writing a log only exist in the journal, and their
    aria2c summary lines are still there, so an in-flight row keeps showing real progress.
    """
    try:
        unit = safe_unit(unit)
    except BadDownload:
        return []
    try:
        out = subprocess.run(['journalctl', '-u', unit, '--no-pager', '-o', 'cat', '--lines=200'],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def decide_state(job: dict | None, active: str, has_file: bool, ctl: bool) -> str:
    """The state machine, kept as a pure function so the rules can be tested.

    aria2c keeps <name>.aria2 while a file is incomplete and removes it when
    the file is whole, so a file alone is never called finished: the worker's
    own exit record is what proves aria2c exited cleanly.
    """
    running = active in ('active', 'activating', 'deactivating')
    if running:
        return 'downloading'
    state = (job or {}).get('state')
    if state == 'complete':
        return 'finished' if has_file and not ctl else 'missing'
    if state == 'failed':
        return 'failed' if not has_file or ctl else 'interrupted'
    if state == 'stopped':
        return 'stopped'
    if state == 'running':
        # The unit is gone but the worker never wrote its exit: killed, rebooted,
        # or OOM'd. Never claim a download finished here.
        return 'interrupted'
    # No record at all (older units, or a file placed by hand).
    if ctl:
        return 'interrupted'
    if has_file:
        return 'present'
    return 'unknown'


def job_state(unit: str, dest: str | None = None, name: str | None = None) -> dict:
    job = read_job(unit)
    dest = dest or (job or {}).get('dest')
    name = name or (job or {}).get('name')
    active = unit_active(unit)
    out = {'unit': unit, 'dest': dest, 'name': name, 'active': active in ('active', 'activating', 'deactivating')}
    if active in ('active', 'activating', 'deactivating'):
        # A running unit is downloading, record or not: the old shell script left no record either.
        out['state'] = 'downloading'
        prog = progress_for(unit)
        out.update(prog)
        if dest and name:
            has_file, size, ctl = file_state(dest, name)
            if ctl:
                # The aria2 control file says data is still missing, and the size on disk then
                # counts reserved space, not progress: only this unit's summaries say how far it got.
                out['bytes'] = int(prog.get('done_bytes') or 0)
                out['ctl'] = True
            else:
                out['bytes'] = size
                partial = dest + '/' + name + '.part'
                if not size and os.path.exists(partial):
                    out['bytes'] = os.path.getsize(partial)
        return out
    if not dest or not name:
        out['state'] = 'unknown'
        out['error'] = (job or {}).get('error') or 'this download has no record'
        # Units started by the old shell script have no record at all: their log still says how far
        # they got, so the labels people and agents parse keep working.
        out.update(progress_for(unit))
        return out
    has_file, size, ctl = file_state(dest, name)
    out['state'] = decide_state(job, active, has_file, ctl)
    out['ctl'] = ctl
    prog = {}
    if out['state'] in ('downloading', 'stopped', 'failed', 'interrupted'):
        # How far it got, read from this unit's own log (aria2c summaries, else wget progress rows).
        prog = progress_for(unit)
        out.update(prog)
    if ctl:
        # Not progress: see the running branch, same rule for a stopped or interrupted aria2c job.
        out['bytes'] = int(prog.get('done_bytes') or 0)
    else:
        out['bytes'] = size
        partial = dest + '/' + name + '.part'
        if not size and os.path.exists(partial):
            # A rate-limited download lives in <name>.part until it is whole: its size is how far it got,
            # true whether the job is running, stopped on purpose, or failed.
            out['bytes'] = os.path.getsize(partial)
    if (job or {}).get('error'):
        out['error'] = job['error']
    elif out['state'] in ('failed', 'interrupted') and (job or {}).get('rc') is not None:
        out['error'] = 'aria2c exited with code %s' % job['rc']
    return out


# ---------------------------------------------------------------- progress ---

# aria2c rewrites one summary line; the last non-empty one is the current state.
# Connection errors are separate [ERROR] lines and must never look like progress.
ARIA2_SUMMARY = re.compile(
    r'\[#(?P<id>[0-9a-f]+)\s+(?P<done>[\d.]+)(?P<du>[KMGT])iB/(?P<total>[\d.]+)(?P<tu>[KMGT])iB'
    r'\((?P<pct>\d{1,3})%\)(?:\s+CN:\d+)?(?:\s+DL:(?P<speed>[\d.]+)(?P<su>[KMGT])iB)?'
    r'(?:\s+ETA:(?P<eta>[^\]]+))?\]')
ARIA2_UNITS = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}
ARIA2_ERROR = re.compile(r'^\s*\[ERROR\]')
ARIA2_DONE = re.compile(r'^DONE ')
ARIA2_FAILED = re.compile(r'^FAILED ')
# wget -c --progress=dot:mega writes: a Length: line, offset rows like
# "     0K ........ ..    100% 4.39M=2.3s" (speed and estimated time joined by '='),
# and a final line like "2026-11-02 20:44:09 (4.39 MB/s) - 'f.part' saved [n/m]".
WGET_LENGTH = re.compile(r'Length:\s+(\d+)\s+\(')
WGET_ROW = re.compile(r'^\s*(?P<off>\d+(?:\.\d+)?)(?P<ou>[KMG])?\s+[^%]*?'
                      r'(?P<pct>\d{1,3})%\s+(?P<speed>[\d.]+)(?P<su>[KMG])?'
                      r'(?:=?\s*(?P<eta>\S+))?\s*$')
WGET_SAVED = re.compile(r"saved \[(\d+)(?:/(\d+))?\]")
WGET_UNITS = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}


def log_lines(unit: str) -> list[str]:
    """This unit's own log lines, newest last."""
    try:
        unit = safe_unit(unit)
    except BadDownload:
        return []
    try:
        with open(os.path.join(LOG_DIR, unit + '.log'), encoding='utf-8', errors='replace') as f:
            lines = [line.strip() for line in f if line.strip()]
    except OSError:
        lines = []
    if lines:
        return lines
    return journal_lines(unit)


def progress_for(unit: str) -> dict:
    """Downloaded/total/speed from the log: aria2c's summary lines, else wget's progress rows."""
    lines = log_lines(unit)
    out: dict = {}
    for line in reversed(lines):
        if ARIA2_ERROR.match(line):
            continue
        m = ARIA2_SUMMARY.search(line)
        if m:
            out = {
                'done_bytes': int(float(m['done']) * ARIA2_UNITS.get(m['du'], 1)),
                'total_bytes': int(float(m['total']) * ARIA2_UNITS.get(m['tu'], 1)),
                'pct': int(m['pct']),
            }
            if m['speed']:
                out['speed_bytes'] = int(float(m['speed']) * ARIA2_UNITS.get(m['su'], 1))
            if m['eta']:
                out['eta'] = m['eta']
            break
        if ARIA2_DONE.match(line) or ARIA2_FAILED.match(line):
            break  # this download's own worker already reported: no live progress
    if out:
        return out
    # No aria2c summary in this log: a rate-limited wget download writes a Length: line instead.
    if any(WGET_LENGTH.search(line) for line in lines):
        return wget_progress(lines)
    return out


def wget_progress(lines: list[str]) -> dict:
    """Progress for a wget (rate-limited) download, read out of its own log."""
    out: dict = {}
    for line in lines:
        m = WGET_LENGTH.search(line)
        if m:
            out['total_bytes'] = int(m.group(1))
        m = WGET_ROW.match(line)
        if m:
            out['done_bytes'] = int(float(m['off']) * WGET_UNITS.get(m['ou'] or 'K', 1024))
            out['pct'] = int(m['pct'])
            out['speed_bytes'] = int(float(m['speed']) * WGET_UNITS.get(m['su'] or 'M', 1024 ** 2))
            if m['eta']:
                out['eta'] = m['eta']
        m = WGET_SAVED.search(line)
        if m:
            out['done_bytes'] = int(m.group(1))
            out['total_bytes'] = int(m.group(2) or m.group(1))
            out['pct'] = 100 if out.get('total_bytes') else out.get('pct', 0)
    return out


# ------------------------------------------------------------------- start ---

def start_debrid(url, dest, name='') -> dict:
    """Start one debrid download as a systemd unit. Validated here, always."""
    url = safe_url(url)
    dest = safe_dest(dest)
    name = media_name(name or name_from_url(url))
    unit = unit_name(name)
    now = time.time()

    current = job_state(unit, dest, name)
    if current['state'] in ('downloading', 'finished', 'present'):
        message = f'{name} is already {current["state"]} at {dest}'
        return {'ok': True, 'state': current['state'], 'unit': unit, 'dest': dest,
                'name': name, 'message': message}

    os.makedirs(LOG_DIR, exist_ok=True)
    write_job(unit, state='running', started=now, dest=dest, name=name, error=None)
    cmd = ['systemd-run', f'--unit={unit}', '--collect', 'python3', __file__,
           'run', unit, url, dest, name]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = (getattr(exc, 'stderr', '') or str(exc)).strip()[:200]
        write_job(unit, state='failed', finished=time.time(), error=detail)
        raise BadDownload(f'could not start the download: {detail}')
    return {'ok': True, 'state': 'downloading', 'unit': unit, 'dest': dest, 'name': name,
            'message': f'started {unit}: {name} -> {dest} (aria2c, full speed)'}


def run(unit: str, url, dest, name) -> int:
    """The worker inside the unit: aria2c at full speed, then its exit record.

    run() opens this unit's own log under LOG_DIR and sends progress there as well as to the
    journal. Before, only the shell front end created that file, so a download started by the
    dashboard had no log to read: homelab-download status skipped it, and no progress existed.
    """
    unit = safe_unit(unit)
    url = safe_url(url)
    dest = safe_dest(dest)
    name = media_name(name)
    os.makedirs(LOG_DIR, exist_ok=True)
    handle = open(os.path.join(LOG_DIR, unit + '.log'), 'a', encoding='utf-8', buffering=1)
    sys.stdout = _Tee((sys.__stdout__, handle))
    sys.stderr = sys.stdout
    for _ in range(60):
        if mount_ok():
            break
        print('waiting for the Unraid mount', flush=True)
        time.sleep(15)
    else:
        write_job(unit, state='failed', finished=time.time(), error='the Unraid share is not mounted')
        print('FAILED the Unraid share is not mounted', flush=True)
        return 1

    def on_term(signum, frame):
        write_job(unit, state='stopped', finished=time.time(), error='stopped')
        sys.exit(143)
    signal.signal(signal.SIGTERM, on_term)

    write_job(unit, state='running', dest=dest, name=name, started=time.time(), error=None)
    try:
        final_url = resolve_redirects(url)
    except BadDownload as exc:
        # The link itself is the problem; nothing was fetched and aria2c never starts.
        write_job(unit, state='failed', finished=time.time(), error=str(exc))
        print('FAILED ' + str(exc), flush=True)
        return 1
    print(f'start aria2c -> {dest}/{name}', flush=True)
    argv = ['aria2c', *ARIA2_FLAGS, '--out=' + name, '-d', dest, final_url]
    # aria2c writes its own progress and summaries to the unit's log file (fd level, so no reader
    # thread is needed); the prints above go to that file and to the journal.
    rc = subprocess.run(argv, stdout=handle, stderr=subprocess.STDOUT).returncode

    has_file, size, ctl = file_state(dest, name)
    if rc == 0 and has_file and not ctl:
        write_job(unit, state='complete', finished=time.time(), bytes=size, rc=0, error=None)
        print(f'DONE {dest}/{name} ({size} bytes)', flush=True)
        return 0
    if rc < 0:
        write_job(unit, state='stopped', finished=time.time(), rc=rc, error='aria2c was killed')
        print('STOPPED aria2c was killed', flush=True)
        return 1
    reason = f'aria2c exited with code {rc}'
    if ctl:
        reason += ' (the .aria2 control file is still there, so the file is incomplete)'
    elif not has_file:
        reason += ' (no file was written)'
    write_job(unit, state='failed', finished=time.time(), rc=rc, error=reason)
    print('FAILED ' + reason, flush=True)
    return 1


def wait_mount_and_run(*args, **kwargs):  # kept so old unit scripts still work
    return run(*args, **kwargs)


# ------------------------------------------------------------------ status ---

def fmt_bytes(n) -> str:
    n = int(n or 0)
    for unit, size in (('T', 1024 ** 4), ('G', 1024 ** 3), ('M', 1024 ** 2)):
        if n >= size:
            return '%.1f %sB' % (n / size, unit)
    return '%d B' % n


def duration(secs: int) -> str:
    if secs >= 3600:
        return '%dh%02dm' % (secs // 3600, (secs % 3600) // 60)
    return '%dm' % (secs // 60)


def running_for(path: str) -> str:
    return duration(max(0, int(time.time() - os.path.getctime(path))))


def running_age(job: dict | None, path: str | None, unit: str | None = None) -> str:
    """How long a download has been going: its log file's age, else its job record.

    A download the dashboard started has a job record from its first second (with a started field),
    and its log opens inside the unit, so either one answers this. The wget path records no started
    field, so the record file's own age is the next best start. An empty string means nothing to say.
    """
    if path:
        return running_for(path)
    started = (job or {}).get('started')
    if started:
        return duration(max(0, int(time.time() - float(started))))
    if not unit:
        return ''
    try:
        return running_for(job_path(unit))
    except (OSError, BadDownload):
        return ''



def legacy_state(unit: str, lines: list[str]) -> str:
    """Downloads started before job records existed: read the unit and its log."""
    if unit_active(unit) in ('active', 'activating', 'deactivating'):
        return 'downloading'
    for line in reversed(lines):
        if ARIA2_DONE.match(line) or ' saved [' in line:
            return 'finished'
        if ARIA2_FAILED.match(line) or line.startswith('[ERROR]') or line.startswith('FAILED'):
            return 'failed'
    return 'unknown'


def status_sources() -> list[tuple[str, str | None]]:
    """Every download still known: (unit, its own log file path, or None).

    The union of three sources: log files (always there once run() opened one), job records, and
    the dl-* units systemd knows about. Listing only log files hid every download the dashboard
    started, which is why homelab-download status skipped them. Newest first, by the newest stamp
    we can see for that unit.
    """
    stamp: dict[str, float] = {}
    paths: dict[str, str | None] = {}
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        names = []
    for base in names:
        if not base.endswith('.log'):
            continue
        try:
            unit = safe_unit(unit_from_log(base))
        except BadDownload:
            continue
        path = os.path.join(LOG_DIR, base)
        try:
            stamp[unit] = os.path.getmtime(path)
        except OSError:
            stamp[unit] = 0.0
        paths[unit] = path
    try:
        records = os.listdir(JOB_DIR)
    except OSError:
        records = []
    for base in records:
        if not base.endswith('.json'):
            continue
        try:
            unit = safe_unit(base[:-len('.json')])
        except BadDownload:
            continue
        try:
            seen = os.path.getmtime(os.path.join(JOB_DIR, base))
        except OSError:
            seen = 0.0
        stamp[unit] = max(stamp.get(unit, 0.0), seen)
        paths.setdefault(unit, None)
    for candidate in systemctl_units():
        try:
            unit = safe_unit(candidate)
        except BadDownload:
            continue
        stamp.setdefault(unit, 0.0)
        paths.setdefault(unit, None)
    return [(unit, paths.get(unit)) for unit in sorted(stamp, key=lambda u: stamp[u], reverse=True)]


def status_lines() -> list[str]:
    """One line per download, newest first: unit, state, then progress from its log or journal."""
    out = []
    for unit, path in status_sources():
        lines = log_lines(unit)
        try:
            job = read_job(unit)
        except BadDownload:
            continue
        try:
            st = job_state(unit, (job or {}).get('dest'), (job or {}).get('name'))
        except BadDownload:
            continue
        word = st['state']
        if job is None and word == 'unknown':
            word = legacy_state(unit, lines)   # units from before job records existed
        bits = [f'{unit}: {word.upper()}']
        if st.get('total_bytes'):
            # How far it got and how fast it was going: true for a running, stopped or failed job too.
            bits.append('downloaded %s of %s (%d%%)' % (
                fmt_bytes(st.get('done_bytes')), fmt_bytes(st['total_bytes']), st.get('pct', 0)))
            if st.get('speed_bytes') is not None:
                bits.append('speed %s/s' % fmt_bytes(st['speed_bytes']))
            if st.get('eta'):
                bits.append('time remaining about %s' % st['eta'])
        elif st.get('bytes'):
            bits.append('%s on disk' % fmt_bytes(st['bytes']))
        if lines and lines[0].startswith('waiting for'):
            bits.append('(waiting for the Unraid mount)')
        age = running_age(job, path, unit)
        if age and word in ('downloading', 'interrupted', 'stopped'):
            bits.append('running for %s' % age)
        if st.get('error') and word in ('failed', 'interrupted', 'missing', 'unknown'):
            bits.append(str(st['error'])[:120])
        out.append(' | '.join(bits))
    return out


def status_map() -> dict:
    return {line.split(': ', 1)[0]: line for line in status_lines()}


# -------------------------------------------------------------------- stop ---

def stop_download(name) -> dict:
    """Stop a download by its file name. Only a job this worker started can be stopped.

    The unit's own worker writes the 'stopped' record when it gets SIGTERM; this writes one
    too, so a job that never started (systemd-run failed, or the unit vanished) also ends as stopped.
    """
    name = safe_name(name)
    unit = unit_name(name)
    job = read_job(unit)
    active = unit_active(unit)
    if job is None and active not in ('active', 'activating', 'deactivating'):
        raise BadDownload('there is no download with that file name to stop')
    message = ''
    if active in ('active', 'activating', 'deactivating'):
        try:
            subprocess.run(['systemctl', 'stop', unit], capture_output=True, text=True, timeout=90, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            detail = (getattr(exc, 'stderr', '') or str(exc)).strip()[:200]
            raise BadDownload('could not stop %s: %s' % (unit, detail))
        message = 'stopped ' + unit
    write_job(unit, state='stopped', finished=time.time(), error='stopped by request')
    return {'ok': True, 'unit': unit, 'name': name, 'state': 'stopped',
            'message': message or ('marked %s stopped' % unit)}


# -------------------------------------------------------------------- rpc ---

MAX_CHECK_JOBS = 60


def rpc_reply(req) -> dict:
    if not isinstance(req, dict) or req.get('v') != VERSION:
        return {'ok': False, 'error': 'this interface is version %d' % VERSION}
    cmd = req.get('cmd')
    try:
        if cmd == 'ping':
            return {'ok': True, 'software': 'homelab-download', 'version': VERSION}
        if cmd == 'start':
            info = start_debrid(req.get('url'), req.get('dest'), req.get('name') or '')
            return {'ok': True, 'unit': info['unit'], 'state': info['state'],
                    'dest': info['dest'], 'name': info['name'], 'message': info['message']}
        if cmd == 'stop':
            info = stop_download(req.get('name'))
            return {'ok': True, 'unit': info['unit'], 'name': info['name'], 'state': info['state'],
                    'message': info['message']}
        if cmd == 'check':
            jobs = req.get('jobs')
            if not isinstance(jobs, list) or len(jobs) > MAX_CHECK_JOBS:
                raise BadDownload('this request asks for too many downloads')
            out = []
            for item in jobs:
                if not isinstance(item, dict):
                    raise BadDownload('each download needs a folder and a file name')
                dest = safe_dest(item.get('dest'))
                name = safe_name(item.get('name'))
                st = job_state(unit_name(name), dest, name)
                st.pop('active', None)
                out.append(st)
            return {'ok': True, 'jobs': out}
        return {'ok': False, 'error': 'unknown command'}
    except BadDownload as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception as exc:  # never leak a traceback to the caller
        return {'ok': False, 'error': 'the download worker could not run that request'}


# ------------------------------------------------------------------- main ---

def main(argv) -> int:
    cmd = argv[0] if argv else 'help'
    try:
        return dispatch(cmd, argv)
    except BadDownload as exc:
        print('FAILED ' + str(exc), file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


def dispatch(cmd: str, argv) -> int:
    if cmd == 'run' and len(argv) >= 5:
        return run(argv[1], argv[2], argv[3], argv[4])
    if cmd == 'status':
        for line in status_lines():
            print(line)
        return 0
    if cmd == 'start-debrid' and len(argv) >= 3:
        info = start_debrid(argv[1], argv[2], argv[3] if len(argv) > 3 else '')
        print(info['message'])
        print('unit: %s' % info['unit'])
        return 0
    if cmd == 'rpc':
        print(json.dumps(rpc_reply(json.loads(sys.stdin.read() or '{}'))))
        return 0
    if cmd == 'stop' and len(argv) >= 2:
        try:
            print(stop_download(argv[1])['message'])
        except BadDownload as exc:
            print('FAILED ' + str(exc), file=sys.stderr)
            return 1
        return 0
    if cmd == 'dest' and len(argv) >= 2:
        print(share_dest(argv[1]))
        return 0
    if cmd == 'unit' and len(argv) >= 2:
        print(unit_name(safe_name(argv[1])))
        return 0
    if cmd == 'record' and len(argv) >= 5:
        job = record_job(argv[1], argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else '')
        print('recorded %s for %s' % (job['state'], job['unit']))
        return 0
    if cmd == 'check' and len(argv) >= 2:
        print(json.dumps(rpc_reply({'v': VERSION, 'cmd': 'check', 'jobs': json.loads(argv[1])})))
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
