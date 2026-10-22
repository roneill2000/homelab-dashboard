"""Read-only Glances collector with 24-hour, server-side disk history."""
import activity_monitor
import gpu_monitor
import net_monitor
import ai_monitor
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
        db.execute('CREATE TABLE IF NOT EXISTS capacity_samples (id TEXT, ts INTEGER, free_bytes REAL, size_bytes REAL, PRIMARY KEY(id, ts))')
        db.execute('CREATE TABLE IF NOT EXISTS samples (id TEXT, ts INTEGER, read_bps REAL, write_bps REAL, used_pct REAL, latency_ms REAL, PRIMARY KEY(id, ts))')

    activity_monitor.initialize(connect)
    gpu_monitor.initialize(connect)
    net_monitor.initialize(connect)

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
    try:
        with urllib.request.urlopen('http://' + host + ':61208/api/4/amps', timeout=4) as response:
            out['amps'] = json.load(response)
    except Exception:
        out['amps'] = []
    return out

def collect():
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {host: pool.submit(fetch, host) for host in ('192.168.0.3', '192.168.0.4')}
        ai_future=pool.submit(ai_monitor.collect)
        gpu_future=pool.submit(gpu_monitor.fetch)
        downloads = {'sab':pool.submit(activity_monitor.sab_rate), 'rdt':pool.submit(activity_monitor.rdt_rate)}
        hosts = {}
        for host, future in futures.items():
            try:
                hosts[host] = future.result()
            except Exception:
                hosts[host] = None
        download_results = {}
        for name, future in downloads.items():
            try: download_results[name] = future.result()
            except Exception: download_results[name] = None
    try:gpu=gpu_future.result()
    except Exception:gpu=None
    now = int(time.time())
    gpu_monitor.collect(connect,gpu,now)
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
    activity_monitor.collect(connect, hosts, latest, now, download_results)
    rows = [(key, now, d['read_bps'], d['write_bps'], d['used_pct'], d['latency_ms']) for key,d in latest.items()]
    with connect() as db:
        db.executemany('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?)', rows)
        db.execute('DELETE FROM samples WHERE ts < ?', (now - 86400,))
        db.executemany('INSERT OR REPLACE INTO capacity_samples VALUES (?,?,?,?)',[(key,now,d['free_bytes'],d['size_bytes']) for key,d in latest.items()])
        db.execute('DELETE FROM capacity_samples WHERE ts < ?', (now - 86400,))
    with _lock:
        _latest.clear()
        _latest.update(latest)

def aggregate_history(rows, bucket, metrics=('read_bps', 'write_bps', 'used_pct', 'latency_ms')):
    """Average complete readings; explicit null points retain collection gaps."""
    groups, previous, gap_buckets, markers, first_stamps = {}, {}, set(), {}, {}
    for key, ts, *values in rows:
        first_stamps.setdefault(key, ts)
        group = (key, ts // bucket)
        groups.setdefault(group, []).append((ts, values))
        prior = previous.get(key)
        if prior is not None and ts - prior > 30:
            # Even an outage wholly inside a large bucket must remain visible.
            gap_buckets.add(group)
            points = markers.setdefault(key, [])
            for stamp in (prior + 1, ts - 1):
                points.append(dict(ts=stamp, **{metric: None for metric in metrics}))
        previous[key] = ts
    histories = {}
    for group, samples in groups.items():
        key, _ = group
        point = {'ts': samples[-1][0]}
        for index, metric in enumerate(metrics):
            values = [values[index] for _, values in samples]
            point[metric] = None if group in gap_buckets or any(value is None for value in values) else sum(values) / len(values)
        histories.setdefault(key, []).append(point)
    for key, points in histories.items():
        if points[0]['ts'] != first_stamps[key]:
            points.insert(0, dict(points[0], ts=first_stamps[key]))
    for key, points in markers.items():
        histories.setdefault(key, []).extend(points)
    for points in histories.values():
        points.sort(key=lambda point: point['ts'])
    return histories


def exhaustion_estimate(rows,now):
    """Five-minute net free-space change, independent of device write traffic."""
    out=dict(state='collecting',seconds=None,growth_bps=None,window_seconds=None)
    if not rows:return out
    last=rows[-1]
    if now-last[0]>45 or last[1] is None or not last[2]:return dict(out,state='unavailable')
    if last[1]<=16*1024**2:return dict(out,state='full',seconds=0)
    usable=[]
    for row in reversed(rows):
        if row[1] is None or row[2]!=last[2] or (usable and usable[-1][0]-row[0]>45):break
        usable.append(row)
    if len(usable)<2 or last[0]-usable[-1][0]<120:return out
    # A completed move can hide renewed filling in the long average. Use the
    # latest minute to detect direction; for growth choose the faster observed rate.
    recent=next((row for row in usable if last[0]-row[0]>=60),usable[-1])
    recent_dt=last[0]-recent[0];recent_delta=recent[1]-last[1]
    out.update(window_seconds=recent_dt,growth_bps=recent_delta/recent_dt)
    if abs(recent_delta)<64*1024**2:return dict(out,state='flat')
    if recent_delta<0:return dict(out,state='draining')
    first=usable[-1];dt=last[0]-first[0];delta=first[1]-last[1]
    if delta/dt>out['growth_bps']:out.update(window_seconds=dt,growth_bps=delta/dt)
    return dict(out,state='filling',seconds=round(last[1]/out['growth_bps']))



def snapshot(window=3600):
    window = min(86400, max(900, int(window)))
    now = int(time.time())
    with _lock:
        latest = {key: dict(value) for key, value in _latest.items()}
    with connect() as db:
        first = db.execute('SELECT MIN(ts) FROM samples').fetchone()[0]
        bucket = max(10, min(window, now-first if first else window) // 240)
        rows = db.execute('SELECT id, ts, read_bps, write_bps, used_pct, latency_ms FROM samples WHERE ts >= ? ORDER BY id, ts', (now-window,)).fetchall()
        first = db.execute('SELECT MIN(ts) FROM samples').fetchone()[0]
        capacity=db.execute('SELECT id,ts,free_bytes,size_bytes FROM capacity_samples WHERE ts>=? ORDER BY id,ts',(now-300,)).fetchall()
    estimates={key:exhaustion_estimate([row[1:] for row in capacity if row[0]==key],now) for key, *_ in DISKS}
    histories = aggregate_history(rows, bucket)
    for key, name, host, device, mount, desc in DISKS:
        latest.setdefault(key, dict(id=key, name=name, host=host, device=device, mount=mount, description=desc, online=False, sampled_at=None))
        latest[key]['exhaustion'] = estimates[key]
        latest[key]['bucket_seconds'] = bucket
        latest[key]['history'] = histories.get(key, [])
    return dict(ai=ai_monitor.snapshot(),network=net_monitor.snapshot(connect,window,bucket,aggregate_history),gpu=gpu_monitor.snapshot(connect,window,bucket,aggregate_history),activity=activity_monitor.snapshot(connect, window, bucket, aggregate_history), disks=list(latest.values()), collected_at=now, history_since=first, window=window,
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
    net_monitor.start(connect)
    ai_monitor.start()
