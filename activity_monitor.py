"""Persistent download correlation, disk saturation and recovery snapshots."""
import http.cookiejar
import json
import math
from pathlib import Path
import threading
import time
import urllib.request

METRICS = ('sab_bps', 'rdt_bps', 'read_bps', 'write_bps', 'busy_pct', 'await_ms', 'queue_depth', 'pressure_pct')
_latest, _previous = {}, {}
_lock = threading.Lock()
_opener = None
CONFIG = '/etc/homelab-dashboard/rdt.json'
TELEMETRY = 'http://127.0.0.1/api/telemetry'


def initialize(connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS activity (ts INTEGER PRIMARY KEY, '+', '.join(k+' REAL' for k in METRICS)+')')


def numeric(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def saturation(previous, current, device):
    empty = dict(busy_pct=None, await_ms=None, queue_depth=None)
    if not previous or not current or previous.get('boot_id') != current.get('boot_id'):
        return empty
    dt = current['sampled_at'] - previous['sampled_at']
    a, b = previous.get('disks', {}).get(device), current.get('disks', {}).get(device)
    if not a or not b or dt <= 0 or dt > 90:
        return empty
    keys = ('read_ops', 'write_ops', 'read_ms', 'write_ms', 'busy_ms', 'weighted_ms')
    if any(not numeric(d.get(k)) for d in (a,b) for k in keys):
        return empty
    delta = {k:b[k]-a[k] for k in keys}
    if min(delta.values()) < 0: return empty
    ops = delta['read_ops'] + delta['write_ops']
    return dict(busy_pct=min(100, delta['busy_ms'] / dt / 10),
                await_ms=(delta['read_ms']+delta['write_ms'])/ops if ops else None,
                queue_depth=delta['weighted_ms']/dt/1000)


def rdt_rate():
    global _opener
    config = json.loads(Path(CONFIG).read_text())
    if _opener is None:
        _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    url = config['url']
    try:
        result = json.load(_opener.open(url+'/api/v2/transfer/info', timeout=4))
    except urllib.error.HTTPError as exc:
        if exc.code not in (401,403): raise
        body=json.dumps({'UserName':config['username'],'Password':config['password']}).encode()
        with _opener.open(urllib.request.Request(url+'/Api/Authentication/Login', data=body, headers={'Content-Type':'application/json'}), timeout=4) as response:
            response.read()
        result = json.load(_opener.open(url+'/api/v2/transfer/info', timeout=4))
    speed = result.get('dl_info_speed')
    if not numeric(speed): raise ValueError('Invalid transfer speed')
    return speed


def sab_rate():
    with urllib.request.urlopen(TELEMETRY, timeout=3) as response:
        source = json.load(response).get('media',{}).get('sabnzbd',{})
    if source.get('online') is not True or source.get('error') or not source.get('last_success_at') or time.time()-source['last_success_at']>45:
        raise ValueError('SAB source unavailable')
    value = float(source['speed'].split()[0]) * 1024
    if not numeric(value): raise ValueError('Invalid SAB speed')
    return value


def probe(payload, now):
    try:
        amp = next(a for a in (payload or {}).get('amps',[]) if a.get('name','').lower() == 'homelab')
        value = json.loads(amp['result'])
        if not -5 <= now-value['sampled_at'] <= 45: return None
        return value
    except (ValueError, StopIteration, KeyError, TypeError): return None


def collect(connect, hosts, disks, now, download_results):
    probes = {host:probe(payload, now) for host,payload in hosts.items()}
    for disk in disks.values():
        host= disk['host'];p=probes.get(host)
        readings=saturation(_previous.get(host), p, disk['device'])
        disk.update(readings)
        disk['latency_ms']=readings['await_ms']
    scratch = disks['scratch'];p=probes.get('192.168.0.3') or {}
    pressure = (p.get('io_pressure') or {}).get('some',{}).get('avg10')
    row = dict(ts=now, sab_bps=download_results.get('sab'), rdt_bps=download_results.get('rdt'),
               read_bps=scratch['read_bps'], write_bps=scratch['write_bps'], busy_pct=scratch['busy_pct'],
               await_ms=scratch['await_ms'], queue_depth=scratch['queue_depth'], pressure_pct=pressure if numeric(pressure) else None)
    recovery = {key:(probes.get(host) or {}).get('recovery') for key,host in [('backup','192.168.0.3'),('parity','192.168.0.4')]}
    # Source observation time must travel with the result; cached success cannot look current.
    for key,value in recovery.items():
        if value and now-value.get('observed_at',0)>180: recovery[key]=None
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO activity VALUES ('+','.join('?' for _ in range(1+len(METRICS)))+')', [now]+[row[k] for k in METRICS])
        db.execute('DELETE FROM activity WHERE ts < ?', (now-86400,))
    with _lock:
        current_nightly=(probes.get('192.168.0.4') or {}).get('nightly')
        report_available=isinstance(current_nightly,dict)
        nightly=current_nightly if report_available else _latest.get('nightly')
        _latest.clear();_latest.update(dict(latest=row,recovery=recovery,nightly=nightly,nightly_source_available=report_available))
    _previous.clear();_previous.update({host:p for host,p in probes.items() if p})


def snapshot(connect, window, bucket, aggregate):
    with connect() as db:
        rows=db.execute('SELECT '+','.join(['ts']+list(METRICS))+' FROM activity WHERE ts>=? ORDER BY ts',(int(time.time())-window,)).fetchall()
        first=db.execute('SELECT MIN(ts) FROM activity').fetchone()[0]
    history=aggregate([('activity',*row) for row in rows],bucket,metrics=METRICS).get('activity',[])
    with _lock: result=dict(_latest)
    return dict(result,history=history,history_since=first,bucket_seconds=bucket)
