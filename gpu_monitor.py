"""Persist authenticated A310 readings alongside dashboard disk history."""
import json
from pathlib import Path
import threading
import time
import urllib.request
from activity_monitor import numeric

METRICS=('compute_pct','render_pct','video_pct','copy_pct','enhance_pct','vram_used_mib','vram_total_mib','temperature_c','fan_rpm','frequency_mhz')
_lock=threading.Lock()
_latest={}


def initialize(connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS gpu_samples (ts INTEGER PRIMARY KEY, '+', '.join(k+' REAL' for k in METRICS)+')')


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
