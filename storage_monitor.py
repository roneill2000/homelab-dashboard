"""Read-only Glances collector with 24-hour, server-side disk history."""
import concurrent.futures
import json
import os
import sqlite3
import threading
import time
import urllib.request

DB = os.environ.get('HOMELAB_METRICS_DB', '/var/lib/homelab-dashboard/metrics.sqlite3')
DISKS = [
    ('scratch', 'Proxmox scratch', '192.168.0.3', 'sda', '/mnt/share', 'SMB · n100 / Prowlarr'),
    ('array', 'Unraid array', '192.168.0.4', 'sdc', '/rootfs/mnt/disk1', 'Data disk · parity excluded'),
    ('cache', 'Unraid cache', '192.168.0.4', 'sdd', '/rootfs/mnt/cache', 'Cache pool · SSD'),
    ('parity', 'Unraid parity', '192.168.0.4', 'sdb', None, 'Parity I/O · separate from data'),
]
_latest, _previous = {}, {}
_lock = threading.Lock()
_started = False

def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    return db

def initialize():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS samples (id TEXT, ts INTEGER, read_bps REAL, write_bps REAL, used_pct REAL, latency_ms REAL, PRIMARY KEY(id, ts))')

def rates(previous, current, seconds):
    """Glances gauges are cumulative; never divide an already-normalized rate."""
    if not previous or seconds <= 0 or seconds > 90:
        return None, None, None
    keys = ('read_bytes_gauge', 'write_bytes_gauge')
    if any(current.get(k) is None or previous.get(k) is None for k in keys):
        return None, None, None
    delta = [current[k] - previous[k] for k in keys]
    if min(delta) < 0:
        return None, None, None
    latency = None
    timing = ('read_time_gauge', 'write_time_gauge', 'read_count_gauge', 'write_count_gauge')
    if all(current.get(k) is not None and previous.get(k) is not None for k in timing):
        dt = [current[k] - previous[k] for k in timing]
        if min(dt) >= 0 and dt[2] + dt[3] > 0:
            latency = (dt[0] + dt[1]) / (dt[2] + dt[3])
    return delta[0] / seconds, delta[1] / seconds, latency

def fetch(host):
    out = {}
    for endpoint in ('diskio', 'fs'):
        with urllib.request.urlopen('http://' + host + ':61208/api/4/' + endpoint, timeout=4) as response:
            out[endpoint] = json.load(response)
    return out

def collect():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {host: pool.submit(fetch, host) for host in ('192.168.0.3', '192.168.0.4')}
        hosts = {}
        for host, future in futures.items():
            try:
                hosts[host] = future.result()
            except Exception:
                hosts[host] = None
    now = int(time.time())
    rows, latest = [], {}
    for key, name, host, device, mount, description in DISKS:
        payload = hosts[host]
        disk = next((d for d in (payload or {}).get('diskio', []) if d.get('disk_name') == device), None)
        fs = next((f for f in (payload or {}).get('fs', []) if f.get('mnt_point') == mount), None)
        prev = _previous.get(key)
        read, write, latency = rates(prev[1] if prev else None, disk or {}, now - prev[0] if prev else 0)
        if disk:
            _previous[key] = (now, disk)
        else:
            _previous.pop(key, None)
        used = fs.get('percent') if fs else None
        latest[key] = dict(id=key, name=name, host=host, device=device, mount=mount, description=description,
                           online=disk is not None, sampled_at=now, read_bps=read, write_bps=write,
                           latency_ms=latency, busy_pct=None, used_pct=used,
                           size_bytes=fs.get('size') if fs else None,
                           used_bytes=fs.get('used') if fs else None,
                           free_bytes=fs.get('free') if fs else None)
        rows.append((key, now, read, write, used, latency))
    with connect() as db:
        db.executemany('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?)', rows)
        db.execute('DELETE FROM samples WHERE ts < ?', (now - 86400,))
    with _lock:
        _latest.clear()
        _latest.update(latest)

def snapshot(window=3600):
    window = min(86400, max(900, int(window)))
    now = int(time.time())
    with _lock:
        latest = {key: dict(value) for key, value in _latest.items()}
    with connect() as db:
        first = db.execute('SELECT MIN(ts) FROM samples').fetchone()[0]
        bucket = max(10, min(window, now-first if first else window) // 240)
        rows = db.execute('SELECT id, MAX(ts), AVG(read_bps), AVG(write_bps), AVG(used_pct), AVG(latency_ms) FROM samples WHERE ts >= ? GROUP BY id, CAST(ts / ? AS INTEGER) ORDER BY MAX(ts)', (now-window, bucket)).fetchall()
        first = db.execute('SELECT MIN(ts) FROM samples').fetchone()[0]
    for key, name, host, device, mount, desc in DISKS:
        latest.setdefault(key, dict(id=key, name=name, host=host, device=device, mount=mount, description=desc, online=False, sampled_at=None))
        latest[key]['bucket_seconds'] = bucket
        latest[key]['history'] = [dict(ts=r[1], read_bps=r[2], write_bps=r[3], used_pct=r[4], latency_ms=r[5]) for r in rows if r[0] == key]
    return dict(disks=list(latest.values()), collected_at=now, history_since=first, window=window,
                sample_seconds=10, bucket_seconds=bucket, retention_hours=24, source='Glances cumulative disk counters')

def start():
    global _started
    if _started:
        return
    initialize()
    _started = True
    def loop():
        while True:
            try:
                collect()
            except Exception as exc:
                print('Storage collector:', type(exc).__name__, flush=True)
            time.sleep(10)
    threading.Thread(target=loop, name='storage-history', daemon=True).start()
