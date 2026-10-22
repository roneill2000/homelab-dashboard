#!/usr/bin/env python3
"""Push ntfy alerts for things the existing watchdog, Uptime Kuma and NetAlertX don't cover.

Runs on Proxmox every 5 minutes (homelab-alerts.timer). Reads Homepage's APIs, compares with the
previous run, and sends one alert when a check goes bad and one when it recovers. A check must be
bad on two consecutive runs before alerting, except failed update runs, which alert immediately.
The ntfy topic is read from the existing watchdog so it lives in one place.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = 'http://192.168.0.2'
WATCHDOG = Path('/usr/local/bin/homelab-health-watchdog.sh')
STATE = Path('/var/lib/homelab-alerts/state.json')
INTENTIONALLY_STOPPED = {'zurg', 'Transcoding-GPU-Benchmark'}
CONSECUTIVE = 2
SNAPSHOTS = Path('/var/lib/homelab-cloud-backup/snapshots.json')
SUMMARY_SENT = Path('/var/lib/homelab-alerts/summary-day')
SOURCES = {'photos': ('Photos', 36), 'proxmox': ('Proxmox', 36), 'unraid': ('Unraid', 30)}  # tag: (label, stale after h)
SUMMARY_HOUR = 7


def fetch(path, timeout=60):
    with urllib.request.urlopen(HOME + path, timeout=timeout) as r:
        return json.load(r)


def checks(t, s, f, now):
    """Return {key: (bad, title, detail, priority)} for every check that has data. Missing data is not a bad signal."""
    out = {}
    if t is not None:
        age = now - (t.get('updated_at') or 0)
        out['homepage-stale'] = (age > 300, 'Homepage data is stale', f'Telemetry last updated {int(age // 60)} min ago.', 4)
        # Not checked here, because another sender already alerts on them (see alerts/SENDERS.md):
        # Unraid disk usage 70/90% and temperatures (Unraid ntfy agent), drive failures (Scrutiny),
        # Proxmox backup results (Proxmox notification webhook).
        st = t.get('speedtest') or {}
        tested = st.get('tested_at')
        if tested:
            try:
                ts = time.mktime(time.strptime(tested, '%Y-%m-%d %H:%M:%S'))  # Speedtest Tracker local time
                hours = (now - ts) / 3600
                out['speedtest-stale'] = (hours > 12, 'Speed tests failing', f'Last completed test was {int(hours)} h ago.', 3)
            except ValueError:
                pass
    if f is not None:
        guests = f.get('guests') or []
        for g in guests:
            for m in g.get('mounts') or []:
                out[f"mount:{g['name']}:{m['path']}"] = (m['state'] != 'ok', f"Share {m['state']} in {g['name']}", f"{m['path']} in LXC {g['vmid']} is {m['state']}.", 4)
            units = g.get('failed_units')
            if units is not None:
                out[f"units:{g['name']}"] = (bool(units), f"Failed services in {g['name']}", f"LXC {g['vmid']}: " + (', '.join(units) if units else 'none'), 3)
        for c in f.get('containers') or []:
            if c['name'] in INTENTIONALLY_STOPPED:
                continue
            out[f"docker:{c['name']}"] = (c['state'] != 'running', f"Docker app {c['name']} stopped", f"{c['name']}: {c.get('status') or c['state']}", 4)
        pi = f.get('pi')
        if pi is not None:
            # Pi unreachable / Pi-hole replica down: already alerted by homelab-health-watchdog ('Secondary Pi-hole (Orange Pi)').
            if pi.get('online'):
                if pi.get('failed_units') is not None:
                    out['pi-units'] = (bool(pi['failed_units']), 'Failed services on the Orange Pi', ', '.join(pi['failed_units']) or 'none', 3)
                for c in pi.get('containers') or []:
                    out[f"pi-docker:{c['name']}"] = (c['state'] != 'running', f"{c['name']} stopped on the Orange Pi", c.get('status') or c['state'], 4)
                if isinstance(pi.get('disk_pct'), (int, float)):
                    out['pi-disk'] = (pi['disk_pct'] >= 85, 'Orange Pi SD card filling', f"{pi['disk_pct']}% used.", 3)
                if isinstance(pi.get('temp_c'), (int, float)):
                    out['pi-temp'] = (pi['temp_c'] >= 75, 'Orange Pi running hot', f"{pi['temp_c']} °C.", 3)
        upd = f.get('updates') or {}
        failed = upd.get('failed') or []
        out['updates-failed'] = (bool(failed), 'Update run failed',
                                 '; '.join(f"{x['name']}: {x.get('detail')}" + (f" ({x['rollback']})" if x.get('rollback') else '') for x in failed) or 'No failed updates.', 5)
    return out


def cloud_backup_check(now, path=Path('/var/lib/homelab-cloud-backup/last_success')):
    """Off-site backup freshness. Silent until the first successful run exists."""
    try:
        last = int(path.read_text().strip())
    except (OSError, ValueError):
        return {}
    hours = (now - last) / 3600
    return {'cloud-backup': (hours > 36, 'Cloud backup is stale', f'Last successful Google Drive backup was {int(hours)} h ago.', 4)}


def refresh_snapshots(now, path=SNAPSHOTS, every=1800):
    """Cache the newest Google Drive snapshot per tag. Proxmox and Unraid each run their own job into the
    same repository, so the repository is the one place that sees both."""
    try:
        if now - json.loads(path.read_text())['checked_at'] < every:
            return
    except (OSError, ValueError, KeyError):
        pass
    env = dict(os.environ, RESTIC_REPOSITORY='rclone:gdrive:homelab-restic',
               RESTIC_PASSWORD_FILE='/root/.config/restic/gdrive.pass')
    try:
        r = subprocess.run(['restic', 'snapshots', '--no-lock', '--json'], env=env, capture_output=True, text=True, timeout=180)
        snaps = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        snaps = None
    if snaps is None:  # unreachable is not stale; keep the old cache
        return
    tmp = path.with_suffix('.new')
    tmp.write_text(json.dumps({'checked_at': now, 'latest': latest_by_tag(snaps)}))
    tmp.replace(path)


def latest_by_tag(snaps):
    from datetime import datetime
    latest = {}
    for snap in snaps:
        ts = datetime.fromisoformat(re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], snap['time']).replace('Z', '+00:00')).timestamp()
        for tag in snap.get('tags') or []:
            latest[tag] = max(latest.get(tag, 0), int(ts))
    return latest


def read_latest(path=SNAPSHOTS):
    try:
        return json.loads(path.read_text())['latest']
    except (OSError, ValueError, KeyError):
        return None


def unraid_backup_check(now, latest):
    """Unraid's own job (unraid-restic container, 04:00). Photos and Proxmox are covered by cloud_backup_check."""
    if not latest or 'unraid' not in latest:
        return {}
    hours = (now - latest['unraid']) / 3600
    return {'cloud-unraid': (hours > SOURCES['unraid'][1], 'Unraid cloud backup is stale',
                             f'Newest Unraid snapshot on Google Drive is {int(hours)} h old. Check the unraid-restic container.', 4)}


def daily_summary(now, latest, sent_path=SUMMARY_SENT):
    """One quiet morning message listing each off-site backup. Returns (title, body, priority) or None."""
    day = time.strftime('%Y-%m-%d', time.localtime(now))
    if time.localtime(now).tm_hour < SUMMARY_HOUR or not latest:
        return None
    try:
        if sent_path.read_text().strip() == day:
            return None
    except OSError:
        pass
    lines, stale = [], []
    for tag, (label, limit) in SOURCES.items():
        ts = latest.get(tag)
        if ts is None:
            lines.append(f'{label}: no snapshot yet'); stale.append(label); continue
        old = (now - ts) / 3600 > limit
        lines.append(f"{label}: {time.strftime('%a %-I:%M %p', time.localtime(ts))}" + (' (stale)' if old else ''))
        if old:
            stale.append(label)
    title = 'Backups OK' if not stale else 'Backups: ' + ', '.join(stale) + ' stale'
    return title, '\n'.join(lines), 2 if not stale else 3


def transitions(prev, current):
    """Yield (key, kind, title, detail, priority) to send, and the new state."""
    sends, state = [], {}
    for key, (bad, title, detail, prio) in current.items():
        p = prev.get(key, {'count': 0, 'alerted': False})
        count = p['count'] + 1 if bad else 0
        need = 1 if key == 'updates-failed' else CONSECUTIVE
        alerted = p['alerted']
        if bad and not alerted and count >= need:
            sends.append((key, 'down', title, detail, prio)); alerted = True
        elif not bad and alerted:
            sends.append((key, 'up', title, detail, 3)); alerted = False
        state[key] = {'count': count, 'alerted': alerted}
    for key, p in prev.items():  # checks that vanished keep their state (missing data is not a recovery)
        state.setdefault(key, p)
    return sends, state


def topic():
    m = re.search(r'^TOPIC="([^"]+)"', WATCHDOG.read_text(), re.M)
    if not m:
        raise SystemExit('No TOPIC in watchdog')
    return m.group(1)


def send(kind, title, detail, prio):
    head = ('RECOVERED: ' if kind == 'up' else '') + title
    tags = {'up': 'white_check_mark', 'info': 'floppy_disk'}.get(kind, 'warning')
    req = urllib.request.Request('https://ntfy.sh/' + topic(), data=detail.encode(), method='POST', headers={
        'Title': head, 'Priority': str(prio), 'Tags': tags})
    urllib.request.urlopen(req, timeout=15).read()


def main(argv):
    if '--test' in argv:
        send('down', 'Homelab alerts test', 'homelab-alerts is installed and can reach your ntfy topic.', 2)
        return 0
    now = time.time()
    def safe(path):
        try:
            return fetch(path)
        except Exception:
            return None
    t, s, f = safe('/api/telemetry'), safe('/api/storage?window=900'), safe('/api/fleet')
    if t is None:  # Homepage unreachable: Uptime Kuma already watches it; record only
        pass
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    current = checks(t, s, f, now)
    current.update(cloud_backup_check(now))
    refresh_snapshots(now)
    latest = read_latest()
    current.update(unraid_backup_check(now, latest))
    sends, state = transitions(prev, current)
    for key, kind, title, detail, prio in sends:
        try:
            send(kind, title, detail, prio)
        except Exception:
            state[key]['alerted'] = not state[key]['alerted']  # retry next run
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    summary = daily_summary(now, latest)
    if summary and '--dry-run' not in argv:
        try:
            send('info', *summary)
            SUMMARY_SENT.write_text(time.strftime('%Y-%m-%d', time.localtime(now)))
        except Exception:
            pass  # try again next run
    if '--dry-run' in argv:
        print(json.dumps({k: v[0] for k, v in checks(t, s, f, now).items()}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
