"""Bounded read of retained syslog files for a local nightly review."""
from datetime import datetime
import hashlib
import re
import time

MAX_FILES=32
MAX_BYTES=16*1024*1024
MAX_GROUPS=80


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


def snapshot(directory, critical, warning, now=None):
    now=now or time.time();cutoff=now-86400
    files=sorted(p for p in directory.glob('*.log') if not p.is_symlink())
    groups={};coverage=[];matched=0
    for p in files[:MAX_FILES]:
        size=p.stat().st_size;start=max(0,size-MAX_BYTES);parsed=[];unknown=0;lines=0;error=False
        try:
            with p.open('rb') as f:
                f.seek(start)
                if start:f.readline()
                raw=f.read(MAX_BYTES)
            for line in raw.decode('utf-8','replace').splitlines():
                lines+=1
                try:ts=datetime.fromisoformat(line.split()[0].replace('Z','+00:00')).timestamp()
                except (ValueError,IndexError):unknown+=1;continue
                parsed.append(ts)
                if ts<cutoff or ts>now+60:continue
                level='CRITICAL' if any(r.search(line) for r in critical) else 'WARNING' if any(r.search(line) for r in warning) else None
                if not level:continue
                matched+=1
                clean=scrub(line)
                message=re.sub(r'^\S+\s+', '',clean)
                if message.startswith(p.stem+' '):message=message[len(p.stem)+1:]
                message=re.sub(r'\b([\w.-]+)\[\d+\]:',r'\1:',message)
                key=(p.stem,level,message)
                if key not in groups:
                    # Bound distinct group storage; coverage records overflow below.
                    if len(groups)>=2000:continue
                    groups[key]=dict(id=hashlib.sha256(repr(key).encode()).hexdigest()[:12],host=p.stem,level=level,
                                     message=message[:1200],count=0,first_at=ts,last_at=ts)
                g=groups[key];g['count']+=1;g['first_at']=min(g['first_at'],ts);g['last_at']=max(g['last_at'],ts)
        except OSError:error=True
        coverage.append(dict(host=p.stem,bytes_read=min(size,MAX_BYTES),lines=lines,unknown_timestamps=unknown,
                             truncated=start>0,window_may_be_incomplete=error or unknown>0 or not parsed or min(parsed)>cutoff,
                             oldest_read_at=min(parsed,default=None),newest_read_at=max(parsed,default=None),read_error=error))
    ordered=sorted(groups.values(),key=lambda g:(g['level']=='CRITICAL',g['last_at']),reverse=True)
    represented=sum(g['count'] for g in ordered)
    return dict(generated_at=int(now),window_start=int(cutoff),window_end=int(now),hosts=coverage,
                coverage_limited=len(files)>MAX_FILES or not files or any(h['window_may_be_incomplete'] for h in coverage) or matched>represented,
                files_available=len(files),files_scanned=len(coverage),matched_entries=matched,distinct_groups=len(groups),
                groups=ordered[:MAX_GROUPS],groups_omitted=max(0,len(groups)-MAX_GROUPS),
                source='Retained syslog files; existing monitor warning/critical patterns',
                limits=dict(bytes_per_file=MAX_BYTES,files=MAX_FILES,groups=MAX_GROUPS))
