#!/usr/bin/env python3
"""Monthly off-site restore check: prove files come back out of Google Drive intact.

Reads from the restic repository only (no restore to live paths). For each backup tag it streams a sample of files
out of the newest snapshot with `restic dump` and checks them:
  photos  - 20 random photos, SHA-256 equal to the original on the Unraid share (skips files changed since)
  unraid  - 10 random small files from the flash config and Pi-hole backups (small subtrees; see files()), SHA-256 equal to the original on Unraid (via ssh)
  proxmox - the smallest guest archive, `zstd -t` integrity check and equal to the local copy if still present
  configs - every config archive, `tar -tz` readable and equal to the local copy
Writes /var/lib/homelab-cloud-backup/restore-test.json (latest) and restore-tests.jsonl (history).
A check that cannot run is "unknown", not failed. This proves retrieval and integrity, not that an
application starts; the Memos and Open WebUI drills of 20 Oct cover that for those apps.
"""
import fcntl
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

STATE = Path('/var/lib/homelab-cloud-backup')
ENV = dict(os.environ, RESTIC_REPOSITORY='rclone:gdrive:homelab-restic', RESTIC_PASSWORD_FILE='/root/.config/restic/gdrive.pass')
UNRAID = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'root@192.168.0.4']
LOCK = '/run/homelab-cloud-backup.lock'  # shared with the nightly backup so they never overlap


def restic_json(*args):
    r = subprocess.run(['restic', *args, '--json'], env=ENV, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise RuntimeError(f"restic {args[0]} failed: {r.stderr.strip().splitlines()[-1][:160] if r.stderr.strip() else r.returncode}")
    return r.stdout


def newest(tag, host):
    snaps = [s for s in json.loads(restic_json('snapshots', '--tag', tag, '--host', host)) if s.get('tags')]
    if not snaps:
        raise RuntimeError(f'no {tag} snapshot')
    return max(snaps, key=lambda s: s['time'])


def files(snap, *dirs):
    """Files in a snapshot, optionally only under dirs (restic then skips other subtrees, which matters for
    snapshots made on another host: their directory data is not in Proxmox's local cache and each
    directory is a separate Google Drive request)."""
    out = []
    for line in restic_json('ls', snap['id'], *dirs).splitlines():
        if line.startswith('{'):
            row = json.loads(line)
            if row.get('type') == 'file':
                out.append(row)
    return out


def dump_hash(snap, path, pipe=None):
    """sha256 of a file streamed out of the repository; optionally also piped to a checker command."""
    p = subprocess.Popen(['restic', 'dump', snap['id'], path], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    checker = subprocess.Popen(pipe, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if pipe else None
    h, n = hashlib.sha256(), 0
    for block in iter(lambda: p.stdout.read(1 << 20), b''):
        h.update(block); n += len(block)
        if checker:
            checker.stdin.write(block)
    if checker:
        checker.stdin.close()
    ok = p.wait() == 0 and (checker.wait() == 0 if checker else True)
    return (h.hexdigest() if ok else None), n


def local_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def snap_time(snap):
    import datetime, re
    return datetime.datetime.fromisoformat(re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], snap['time']).replace('Z', '+00:00')).timestamp()


def check_photos():
    snap = newest('photos', 'homelab'); taken = snap_time(snap)
    pool = [f for f in files(snap) if f['size'] > 0]
    random.shuffle(pool)
    good = bad = 0; size = 0; notes = []
    for f in pool:
        if good + bad >= 20:
            break
        try:
            st = os.stat(f['path'])
        except OSError:
            continue  # deleted since the snapshot; not a restore failure
        if st.st_mtime > taken or st.st_size != f['size']:
            continue  # changed since the snapshot
        digest, n = dump_hash(snap, f['path'])
        size += n
        if digest and digest == local_hash(f['path']):
            good += 1
        else:
            bad += 1; notes.append(os.path.basename(f['path']))
    return result('photos', 'Photos', snap, good, bad, size, f'{good} of {good + bad} sampled photos match the originals' + (f"; mismatched: {', '.join(notes[:3])}" if notes else ''), minimum=10)


def check_unraid():
    snap = newest('unraid', 'unraid'); taken = snap_time(snap)
    pool = [f for f in files(snap, '/boot-config', '/data/backup/backups/pihole') if 0 < f['size'] <= 5 << 20 and '/restic' not in f['path']]
    random.shuffle(pool)
    good = bad = 0; size = 0
    for f in pool[:40]:
        if good + bad >= 10:
            break
        orig = '/mnt/user' + f['path'][len('/data'):] if f['path'].startswith('/data/') else '/boot/config' + f['path'][len('/boot-config'):] if f['path'].startswith('/boot-config/') else None
        if not orig:
            continue
        r = subprocess.run(UNRAID + [f"stat -c %Y {sh(orig)} && sha256sum {sh(orig)}"], capture_output=True, text=True, timeout=60)
        if r.returncode:
            continue
        mtime, line = r.stdout.splitlines()[0], r.stdout.splitlines()[1]
        if int(mtime) > taken:
            continue
        digest, n = dump_hash(snap, f['path'])
        size += n
        if digest and digest == line.split()[0]:
            good += 1
        else:
            bad += 1
    return result('unraid', 'Unraid appdata + flash', snap, good, bad, size, f'{good} of {good + bad} sampled files match the originals on Unraid', minimum=5)


def check_proxmox():
    snap = newest('proxmox', 'homelab')
    archives = sorted((f for f in files(snap) if f['path'].endswith('.zst')), key=lambda f: f['size'])
    if not archives:
        raise RuntimeError('no guest archive in snapshot')
    f = archives[0]
    digest, n = dump_hash(snap, f['path'], pipe=['zstd', '-t', '-q'])
    same = os.path.exists(f['path']) and digest == local_hash(f['path'])
    ok = digest is not None
    detail = f"{os.path.basename(f['path'])} ({round(n / 1e6)} MB) decompresses cleanly" + ('' if not ok else ' and matches the local copy' if same else '; local copy already rotated')
    return result('proxmox', 'Proxmox guests', snap, 1 if ok else 0, 0 if ok else 1, n, detail if ok else f"{os.path.basename(f['path'])} failed the integrity check", minimum=1)


def check_configs():
    snap = newest('configs', 'homelab')
    good = bad = 0; size = 0
    for f in files(snap):
        if not f['path'].endswith('.tgz'):
            continue
        digest, n = dump_hash(snap, f['path'], pipe=['tar', '-tzf', '-'])
        size += n
        if digest and (not os.path.exists(f['path']) or digest == local_hash(f['path'])):
            good += 1
        else:
            bad += 1
    return result('configs', 'Router + host configs', snap, good, bad, size, f'{good} of {good + bad} config archives readable and intact', minimum=1)


def sh(s):
    return "'" + s.replace("'", "'\\''") + "'"


def result(key, label, snap, good, bad, size, detail, minimum):
    ok = bad == 0 and good >= minimum
    return dict(key=key, label=label, ok=ok if (good + bad) else None, files=good + bad, bytes=size, detail=detail,
                snapshot=snap['short_id'], snapshot_time=int(snap_time(snap)))


def main():
    lock = open(LOCK, 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('backup running; restore test skipped'); return 0
    started = time.time(); checks = []
    for fn in (check_configs, check_proxmox, check_unraid, check_photos):
        t0 = time.time()
        try:
            c = fn()
        except Exception as e:
            c = dict(key=fn.__name__[6:], label=fn.__name__[6:], ok=None, detail=f'Could not run: {e}'[:240])
        c['seconds'] = round(time.time() - t0)
        checks.append(c)
    states = [c['ok'] for c in checks]
    report = dict(started_at=int(started), finished_at=int(time.time()), checks=checks,
                  ok=False if False in states else (True if all(s is True for s in states) else None))
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / 'restore-test.json.new'; tmp.write_text(json.dumps(report)); tmp.replace(STATE / 'restore-test.json')
    with open(STATE / 'restore-tests.jsonl', 'a') as f:
        f.write(json.dumps(report) + '\n')
    print(json.dumps(report, indent=1))
    return 0 if report['ok'] is not False else 1


if __name__ == '__main__':
    sys.exit(main())
