#!/usr/bin/env python3
"""SMART status and self-test scheduler for Unraid's drives.

`status` prints JSON for Homepage (via the update runner's `health`). `schedule` runs daily from cron and starts
an extended test on each spinning array disk whose last extended test is older than EXTENDED_DAYS, and a short
test on SSDs older than SHORT_DAYS. It never starts a test during a parity check, on Sunday (parity day), or while
another test is running on that disk. Read-only apart from `smartctl -t`.
"""
import configparser
import json
import re
import subprocess
import sys
import time
from pathlib import Path

EXTENDED_DAYS = 90
SHORT_DAYS = 30
WATCH = {5: 'reallocated', 187: 'reported_uncorrectable', 197: 'pending', 198: 'offline_uncorrectable', 199: 'crc_errors'}
LOG = Path('/mnt/disk1/.homelab-smart/schedule.log')
LAUNCHES = Path('/mnt/disk1/.homelab-smart/launches.json')  # last launch attempt per device, ok or not


def smartctl(*args):
    r = subprocess.run(['smartctl', *args], capture_output=True, text=True, timeout=60)
    return r.stdout


def roles():
    """device -> Unraid role (parity, disk1, cache, flash) from emhttp's disks.ini."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(Path('/var/local/emhttp/disks.ini').read_text())
    except OSError:
        return {}
    return {cp[s].get('device', '').strip('"'): s.strip('"') for s in cp.sections() if cp[s].get('device', '').strip('"')}


def parse_selftests(text):
    tests = []
    for line in text.splitlines():
        m = re.match(r'#\s*\d+\s+(Short|Extended|Conveyance)\s+offline\s+(.+?)\s{2,}(\d+)%\s+(\d+)', line)
        if m:
            tests.append(dict(kind=m[1].lower(), result=m[2].strip(), remaining_pct=int(m[3]), at_hours=int(m[4])))
    return tests


def parse_attrs(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[0].isdigit() and int(parts[0]) in WATCH:
            raw = re.match(r'\d+', parts[9])
            out[WATCH[int(parts[0])]] = int(raw.group()) if raw else None
        if len(parts) >= 10 and parts[0] == '9':
            raw = re.match(r'\d+', parts[9])
            out['power_on_hours'] = int(raw.group()) if raw else None
        if len(parts) >= 10 and parts[0] in ('190', '194'):
            raw = re.match(r'\d+', parts[9])
            out.setdefault('temp_c', int(raw.group()) if raw else None)
    return out


def running_pct(text):
    m = re.search(r'Self-test execution status:.*?(\d+)% of test remaining', text, re.S)
    return int(m[1]) if m and 'in progress' in text else None


def disk(dev, role):
    info = smartctl('-i', dev)
    model = re.search(r'(?:Device Model|Model Number):\s*(.+)', info)
    rotational = 'Rotation Rate:' in info and 'Solid State' not in info
    attrs = parse_attrs(smartctl('-A', dev))
    tests = parse_selftests(smartctl('-l', 'selftest', dev))
    hours = attrs.pop('power_on_hours', None)
    def last(kind):
        t = next((t for t in tests if t['kind'] == kind), None)
        if t and hours is not None:
            t = dict(t, hours_ago=hours - t['at_hours'])
        return t
    return dict(device=dev.rsplit('/', 1)[-1], role=role, model=model[1].strip() if model else None, rotational=rotational,
                power_on_hours=hours, attrs=attrs, last_extended=last('extended'), last_short=last('short'),
                running_remaining_pct=running_pct(smartctl('-c', dev)))


def devices():
    r = roles()
    return [(f'/dev/{d}', role) for d, role in sorted(r.items()) if role != 'flash' and Path(f'/dev/{d}').exists()]


def status():
    cron = Path('/boot/config/plugins/dynamix/homelab-smart.cron')
    return dict(observed_at=int(time.time()), extended_every_days=EXTENDED_DAYS, short_every_days=SHORT_DAYS,
                scheduler_installed_at=int(cron.stat().st_mtime) if cron.exists() else None,
                launches=json.loads(LAUNCHES.read_text()) if LAUNCHES.exists() else {},
                disks=[disk(dev, role) for dev, role in devices()])


def parity_running():
    out = subprocess.run(['/usr/local/sbin/mdcmd', 'status'], capture_output=True, text=True).stdout
    m = re.search(r'^mdResyncPos=(\d+)', out, re.M)
    return bool(m and int(m[1]) > 0)


def due(d):
    """Return 'long', 'short' or None."""
    if d['running_remaining_pct'] is not None or d['power_on_hours'] is None:
        return None
    if d['rotational'] and d['role'].startswith(('parity', 'disk')):
        t = d['last_extended']
        return 'long' if t is None or t['hours_ago'] >= EXTENDED_DAYS * 24 else None
    t = d['last_short']
    return 'short' if t is None or t['hours_ago'] >= SHORT_DAYS * 24 else None


def record_launch(dev, kind, ok, message):
    try:
        data = json.loads(LAUNCHES.read_text())
    except (OSError, ValueError):
        data = {}
    data[dev.rsplit('/', 1)[-1]] = dict(kind=kind, at=int(time.time()), ok=ok, message=message)
    LAUNCHES.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHES.write_text(json.dumps(data))


def schedule():
    if time.localtime().tm_wday == 6 or parity_running():
        return []
    started = []
    for dev, role in devices():
        kind = due(disk(dev, role))
        if kind:
            text = smartctl('-t', kind, dev)
            ok = 'has begun' in text or 'Testing has begun' in text
            record_launch(dev, kind, ok, '' if ok else text.strip().splitlines()[-1][:200] if text.strip() else 'no output from smartctl')
            started.append(f"{dev} {kind}{'' if ok else ' FAILED'}")
    if started:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M ') + ', '.join(started) + '\n')
    return started


if __name__ == '__main__':
    if sys.argv[1:] == ['status']:
        print(json.dumps(status()))
    elif sys.argv[1:] == ['schedule']:
        print(json.dumps(schedule()))
    else:
        sys.exit('usage: homelab_smart.py status|schedule')
