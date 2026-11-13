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


def snapshot(directory, critical, warning, now=None):
    now=now or time.time();cutoff=now-86400
    files=sorted(p for p in directory.glob('*.log') if not p.is_symlink())
    groups={};coverage=[];matched=0;file_spans={}
    for p in files[:MAX_FILES]:
        size=p.stat().st_size;start=max(0,size-MAX_BYTES);parsed=[];unknown=0;lines=0;error=False
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
                                     message=message[:1200],count=0,first_at=ts,last_at=ts,hours=[0]*24,_ctx=(p,idx))
                g=groups[key];g['count']+=1;g['first_at']=min(g['first_at'],ts)
                if ts>=g['last_at']:g['last_at']=ts;g['_ctx']=(p,idx)
                g['hours'][datetime.fromtimestamp(ts).hour]+=1
        except OSError:error=True
        # A file with no lines at all is a quiet source (nothing logged), not a gap in coverage.
        quiet=not error and lines==0
        coverage.append(dict(host=p.stem,bytes_read=min(size,MAX_BYTES)+len(prev),lines=lines,unknown_timestamps=unknown,quiet=quiet,
                             rotated_read=bool(prev),truncated=start>0 or rot_start>0,
                             window_may_be_incomplete=not quiet and (error or unknown>0 or not parsed or min(parsed)>cutoff),
                             oldest_read_at=min(parsed,default=None),newest_read_at=max(parsed,default=None),read_error=error))
    ordered=sorted(groups.values(),key=lambda g:(g['level']=='CRITICAL',g['last_at']),reverse=True)
    top=ordered[:MAX_GROUPS]
    by_file={}
    for g in top:
        src,idx=g.pop('_ctx');by_file.setdefault(src,[]).append((g,idx))
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
                limits=dict(bytes_per_file=MAX_BYTES,files=MAX_FILES,groups=MAX_GROUPS))
