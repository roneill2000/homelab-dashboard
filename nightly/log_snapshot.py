"""Bounded read of retained syslog files for a local nightly review."""
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
import statistics
import time
from pathlib import Path

MAX_FILES=32
MAX_BYTES=16*1024*1024
MAX_GROUPS=80
# Normalization folds varying identifiers into stable placeholders so one noisy source is one group, not 30.
# Small tokens that known causes match on (exit statuses, wlan0, sdc1) are deliberately kept.
BASELINE_PATH='/var/lib/syslog-monitor/baseline.json'
BASELINE_MAX_KEYS=5000
BASELINE_MAX_BYTES=2*1000*1000
BASELINE_NIGHTS=30
VARIANT_EXAMPLES=3
MONTHS=r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
_TS_ISO=re.compile(r'\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?')
_TS_CTIME=re.compile(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) '+MONTHS+r' \d{1,2} \d{2}:\d{2}:\d{2}(?:\.\d+)? (?:UTC |GMT )?\d{4}')
_TS_SYSLOG=re.compile(r'\b'+MONTHS+r' +\d{1,2} \d{2}:\d{2}:\d{2}(?: \d{4})?\b')
_MAC=re.compile(r'\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b')
_UUID=re.compile(r'\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b')
_IPV6=re.compile(r'(?<![0-9A-Za-z:])(?=[0-9A-Fa-f:.]*(?:[a-fA-F]|[0-9A-Fa-f]{3}))((?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:)*:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)(?![0-9A-Za-z:])')
_LAN=re.compile(r'\b192\.168\.0\.\d{1,3}\b')
_IPV4=re.compile(r'(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])')
_HEX=re.compile(r'(?<![0-9A-Za-z])(?=[0-9A-Fa-f]*[a-fA-F])[0-9A-Fa-f]{8,}(?![0-9A-Za-z])')
_UNIT=re.compile(r'(?<![0-9A-Za-z.])(\d+(?:\.\d+)?)(ms|min|us|ns|GB|GiB|TB|TiB|MB|MiB|KB|KiB|kB|mb|kb|b|B|%|s|h|m|d)(?![0-9A-Za-z])')
_NUMBER=re.compile(r'(?<![0-9A-Za-z=.])\d{3,}(?:\.\d+)?(?![0-9A-Za-z])')
# Cross-host lines: what other hosts logged within a minute of a group's latest line (a router reboot
# explains a dozen downstream lines). Only candidate-worthy lines are kept: matched, boot, link, or service start/stop.
BOOT_RE=re.compile(r'Booting Linux|kernel: \[\s*\d+\.\d+\]|procd: - init -|Startup finished')
LINK_RE=re.compile(r'(?i)\b(?:link is down|link is up|link established|link lost|carrier lost|link up\b|link down\b)')
SOAK_RE=re.compile(r'(?i)\b(?:Started|Stopping|Stopped)\b')
NEARBY_WINDOW=60
NEARBY_PER_GROUP=4
NEARBY_MAX_PER_FILE=2000  # Started/Stopped chatter is capped; matched/boot/link lines always pass


def normalize(message):
    """Collapse varying identifiers after scrub() and before the group key.
    192.168.0.x keeps its last octet (which LAN host it was matters)."""
    text=_TS_CTIME.sub('<ts>',message)
    text=_TS_ISO.sub('<ts>',text)
    text=_TS_SYSLOG.sub('<ts>',text)
    text=_MAC.sub('<mac>',text)
    text=_UUID.sub('<uuid>',text)
    text=_IPV6.sub('<ip>',text)
    text=_LAN.sub(lambda m:'<lan:'+m.group(0)[9:]+'>',text)
    text=_IPV4.sub('<ip>',text)
    text=_HEX.sub('<hex>',text)
    text=_UNIT.sub(lambda m:'<n>'+m.group(2),text)
    text=_NUMBER.sub('<n>',text)
    return text


def scrub(text):
    # Logs can embed JSON, quoted values, or credential-bearing URLs. Keep
    # surrounding text useful without retaining those common secret forms.
    quoted=r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    def hide(match):
        value=match.group(2)
        quote=value[0] if value[:1] in ('"', "'") else ''
        return match.group(1)+quote+'[redacted]'+quote
    text=re.sub(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s?#]+@',r'\1[redacted]@',text)
    text=re.sub(r'(?i)(["\']?\bauthorization["\']?\s*[:=]\s*)('
                +quoted+r'|(?:bearer|basic)\s+[^\s,;]+|[^\s,;]+)',hide,text)
    text=re.sub(r'(?i)(["\']?\b(?:password|passwd|(?:access[_-]?|refresh[_-]?)?token|api[_-]?key|secret)["\']?\s*[:=]\s*)('
                +quoted+r'|[^\s,;]+)',hide,text)
    return text


def _read_stream(p, rot_start, prev_len, start, raw_len):
    rotated=p.with_name(p.name+'.1');prev=b'';raw=b''
    if prev_len and rotated.is_file() and not rotated.is_symlink():
        with rotated.open('rb') as f:
            f.seek(rot_start)
            if rot_start:f.readline()
            prev=f.read(prev_len)
    with p.open('rb') as f:
        f.seek(start)
        if start:f.readline()
        raw=f.read(raw_len)
    return (prev+(b'\n' if prev and not prev.endswith(b'\n') else b'')+raw).decode('utf-8','replace').splitlines()


def _baseline_key(host,level,normalized):return json.dumps([host,level,normalized])


def load_baseline(path):
    if not path:return {}
    try:
        data=json.loads(Path(path).read_text())
        keys=data.get('keys') if isinstance(data,dict) else None
        return keys if isinstance(keys,dict) else {}
    except (OSError,ValueError):return {}


def _baseline_stats(entry):
    dates=[d for d in (entry.get('dates') or []) if isinstance(d,str)]
    counts=[c for c in (entry.get('counts') or []) if isinstance(c,(int,float)) and not isinstance(c,bool)]
    return dates,counts


def update_baseline(baseline,groups,today):
    """Fold this night's groups into the baseline, bound it to 30 nights, 5,000 keys and 2 MB."""
    oldest=(datetime.strptime(today,'%Y-%m-%d')-timedelta(days=BASELINE_NIGHTS-1)).strftime('%Y-%m-%d')
    for g in groups.values():
        k=_baseline_key(g['host'],g['level'],g['normalized'])
        entry=baseline.get(k)
        if not isinstance(entry,dict):entry=dict(first_seen=today,last_seen=today,dates=[],counts=[]);baseline[k]=entry
        dates,counts=_baseline_stats(entry)
        if today in dates:counts[dates.index(today)]=max(counts[dates.index(today)],g['count'])
        else:dates.append(today);counts.append(g['count'])
        keep=[i for i,d in enumerate(dates) if d>=oldest]
        entry['dates']=[dates[i] for i in keep];entry['counts']=[counts[i] for i in keep]
        entry['first_seen']=min(str(entry.get('first_seen') or today),today);entry['last_seen']=today
    if len(baseline)>BASELINE_MAX_KEYS:
        for k in sorted(baseline,key=lambda k:str(baseline[k].get('last_seen') or ''))[:len(baseline)-BASELINE_MAX_KEYS]:baseline.pop(k)
    while baseline and len(json.dumps(dict(updated_at=today,keys=baseline)))>BASELINE_MAX_BYTES:
        baseline.pop(min(baseline,key=lambda k:str(baseline[k].get('last_seen') or '')))
    return baseline


def write_baseline(baseline,path,now):
    if not path:return
    try:
        target=Path(path)
        if not target.parent.is_dir():return  # the baseline lives only where syslog-monitor runs (added by install_snapshot)
        tmp=target.with_name(target.name+'.tmp')
        tmp.write_text(json.dumps(dict(updated_at=int(now),keys=baseline)))
        os.replace(tmp,target)
    except OSError:pass  # a baseline failure must not take down the snapshot endpoint


def snapshot(directory, critical, warning, now=None, baseline_path=BASELINE_PATH):
    now=now or time.time();cutoff=now-86400
    today=datetime.fromtimestamp(now).strftime('%Y-%m-%d')
    oldest_night=(datetime.strptime(today,'%Y-%m-%d')-timedelta(days=BASELINE_NIGHTS)).strftime('%Y-%m-%d')
    baseline=load_baseline(baseline_path)
    files=sorted(p for p in directory.glob('*.log') if not p.is_symlink())
    groups={};coverage=[];matched=0;file_spans={}
    candidates=[]  # (ts, host, scrubbed line) across all files, for the nearby pass below
    for p in files[:MAX_FILES]:
        size=p.stat().st_size;start=max(0,size-MAX_BYTES);parsed=[];unknown=0;lines=0;error=False;soaked=0
        # Weekly rotation leaves the start of the window in <name>.log.1 (uncompressed: delaycompress), so read it
        # too while it was still being written inside the window.
        rotated=p.with_name(p.name+'.1');prev=b'';rot_start=0
        try:
            if rotated.is_file() and not rotated.is_symlink() and rotated.stat().st_mtime>=cutoff:
                rsize=rotated.stat().st_size;rot_start=max(0,rsize-MAX_BYTES)
                with rotated.open('rb') as f:
                    f.seek(rot_start)
                    if rot_start:f.readline()
                    prev=f.read(MAX_BYTES)
        except OSError:error=True
        try:
            with p.open('rb') as f:
                f.seek(start)
                if start:f.readline()
                raw=f.read(MAX_BYTES)
            file_spans[p]=(rot_start,len(prev),start,len(raw))
            for idx,line in enumerate((prev+(b'\n' if prev and not prev.endswith(b'\n') else b'')+raw).decode('utf-8','replace').splitlines()):
                lines+=1
                try:ts=datetime.fromisoformat(line.split()[0].replace('Z','+00:00')).timestamp()
                except (ValueError,IndexError):unknown+=1;continue
                parsed.append(ts)
                if ts<cutoff or ts>now+60:continue
                level='CRITICAL' if any(r.search(line) for r in critical) else 'WARNING' if any(r.search(line) for r in warning) else None
                if level or BOOT_RE.search(line) or LINK_RE.search(line) or (soaked<NEARBY_MAX_PER_FILE and SOAK_RE.search(line)):
                    if level is None and SOAK_RE.search(line):soaked+=1
                    candidates.append((ts,p.stem,scrub(line)[:300]))
                if not level:continue
                matched+=1
                clean=scrub(line)
                message=re.sub(r'^\S+\s+', '',clean)
                if message.startswith(p.stem+' '):message=message[len(p.stem)+1:]
                message=re.sub(r'\b([\w.-]+)\[\d+\]:',r'\1:',message)
                normalized=normalize(message)
                key=(p.stem,level,normalized)
                if key not in groups:
                    # Bound distinct group storage; coverage records overflow below.
                    if len(groups)>=2000:continue
                    groups[key]=dict(id=hashlib.sha256(repr(key).encode()).hexdigest()[:12],host=p.stem,level=level,
                                     normalized=normalized,message=message[:1200],count=0,first_at=ts,last_at=ts,hours=[0]*24,
                                     variants=dict(count=0,examples=[]),_seen=set(),_ctx=(p,idx))
                g=groups[key];g['count']+=1;g['first_at']=min(g['first_at'],ts)
                if ts>=g['last_at']:g['last_at']=ts;g['_ctx']=(p,idx);g['message']=message[:1200]
                if message not in g['_seen'] and len(g['_seen'])<1000:
                    g['_seen'].add(message);g['variants']['count']+=1
                    if len(g['variants']['examples'])<VARIANT_EXAMPLES:g['variants']['examples'].append(message[:1200])
                g['hours'][datetime.fromtimestamp(ts).hour]+=1
        except OSError:error=True
        # A file with no lines at all is a quiet source (nothing logged), not a gap in coverage.
        quiet=not error and lines==0
        coverage.append(dict(host=p.stem,bytes_read=min(size,MAX_BYTES)+len(prev),lines=lines,unknown_timestamps=unknown,quiet=quiet,
                             rotated_read=bool(prev),truncated=start>0 or rot_start>0,
                             window_may_be_incomplete=not quiet and (error or unknown>0 or not parsed or min(parsed)>cutoff),
                             oldest_read_at=min(parsed,default=None),newest_read_at=max(parsed,default=None),read_error=error))
    # Novelty against the baseline: a never-seen line first, then a spike over its usual nightly count,
    # then how long the burst lasted; a line seen most nights at its usual count ends up last.
    for g in groups.values():
        entry=baseline.get(_baseline_key(g['host'],g['level'],g['normalized'])) or {}
        dates,counts=_baseline_stats(entry)
        g['nights_seen_30']=sum(1 for d in dates if d>=oldest_night)
        g['baseline_count']=statistics.median(counts) if counts else 0
        g['first_seen']=entry.get('first_seen')
        g['novelty']=(3 if g['nights_seen_30']==0 else 0)+(2 if g['count']>3*g['baseline_count'] else 0)+sum(1 for h in g['hours'] if h>=6)
    update_baseline(baseline,groups,today)
    write_baseline(baseline,baseline_path,now)
    for g in groups.values():g.pop('_seen',None)
    ordered=sorted(groups.values(),key=lambda g:(g['level']=='CRITICAL',g['novelty'],g['last_at']),reverse=True)
    top=ordered[:MAX_GROUPS]
    by_file={}
    for g in top:
        src,idx=g.pop('_ctx');by_file.setdefault(src,[]).append((g,idx))
    # Nearby lines for the kept groups: up to 4 lines from other hosts within ±60 s of the group's latest line.
    for g in top:
        near=sorted((abs(ts-g['last_at']),line) for ts,host,line in candidates if host!=g['host'] and abs(ts-g['last_at'])<=NEARBY_WINDOW)
        g['nearby']=[line for _,line in near[:NEARBY_PER_GROUP]]
    for g in ordered[MAX_GROUPS:]:g.pop('_ctx',None)
    for src,items in by_file.items():
        try:flines=_read_stream(src,*file_spans[src])
        except OSError:flines=[]
        for g,idx in items:
            g['context']=[scrub(l)[:300] for l in flines[max(0,idx-8):idx+5]] if flines else []
    represented=sum(g['count'] for g in ordered)
    return dict(generated_at=int(now),window_start=int(cutoff),window_end=int(now),hosts=coverage,
                coverage_limited=len(files)>MAX_FILES or not files or any(h['window_may_be_incomplete'] for h in coverage) or matched>represented,
                files_available=len(files),files_scanned=len(coverage),matched_entries=matched,distinct_groups=len(groups),
                groups=top,groups_omitted=max(0,len(groups)-MAX_GROUPS),
                source='Retained syslog files; existing monitor warning/critical patterns',
                limits=dict(bytes_per_file=MAX_BYTES,files=MAX_FILES,groups=MAX_GROUPS,baseline_keys=len(baseline)))
