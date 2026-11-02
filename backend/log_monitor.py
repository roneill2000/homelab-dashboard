"""Group a bounded recent alert window and attach explicitly mapped service state."""
import hashlib
import json
import math
import re
import time
import urllib.request


def read_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        raw=response.read(262145)
    if len(raw)>262144: raise ValueError('Response exceeds limit')
    return json.loads(raw)


def normalized(event):
    text=event['msg']
    # Remove only transport timestamp/host and process PID; retain failure codes and paths.
    text=re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+', '', text)
    host=event.get('host','')
    if host and text.startswith(host+' '): text=text[len(host)+1:]
    return re.sub(r'\b([\w.-]+)\[\d+\]:',r'\1:',text)


def group_events(events):
    groups={}
    valid=[e for e in events[:100] if isinstance(e,dict) and isinstance(e.get('msg'),str)
           and isinstance(e.get('ts'),(int,float)) and math.isfinite(e['ts']) and e['ts']>0
           and isinstance(e.get('host'),str) and isinstance(e.get('level'),str)]
    for e in sorted(valid,key=lambda e:e['ts']):
        text=normalized(e);key=(e['host'],e['level'],text)
        if key not in groups:
            service=re.search(r'(?:^|\s)([\w@.-]+\.service):',text)
            groups[key]=dict(id=hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16],host=e['host'],level=e['level'],
                             summary=text,unit=service.group(1) if service else None,count=0,first_at=e['ts'])
        groups[key].update(count=groups[key]['count']+1,last_at=e['ts'],message=e['msg'])
    result=sorted(groups.values(),key=lambda g:g['last_at'],reverse=True)
    return result,valid


def service_state(group, services, now):
    # Explicit verified mapping only; arbitrary log strings cannot trigger commands or requests.
    if (group['host'],group['unit']) != ('utilities','filebrowser.service'):
        return dict(state='unverified')
    value=services.get('utilities/filebrowser.service') or {}
    observed=value.get('observed_at')
    if not isinstance(observed,(int,float)) or not -5<=now-observed<=90 or value.get('load')!='loaded':
        return dict(state='unverified')
    active,sub=value.get('active'),value.get('sub');since=value.get('active_since')
    state='failed' if active=='failed' else 'restarting' if sub=='auto-restart' else 'stopped' if active=='inactive' else 'unverified'
    if active=='active' and sub=='running':
        state='running_again' if isinstance(since,(int,float)) and group['last_at']<since<=observed else 'running'
    return dict(state=state,checked_at=observed,active_since=since,active=active,sub=sub)


def details(status):
    result=dict(status,latest_event=None,last_alert_full=False,log_groups=None,log_window=None)
    try:
        events=read_json('http://192.168.0.3:8999/api/alerts')
        if not isinstance(events,list):return result
        groups,valid=group_events(events)
        services={}
        if any((g['host'],g['unit'])==('utilities','filebrowser.service') for g in groups):
            try:
                amps=read_json('http://192.168.0.3:61208/api/4/amps')
                probe=json.loads(next(a['result'] for a in amps if a.get('name','').lower()=='homelab'))
                if -5<=time.time()-probe.get('sampled_at',0)<=45: services=probe.get('services',{})
            except Exception: pass
        for group in groups:group['service_status']=service_state(group,services,time.time())
        result.update(log_groups=groups,log_window=dict(returned=len(events),valid=len(valid),groups=len(groups),
                      first_at=min((e['ts'] for e in valid),default=None),last_at=max((e['ts'] for e in valid),default=None),
                      observed_at=int(time.time())))
        if valid:
            event=max(valid,key=lambda e:e['ts'])
            result.update(last_alert=event['msg'],last_alert_full=True,latest_event={k:event.get(k) for k in ('msg','host','level','ts')})
    except Exception:pass
    return result
