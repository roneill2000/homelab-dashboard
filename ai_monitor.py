"""Read-only local AI service and llama.cpp router inventory; no model loading."""
import concurrent.futures
import copy
import json
from pathlib import Path
import threading
import time
import urllib.request

CONFIG=Path('/etc/homelab-dashboard/ai.json')
_lock=threading.Lock()
_latest={}
_last_poll=0


def fetch(url,token=None):
    headers={'Authorization':'Bearer '+token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=4) as response:
        raw=response.read(262145)
    if len(raw)>262144:raise ValueError('AI response exceeds limit')
    return json.loads(raw)


def positive(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and 0<value<=1000000000 else None


def models(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):raise ValueError('Invalid model inventory')
    out=[];seen=set()
    for entry in payload['data'][:64]:
        if not isinstance(entry,dict):continue
        name=entry.get('id')
        if not isinstance(name,str) or not name or len(name)>200 or name in seen:continue
        seen.add(name)
        status=entry.get('status') or {};status=status if isinstance(status,dict) else {}
        state=status.get('value');state=state if state in ('loaded','unloaded','loading','unloading','sleeping','failed') else 'unknown'
        args=status.get('args') or [];configured=None
        if isinstance(args,list):
            for i,arg in enumerate(args[:-1]):
                if arg in ('--ctx-size','-c'):
                    try:configured=positive(int(args[i+1]))
                    except (ValueError,TypeError):pass
        meta=entry.get('meta') or {};meta=meta if isinstance(meta,dict) else {}
        # context_length is the model's training limit, not the active server allocation.
        context=positive(meta.get('n_ctx')) if state=='loaded' else None
        out.append(dict(id=name,state=state,context_tokens=context or configured,
                        context_source='runtime' if context else 'configured' if configured else 'unknown'))
    if payload['data'] and not out:raise ValueError('No valid model records')
    return out


def webui():
    result=fetch('http://192.168.0.16:3000/api/config')
    if result.get('name')!='Open WebUI' or result.get('status') is not True:raise ValueError('Unexpected WebUI response')
    return dict(version=str(result.get('version',''))[:40])


def desktop():
    config=json.loads(CONFIG.read_text())
    if config.get('url')!='http://192.168.0.12:8080/v1':raise ValueError('Unexpected model endpoint')
    return dict(models=models(fetch(config['url']+'/models',config['token'])))


def collect(now=None):
    global _last_poll
    now=now or time.time()
    with _lock:
        if now-_last_poll<30:return
        _last_poll=now
        prior=copy.deepcopy(_latest)
    result={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures={k:pool.submit(fn) for k,fn in [('webui',webui),('desktop',desktop)]}
        for key,future in futures.items():
            old=prior.get(key,{})
            try:
                value=future.result()
                result[key]=dict(value,online=True,last_attempt_at=int(now),last_success_at=int(now),error=None)
            except Exception:
                result[key]=dict(old,online=False,last_attempt_at=int(now),error='Source unavailable')
    with _lock:
        _latest.clear();_latest.update(result)


def snapshot():
    with _lock:return copy.deepcopy(_latest)
