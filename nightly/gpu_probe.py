"""Bounded A310 reads; no GPU workload is started by this monitor."""
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import time

PCI='0000:03:00.0'
ENGINES={'compute_pct':'Compute','render_pct':'Render/3D','video_pct':'Video','copy_pct':'Blitter','enhance_pct':'VideoEnhance'}
_lock=threading.Lock()
_latest={}


def number(value,maximum=None):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 and (maximum is None or value<=maximum) else None


def samples(text):
    # This installed intel_gpu_top emits adjacent objects without array commas.
    if len(text)>2*1024*1024:raise ValueError('GPU output exceeds limit')
    decoder=json.JSONDecoder();out=[];offset=0
    while offset<len(text):
        while offset<len(text) and text[offset] in ' \r\n\t[],':offset+=1
        if offset>=len(text):break
        item,end=decoder.raw_decode(text,offset);offset=end
        if isinstance(item,dict):out.append(item)
    if not out:raise ValueError('No GPU samples')
    return out


def engine_reading(text):
    last=samples(text)[-1]
    duration=number(last.get('period',{}).get('duration'))
    if duration is None or duration<500:raise ValueError('GPU sample too short')
    result={key:number(last.get('engines',{}).get(name,{}).get('busy'),100) for key,name in ENGINES.items()}
    result['frequency_mhz']=number(last.get('frequency',{}).get('actual'))
    result['sample_seconds']=duration/1000
    return result


def memory_reading(text):
    local=text.split('local0:',1)
    if len(local)!=2:return {}
    match=re.search(r'chunk_size:.*?total:\s*(\d+)MiB,\s*free:\s*(\d+)MiB',local[1])
    if not match:return {}
    total,free=map(int,match.groups())
    if not total or not 0<=free<=total:return {}
    return dict(vram_total_mib=total,vram_used_mib=total-free)


def read():
    result=dict(online=False,observed_at=int(time.time()),name='Intel Arc A310',pci=PCI)
    device=Path('/sys/bus/pci/devices')/PCI
    if device.joinpath('vendor').read_text().strip()!='0x8086' or device.joinpath('device').read_text().strip()!='0x56a6':
        return result
    try:
        raw=subprocess.check_output(['intel_gpu_top','-d','pci:slot='+PCI,'-J','-s','1000','-n','2','-o','-'],timeout=8,text=True,stderr=subprocess.DEVNULL)
        result.update(engine_reading(raw))
    except (OSError,ValueError,subprocess.SubprocessError):pass
    cards=[p.name for p in (device/'drm').glob('card*') if re.fullmatch(r'card\d+',p.name)]
    if len(cards)==1:
        try:result.update(memory_reading((Path('/sys/kernel/debug/dri')/cards[0][4:]/'i915_gem_objects').read_text()[:65536]))
        except OSError:pass
    for hw in (device/'hwmon').glob('hwmon*'):
        for filename,key,divisor,maximum in [('temp1_input','temperature_c',1000,150),('fan1_input','fan_rpm',1,30000)]:
            try:result[key]=number(float((hw/filename).read_text())/divisor,maximum)
            except (OSError,ValueError):pass
        break
    result['online']=any(result.get(k) is not None for k in (*ENGINES,'vram_used_mib','temperature_c'))
    result['observed_at']=int(time.time())
    return result


def snapshot():
    with _lock:return dict(_latest)


def start():
    def loop():
        while True:
            started=time.monotonic()
            try:value=read()
            except Exception:value=dict(online=False,observed_at=int(time.time()),name='Intel Arc A310',pci=PCI)
            with _lock:
                _latest.clear();_latest.update(value)
            time.sleep(max(1,10-(time.monotonic()-started)))
    threading.Thread(target=loop,name='arc-monitor',daemon=True).start()
