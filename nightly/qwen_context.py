"""Question-directed, allowlisted observations for the small A310 model.

The application selects fixed read-only sources; model text cannot choose URLs,
commands or files. Large evidence is pruned by actual tokenizer count upstream.
"""
import math
import re
from datetime import datetime, timezone
from log_snapshot import scrub
from log_monitor import service_state

CAPABILITIES = ('Available observations: host CPU/RAM, guests/containers, disk capacity and I/O history, '
                'SMART health, backup/parity status, downloads, DNS/network summaries, service reachability, '
                'model inventory/GPU, network mounts and failed systemd units inside each Proxmox container, '
                'every Unraid Docker container including stopped ones, pending updates, and bounded retained warning logs. Sources are selected by question. '
                'No arbitrary SSH, internet search, private notes, file contents, chat history, or configuration writes. '
                'General knowledge questions are allowed; distinguish them from observed local facts.')
RECORDED = {
    'as_of': '2026-10-20',
    'facts': ['Proxmox .3; Unraid .4; Homepage .2; Open WebUI .16:3000; Memos .18:5230.',
              'Desktop .12 is a gaming PC and is intentionally off sometimes; A310 is always available on Unraid.',
              'A310 Qwen3.5-4B Q4_K_M starts on demand; it is separate from Open WebUI desktop models.',
              'Cache guard checks every 5 minutes and starts mover at 70% used; stock daily mover is 03:40.',
              'Cache pool minimum-free reserve is 100 GiB, applied and checked on 2026-10-20.',
              'Home internet plan is 2.5 Gbps (user supplied); disk MB/s is not SMB or WAN throughput.',
              'Nightly review is scheduled 03:00 America/New_York, waiting for backups to finish.',
              'Proxmox nightly backup membership now includes 112 Open WebUI and 113 Memos; keep last 3.',
              'Memos LAN access intentionally grants owner access without login.',
              'FileBrowser runs natively in LXC 102 utilities (192.168.0.13:6501), not on Unraid. It serves /mnt, including /mnt/UNRAID (CIFS from the Unraid 4tb share) and /mnt/NAS.',
              'mount-heal in LXC 102 checks fstab CIFS/NFS mounts every 2 minutes and remounts stale or missing ones.',
              'Kasm 103 and stopped VMs 100, 101, 109, 110 are intentionally not backed up.',
              'The Homepage Updates page snapshots each LXC before apt upgrade and updates one guest at a time; it never reboots.',
              'Desktop router (RTX 5070 Ti, 16 GB): qwen3.6-35b-moe now 32,768 context, 54.8-63.1 tok/s; gemma-4-26b-a4b about 163 tok/s; one model loaded at a time.',
              'OpenCode CLI is installed on the gaming PC and uses the local router (default model qwen3.6-35b-moe; bash/edit actions ask first).',
              'Zurg (Real-Debrid) is disabled since 2026-10-20 after its rclone mount hung twice; Jellyfin uses local 4TB folders and TorBox only.',
              'Jellyfin is 12.2.0 at 192.168.0.17:8096 with legacy token authorization re-enabled for older clients.'],
    'notice': 'Recorded configuration, not a live configuration check.'}

KEYWORDS = {
    'storage': r'disk|cache|scratch|smb|space|full|storag|read|write|mover|slow|latency|speed|usage',
    'hosts': r'host|proxmox|unraid|cpu|ram|memory|load|container|docker|guest|vm|system',
    'services': r'service|online|offline|down|reach|memos|homepage|where|address|port|url|inventory',
    'recovery': r'\b(?:backups?|restores?|parity|raid|mover|cache)\b',
    'health': r'smart|health|temperature|hot|drive|disk|fail|error',
    'downloads': r'download|sab|rdt|torrent|usenet|slow|speed|queue|media|jellyfin|sonarr',
    'network': r'network|dns|internet|pihole|pi.hole|bandwidth|ping|speed|lan|wan',
    'ai': r'qwen|gemma|model|llm|ai\b|gpu|a310|arc|token|vram|webui',
    'logs': r'log|warn|error|fail|crash|incident|nightly|review|wrong|problem|broken',
    'mounts': r'mount|share|smb|cifs|nfs|stale|handle|filebrowser|nas\b|/mnt',
    'units': r'service|systemd|unit|fail|crash|broken|wrong|problem|down|error|health',
    'containers': r'docker|container|stopped|exited|unhealthy|running',
    'updates': r'update|upgrade|patch|package|apt\b|outdated|version|image',
}


def topics(prompt):
    selected=[name for name,pattern in KEYWORDS.items() if re.search(pattern,prompt,re.I)]
    return selected or ['hosts','storage','services']


def clean(value, depth=0):
    if depth>9:return None
    if isinstance(value,str):return scrub(value)[:400]
    if isinstance(value,bool) or value is None:return value
    if isinstance(value,(int,float)):return round(value,3) if math.isfinite(value) else None
    if isinstance(value,list):return [clean(x,depth+1) for x in value[:32]]
    if isinstance(value,dict):return {k:clean(v,depth+1) for k,v in list(value.items())[:40]}
    return None


def pick(data, fields):
    return clean({key:data.get(key) for key in fields.split()})


def observed(data, now, fields, ttl=90, stamp='last_success_at'):
    data=data or {};ts=data.get(stamp)
    fresh=(isinstance(ts,(int,float)) and 0<=now-ts<=ttl and data.get('online',True) is True and not data.get('error'))
    return dict(observed_at=ts,fresh=fresh,**(pick(data,fields) if fresh else {}))


def trend(rows,fields):
    result={'samples':len(rows),'notice':'Available samples only; gaps are not zero.'}
    times=[x['ts'] for x in rows if isinstance(x.get('ts'),(int,float))]
    result.update(first_at=min(times,default=None),last_at=max(times,default=None))
    for field in fields.split():
        vals=[r[field] for r in rows if isinstance(r.get(field),(int,float)) and math.isfinite(r[field])]
        result[field]={'samples':len(vals),'mean':round(sum(vals)/len(vals),2),'peak':round(max(vals),2)} if vals else None
    return result


def build(prompt,storage,telemetry,snapshot,now,base,services=None,fleet=None):
    selected=topics(prompt);t=telemetry or {};a=storage.get('activity') or {}
    sections={}
    sections['storage']={'source':'Homepage /api/storage',**base}
    for item,raw in zip(sections['storage']['disks'],storage.get('disks',[])):
        if item['fresh']:
            item.update(pick(raw,'queue_depth exhaustion'))
            item['history']=trend(raw.get('history',[]),'read_bps write_bps busy_pct')
    sections['hosts']={name:observed(t.get(name),now,'cpu cpu_temp ram_pct ram_used_gb ram_total_gb uptime load guests_running guests_total containers_running') for name in ('pve','unraid')}
    for name,key in [('pve','guests'),('unraid','containers')]:
        if sections['hosts'][name]['fresh']:
            sections['hosts'][name][key]=[pick(x,'name status online cpu ram_mb') for x in t[name].get(key,[])[:20]]
    sections['services']=observed(t.get('services'),now,'',ttl=90)
    if sections['services']['fresh']:
        sections['services']['checks']={k:v for k,v in t['services'].items() if isinstance(v,bool) and k!='online'}
        sections['services']['inventory']=[pick(x,'ip name role url') for x in t.get('ip_matrix',[])[:24]]
        sections['services']['notice']='TCP reachability alone does not prove application health.'
    recovery=a.get('recovery') or {}
    sections['recovery']={name:observed(recovery.get(name),now,fields,ttl=180,stamp='observed_at') for name,fields in [('backup','running latest last_success coverage'),('parity','array_state operation action last_started_at last_finished_at last_exit last_errors')]}
    sections['health']=observed(t.get('scrutiny'),now,'',ttl=180)
    if sections['health']['fresh']:sections['health']['drives']=[pick(x,'name model temp cap hours healthy') for x in t['scrutiny'].get('drives',[])[:10]]
    sections['downloads']={name:observed(data,now,'speed queue free_gb active_streams') for name,data in (t.get('media') or {}).items()}
    sections['downloads']['activity']=observed(a.get('latest'),now,'sab_bps rdt_bps read_bps write_bps busy_pct await_ms queue_depth pressure_pct',stamp='ts')
    sections['downloads']['history']=trend(a.get('history',[]),'sab_bps rdt_bps busy_pct')
    sections['network']={name:observed(t.get(name),now,fields) for name,fields in [('pihole','blocking queries blocked pct clients status'),('speedtest','dl ul ping'),('netalertx','total connected offline')]}
    sections['network']['notice']='Speedtest is last recorded test, not current throughput. DNS counters are aggregate; no query history.'
    ai=t.get('ai') or storage.get('ai') or {}
    sections['ai']={name:observed(ai.get(name),now,'version' if name=='webui' else '',ttl=90) for name in ('webui','desktop')}
    if sections['ai']['desktop']['fresh']:sections['ai']['desktop']['models']=[pick(x,'id state context_tokens context_source') for x in ai['desktop'].get('models',[])[:8]]
    sections['ai']['gpu']=observed((storage.get('gpu') or {}).get('latest'),now,'name compute_pct render_pct vram_used_mib vram_total_mib temperature_c',stamp='observed_at')
    snap=snapshot or {}
    sections['logs']=observed(snap,now,'source window_start window_end coverage_limited files_scanned matched_entries distinct_groups groups_omitted',ttl=180,stamp='generated_at')
    if sections['logs']['fresh']:
        words=set(re.findall(r'[a-z0-9_.-]{3,}',prompt.lower()))
        groups=sorted(snap.get('groups',[]),key=lambda x:sum(w in (str(x.get('host',''))+' '+str(x.get('message',''))).lower() for w in words),reverse=True)
        sections['logs']['groups']=[pick(x,'host level count first_at last_at message') for x in groups[:6]]
        live_checks=[]
        for item in sections['logs']['groups']:
            unit='filebrowser.service' if 'filebrowser.service:' in (item.get('message') or '') else None
            last=item.get('last_at')
            measured=(service_state(dict(host=item.get('host'),unit=unit,last_at=last),services or {},now)
                      if isinstance(last,(int,float)) else {'state':'unverified'})
            if unit and item.get('host')=='utilities':
                check=dict(host=item['host'],unit=unit,**measured)
                if check not in live_checks:live_checks.append(check)
            for key in ('first_at','last_at'):
                if isinstance(item.get(key),(int,float)):
                    item[key]=datetime.fromtimestamp(item[key],timezone.utc).isoformat()
        if live_checks:
            sections['live_service_checks']={'source':'Independent fixed Glances/systemd probe, not syslog',
                                            'checks':live_checks,
                                            'notice':'running_again means measured running with a start after the historical failure. It does not establish root cause.'}
            if 'logs' in selected:selected.insert(0,'live_service_checks')
        sections['logs']['groups_not_included']=max(0,snap.get('distinct_groups',len(groups))-len(groups[:6]))
        sections['logs']['notice']='Retained warning-pattern matches only; not all logs or proof of current health.'
    sections['logs']['nightly']=pick(a.get('nightly') or {},'status started_at completed_at model')
    f=fleet or {};fleet_ok=isinstance(f.get('observed_at'),(int,float)) and 0<=now-f['observed_at']<=180
    def fleet_source(key,notice):
        return {'source':'Proxmox homelab-update health probe','observed_at':f.get('observed_at'),'fresh':fleet_ok and f.get(key) is not None,'notice':notice}
    guests=f.get('guests') or []
    sections['mounts']=fleet_source('guests','Each network mount checked with a 5 s stat from inside its container. Containers with no network mounts are omitted.')
    if sections['mounts']['fresh']:
        sections['mounts']['guests']=[dict(vmid=g['vmid'],name=g['name'],mounts=g['mounts']) for g in guests if g.get('mounts')]
    sections['units']=fleet_source('guests','Failed systemd units per running LXC. Some .mount/.timer failures are normal in unprivileged containers and do not mean the app is down.')
    if sections['units']['fresh']:
        sections['units']['guests']=[dict(vmid=g['vmid'],name=g['name'],status=g['status'],failed_units=g.get('failed_units'),error=g.get('error')) for g in guests]
    sections['containers']=fleet_source('containers','All Unraid Docker containers, including stopped ones.')
    if sections['containers']['fresh']:sections['containers']['unraid']=f['containers'][:30]
    sections['updates']=fleet_source('updates','From the last Updates-page check; not a live package query.')
    if sections['updates']['fresh']:sections['updates'].update(f['updates'])
    # Specific questions get their relevant sections first; broad checks get
    # hosts/storage/services. Every omitted section is explicitly named.
    evidence={'observed_at':int(now),'recorded_configuration':RECORDED,
              'sources':{key:sections[key] for key in selected},
              'not_selected':[key for key in sections if key not in selected]}
    return clean(evidence)


def reduce_evidence(evidence):
    """Drop detail before dropping a complete source; record every omission."""
    sources=evidence['sources']
    for name in reversed(list(sources)):
        def prune(obj):
            if isinstance(obj,list):
                return any(prune(value) for value in obj)
            if not isinstance(obj,dict):return False
            for key in ('history','inventory','groups','guests','containers'):
                if key in obj:
                    if key=='groups' and len(obj[key])>1:
                        length=len(obj[key]);obj[key]=obj[key][:max(1,length//2)]
                        obj['groups_not_included']=obj.get('groups_not_included',0)+length-len(obj[key])
                        return True
                    del obj[key];return True
            return any(prune(value) for value in obj.values() if isinstance(value,(dict,list)))
        if prune(sources[name]):
            evidence.setdefault('budget_omissions',[]).append(name+' detail')
            return True
    if len(sources)>1:
        name=next(reversed(sources));del sources[name]
        evidence.setdefault('budget_omissions',[]).append(name)
        return True
    if 'recorded_configuration' in evidence:
        del evidence['recorded_configuration'];evidence.setdefault('budget_omissions',[]).append('recorded configuration');return True
    return False
