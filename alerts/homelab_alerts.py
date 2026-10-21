#!/usr/bin/env python3
"""Push ntfy alerts for things the existing watchdog, Uptime Kuma and NetAlertX don't cover.

Runs on Proxmox every 5 minutes (homelab-alerts.timer). Reads Homepage's APIs, compares with the
previous run, and sends one alert when a check goes bad and one when it recovers. A check must be
bad on two consecutive runs before alerting, except failed update runs, which alert immediately.
The ntfy topic is read from the existing watchdog so it lives in one place.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HOME = 'http://192.168.0.2'
WATCHDOG = Path('/usr/local/bin/homelab-health-watchdog.sh')
STATE = Path('/var/lib/homelab-alerts/state.json')
INTENTIONALLY_STOPPED = {'zurg', 'Transcoding-GPU-Benchmark'}
CONSECUTIVE = 2


def fetch(path, timeout=60):
    with urllib.request.urlopen(HOME + path, timeout=timeout) as r:
        return json.load(r)


def checks(t, s, f, now):
    """Return {key: (bad, title, detail, priority)} for every check that has data. Missing data is not a bad signal."""
    out = {}
    if t is not None:
        age = now - (t.get('updated_at') or 0)
        out['homepage-stale'] = (age > 300, 'Homepage data is stale', f'Telemetry last updated {int(age // 60)} min ago.', 4)
        u = t.get('unraid') or {}
        if u.get('online') and isinstance(u.get('cache_pct'), (int, float)):
            out['cache-full'] = (u['cache_pct'] >= 85, 'Unraid cache filling', f"Cache {u['cache_pct']}% used.", 4)
        if u.get('online') and isinstance(u.get('array_pct'), (int, float)):
            out['array-full'] = (u['array_pct'] >= 90, 'Unraid array filling', f"Array {u['array_pct']}% used.", 4)
        drives = (t.get('scrutiny') or {}).get('drives') or []
        if (t.get('scrutiny') or {}).get('online'):
            bad = [d.get('name') for d in drives if d.get('healthy') is False]
            out['drives'] = (bool(bad), 'Drive health warning', 'Scrutiny reports unhealthy: ' + ', '.join(map(str, bad)) if bad else 'All drives healthy.', 5)
        st = t.get('speedtest') or {}
        tested = st.get('tested_at')
        if tested:
            try:
                ts = time.mktime(time.strptime(tested, '%Y-%m-%d %H:%M:%S'))  # Speedtest Tracker local time
                hours = (now - ts) / 3600
                out['speedtest-stale'] = (hours > 12, 'Speed tests failing', f'Last completed test was {int(hours)} h ago.', 3)
            except ValueError:
                pass
    if s is not None:
        b = ((s.get('activity') or {}).get('recovery') or {}).get('backup') or {}
        latest = (b.get('latest') or {}).get('status')
        if latest:
            out['backup'] = (latest != 'OK', 'Proxmox backup job failed', f'Latest backup job status: {latest}.', 4)
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
            out['pi-reachable'] = (pi.get('online') is False, 'Orange Pi unreachable', 'Proxmox could not run its health probe on 192.168.0.10.', 4)
            if pi.get('online'):
                if pi.get('failed_units') is not None:
                    out['pi-units'] = (bool(pi['failed_units']), 'Failed services on the Orange Pi', ', '.join(pi['failed_units']) or 'none', 3)
                out['pi-pihole'] = (pi.get('pihole') != 'active', 'Pi-hole replica not running', f"pihole-FTL is {pi.get('pihole')} on the Orange Pi.", 4)
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
    req = urllib.request.Request('https://ntfy.sh/' + topic(), data=detail.encode(), method='POST', headers={
        'Title': head, 'Priority': str(prio), 'Tags': 'white_check_mark' if kind == 'up' else 'warning'})
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
    sends, state = transitions(prev, checks(t, s, f, now))
    for key, kind, title, detail, prio in sends:
        try:
            send(kind, title, detail, prio)
        except Exception:
            state[key]['alerted'] = not state[key]['alerted']  # retry next run
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    if '--dry-run' in argv:
        print(json.dumps({k: v[0] for k, v in checks(t, s, f, now).items()}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
