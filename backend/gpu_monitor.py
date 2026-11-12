"""Persist authenticated A310 readings alongside dashboard disk history."""
import json
from pathlib import Path
import threading
import time
import urllib.request
import energy_monitor
from activity_monitor import numeric

METRICS=('compute_pct','render_pct','video_pct','copy_pct','enhance_pct','vram_used_mib','vram_total_mib','temperature_c','fan_rpm','frequency_mhz')
# Gaming PC GPU (pc/gpu_metrics.py on .12): the fields the collector already fetches every 10 s for the energy log.
# vram_total_mib and power_limit_w stay constants in _pc_latest; the chart uses them as max values.
PC_METRICS=('power_w','temperature_c','utilization_pct','memory_util_pct','vram_used_mib','clock_mhz','fan_pct','cpu_w')
_lock=threading.Lock()
_latest={}
_pc_latest={}


def initialize(connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS gpu_samples (ts INTEGER PRIMARY KEY, '+', '.join(k+' REAL' for k in METRICS)+')')
        db.execute('CREATE TABLE IF NOT EXISTS pc_gpu_samples (ts INTEGER PRIMARY KEY, '+', '.join(k+' REAL' for k in PC_METRICS)+')')
        # Part 2 added cpu_w to a table that may already exist from a Part 1-only deploy:
        if 'cpu_w' not in [r[1] for r in db.execute('PRAGMA table_info(pc_gpu_samples)')]:
            db.execute('ALTER TABLE pc_gpu_samples ADD COLUMN cpu_w REAL')


def fetch():
    config=json.loads(Path('/etc/homelab-dashboard/qwen.json').read_text())
    request=urllib.request.Request('http://192.168.0.4:11438/gpu',headers={'Authorization':'Bearer '+config['token']})
    with urllib.request.urlopen(request,timeout=3) as response:raw=response.read(32769)
    if len(raw)>32768:raise ValueError('GPU response too large')
    return json.loads(raw)


def normalized(value,now):
    value=value if isinstance(value,dict) else {}
    stamp=value.get('observed_at')
    fresh=value.get('online') is True and numeric(stamp) and 0<=now-stamp<=30 and value.get('pci')=='0000:03:00.0'
    out=dict(online=bool(fresh),observed_at=stamp,name='Intel Arc A310',pci='0000:03:00.0',ts=now)
    for key in METRICS:
        x=value.get(key)
        out[key]=x if fresh and numeric(x) and (not key.endswith('_pct') or x<=100) else None
    out['sample_seconds']=value.get('sample_seconds') if fresh else None
    return out


def collect(connect,value,now):
    row=normalized(value,now)
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO gpu_samples VALUES ('+','.join('?' for _ in range(len(METRICS)+1))+')',[now]+[row[k] for k in METRICS])
        db.execute('DELETE FROM gpu_samples WHERE ts<?',(now-86400,))
    with _lock:
        _latest.clear();_latest.update(row)


def snapshot(connect,window,bucket,aggregate):
    with connect() as db:
        rows=db.execute('SELECT '+','.join(['ts']+list(METRICS))+' FROM gpu_samples WHERE ts>=? ORDER BY ts',(int(time.time())-window,)).fetchall()
        first=db.execute('SELECT MIN(ts) FROM gpu_samples').fetchone()[0]
    history=aggregate([('arc',*row) for row in rows],bucket,metrics=METRICS).get('arc',[])
    with _lock:latest=dict(_latest)
    return dict(latest=latest,history=history,history_since=first,bucket_seconds=bucket)


def pc_valid(key,x,max_power=1000):
    """One /gpu field, or None: numeric(), no _pct over 100, no power_w over the plausible bound, no cpu_w over 300.
    A bad field never rejects the whole row."""
    if not numeric(x):
        return None
    if key.endswith('_pct') and x > 100:
        return None
    if key == 'power_w' and x > max_power:
        return None
    if key == 'cpu_w' and x > 300:
        return None
    return x


def pc_collect(connect,value,now):
    """Keep the gaming PC GPU reading the collector already has (energy_monitor.fetch_pc's JSON). Asleep/off PC (None,
    error or non-dict): store nothing, so the chart shows a gap, never zeros. cpu_w is the CPU watts energy_monitor
    derives from the same cpu_energy_j difference (computed once per tick, shared via its memo)."""
    if not isinstance(value, dict) or value.get('error'):
        return
    cpu_w=energy_monitor.pc_cpu_watts(value,now)
    row=[now]+[(cpu_w if k=='cpu_w' else pc_valid(k,value.get(k))) for k in PC_METRICS]
    if all(x is None for x in row[1:]):  # no usable metric at all: keep the gap, write nothing
        return
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO pc_gpu_samples VALUES ('+','.join('?' for _ in range(len(PC_METRICS)+1))+')',row)
        db.execute('DELETE FROM pc_gpu_samples WHERE ts<?',(now-86400,))
    with _lock:
        _pc_latest.clear()
        _pc_latest.update({k:row[i+1] for i,k in enumerate(PC_METRICS)})
        for k in ('vram_total_mib','power_limit_w','name','observed_at'):
            x=value.get(k)
            if numeric(x) or isinstance(x,str): _pc_latest[k]=x


def pc_snapshot(connect,window,bucket,aggregate):
    with connect() as db:
        cols=['ts']+list(PC_METRICS)
        rows=db.execute('SELECT '+','.join(cols)+' FROM pc_gpu_samples WHERE ts>=? ORDER BY ts',(int(time.time())-window,)).fetchall()
        first=db.execute('SELECT MIN(ts) FROM pc_gpu_samples').fetchone()[0]
    history=aggregate([tuple(['pc',*row]) for row in rows],bucket,metrics=PC_METRICS).get('pc',[])
    with _lock:latest=dict(_pc_latest)
    return dict(latest=latest,history=history,history_since=first,bucket_seconds=bucket)
