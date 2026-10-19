"""Read-only, bounded host readings for the existing Glances AMP endpoint."""
import argparse
import configparser
import json
from pathlib import Path
import subprocess
import time


def diskstats(text):
    result = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 14:
            continue
        v = list(map(int, f[3:14]))
        result[f[2]] = dict(read_ops=v[0], read_ms=v[3], write_ops=v[4],
                            write_ms=v[7], in_flight=v[8], busy_ms=v[9], weighted_ms=v[10])
    return result


def ini(path, section=False):
    c = configparser.ConfigParser(interpolation=None)
    c.read_string(('[state]\n' if section else '') + path.read_text())
    return {s.strip('"'): {k: v.strip('"') for k, v in c[s].items()} for s in c.sections()}


def parity(root):
    v = ini(root / 'var/local/emhttp/var.ini', True)['state']
    def number(k):
        try: return int(v[k])
        except (KeyError, ValueError): return None
    disks = ini(root / 'var/local/emhttp/disks.ini')
    assigned = [dict(name=d.get('name'), device=d['device'], status=d.get('status'))
                for d in disks.values() if d.get('device') and d.get('type') in ('Data', 'Parity')]
    pos, running = number('mdresyncpos'), number('mdresync')
    return dict(observed_at=int(time.time()), array_state=v.get('mdstate'), assigned_disks=assigned,
                operation='unknown' if pos is None or running is None else 'running' if running > 0 else 'paused' if pos > 0 else 'idle',
                action=v.get('mdresyncaction'), position=pos, size=number('mdresyncsize'),
                last_started_at=number('sbsynced'), last_finished_at=number('sbsynced2'),
                last_exit=number('sbsyncexit'), last_errors=number('sbsyncerrs'))


def backup(cache=None):
    cache = cache or Path('/run/homelab-backup-status.json')
    try:
        if time.time() - cache.stat().st_mtime < 60:
            return json.loads(cache.read_text())
    except (OSError, ValueError): pass
    tasks = json.loads(subprocess.check_output(['pvenode', 'task', 'list', '--typefilter', 'vzdump',
                                               '--source', 'archive', '--limit', '20', '--output-format', 'json'], timeout=8))
    active = json.loads(subprocess.check_output(['pvenode', 'task', 'list', '--typefilter', 'vzdump',
                                                '--source', 'active', '--limit', '20', '--output-format', 'json'], timeout=8))
    # Completed task status only: does not assert restoreability or every guest's coverage.
    completed = sorted((t for t in tasks if t.get('endtime')), key=lambda t: t['endtime'], reverse=True)
    def slim(t):
        return dict(started_at=t.get('starttime'), finished_at=t.get('endtime'), status=t.get('status')) if t else None
    data = dict(observed_at=int(time.time()), latest=slim(completed[0] if completed else None),
                last_success=slim(next((t for t in completed if t.get('status') == 'OK'), None)),
                running=bool(active))
    cache.write_text(json.dumps(data));cache.chmod(0o600)
    return data


def main():
    p = argparse.ArgumentParser();p.add_argument('--root', default='/');p.add_argument('--kind', choices=['pve','unraid'], required=True)
    a = p.parse_args();root = Path(a.root)
    out = dict(sampled_at=time.time(), disks=diskstats((root/'proc/diskstats').read_text()),
               boot_id=(root/'proc/sys/kernel/random/boot_id').read_text().strip())
    try:
        out['io_pressure'] = {f[0]: dict((k, float(v)) for k,v in (x.split('=') for x in f[1:]))
                              for f in (line.split() for line in (root/'proc/pressure/io').read_text().splitlines())}
    except (OSError, ValueError): out['io_pressure'] = None
    try: out['recovery'] = backup() if a.kind == 'pve' else parity(root)
    except Exception: out['recovery'] = None
    print(json.dumps(out, separators=(',', ':')))

if __name__ == '__main__': main()
