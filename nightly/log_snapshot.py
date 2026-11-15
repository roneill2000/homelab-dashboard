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



SEEK_PROBE_BYTES = 65536          # window read at each binary-search probe
SEEK_SCAN_CAP = SEEK_PROBE_BYTES * 4   # extra whole-line scan around a probe


def _parse_ts(line):
    if len(line) < 25 or line[4:5] != b'-' or line[7:8] != b'-' \
            or line[10:11] != b'T' or line[13:14] != b':' or line[16:17] != b':':
        return None
    try:
        return datetime.fromisoformat(line.decode('utf-8', 'replace')[:25]).timestamp()
    except ValueError:
        return None


class _SeekReader:
    """Whole-line reader for a log file being opened at a timestamp-selected offset.

    A syslog file is one record per line, but a record whose message contains a raw
    newline arrives as several lines, and only the first carries the timestamp: the
    rest are continuation lines with no timestamp of their own. Seeking to a byte
    offset would therefore start the read partway into a record, or inside a run of
    continuation lines, and the reader would classify records that were only partly
    read as if they were whole ones.

    Every probe here classifies whole lines only. A probe offset is first resolved
    to the full line that contains it; the scan then steps line by line, buffering
    at line starts, so a run of continuation lines is crossed in one scan bounded by
    SEEK_SCAN_CAP rather than by one seek per line.

    Two probes bracket a probe offset, and each gives a bound the binary search can
    use:
      next_parseable(mid) - the first line whose text reaches mid or beyond (the line
        containing mid, or the first line after it), scanning over whole unparseable
        lines to the first timestamped one. If it is inside the window, the window
        starts no later than it; if it predates the window, it and every line up to
        its end predate the window.
      prev_parseable(mid) - the last line that starts at or before mid. If it is
        inside the window, every earlier timestamped line is older (syslog timestamps
        do not go backwards), so the window starts strictly after mid.
    A probe sitting on a terminator is bracketed by the line before it and the line
    after it; a probe inside a line is bracketed by that same line on both sides.
    """

    def __init__(self, f, size, cutoff):
        self.f, self.size, self.cutoff = f, size, cutoff

    def _read(self, off, length):
        self.f.seek(off)
        return self.f.read(length)

    def _terminator_after(self, off):
        """Position of the first newline strictly after off, or None."""
        pos = max(0, off - SEEK_PROBE_BYTES)
        while pos < self.size:
            chunk = self._read(pos, SEEK_PROBE_BYTES)
            if not chunk:
                return None
            idx = chunk.find(b'\n', max(0, off + 1 - pos))
            if idx >= 0:
                return pos + idx
            pos += len(chunk)
        return None

    def _line_containing(self, off):
        """(start, end, text, complete) of the whole line whose text covers off.

        end is the position of that line's terminator, or size when the line is the
        unterminated tail. complete is False when the scan cap could not reach the
        line's start.
        """
        term = self._terminator_after(off)
        limit = self.size if term is None else term
        start = max(0, off - SEEK_SCAN_CAP)
        pos = max(0, limit - SEEK_PROBE_BYTES)
        while pos >= start:
            chunk = self._read(pos, SEEK_PROBE_BYTES)
            if not chunk:
                break
            idx = chunk.rfind(b'\n', 0, min(len(chunk), limit - pos))
            if idx >= 0:
                return pos + idx + 1, limit, chunk[idx + 1:limit - pos], True
            pos -= len(chunk)
        return start, limit, self._read(start, limit - start), False

    def _next_line(self, off):
        """First line whose text reaches off or beyond: (start, text, complete).

        start is size when off is past the end of the file (no line there).
        """
        if off >= self.size:
            return self.size, b'', True
        start, end, text, complete = self._line_containing(off)
        if start <= off:
            return start, text, complete
        # off is a terminator: the line containing it begins after off.
        nxt = self._read(start, min(SEEK_PROBE_BYTES, self.size - start))
        cut = nxt.find(b'\n')
        if cut < 0:
            return start, nxt, True
        return start, nxt[:cut], True

    def _terminator_before(self, off):
        """Position of the last newline at or before off, or None."""
        pos = min(off + 1, self.size)
        while pos > 0:
            start = max(0, pos - SEEK_PROBE_BYTES)
            chunk = self._read(start, pos - start)
            idx = chunk.rfind(b'\n')
            if idx >= 0:
                return start + idx
            pos = start
        return None

    def _prev_line(self, off):
        """Line terminated by the last newline at or before off: (start, text, complete).

        Returns (size, b'', True) when off sits before the first newline, i.e. there
        is no line ending at or before off.
        """
        term = self._terminator_before(off)
        if term is None:
            return self.size, b'', True
        start, end, text, complete = self._line_containing(term)
        return start, text, complete

    def next_parseable(self, off):
        """First line whose text reaches off and parses, scanning whole lines forward."""
        pos = off
        for _ in range(64):
            start, text, _ = self._next_line(pos)
            if start >= self.size:
                return self.size, None
            ts = _parse_ts(text)
            if ts is not None:
                return start, ts
            pos = start + len(text) + 1
            if pos > off + SEEK_SCAN_CAP:
                return None, None
        return None, None

    def prev_parseable(self, off):
        """Last line starting at or before off that parses, scanning whole lines back."""
        pos = off
        for _ in range(64):
            start, text, _ = self._prev_line(pos)
            ts = _parse_ts(text)
            if ts is not None:
                return start, ts
            if start == 0:
                return None, None
            pos = start - 1
            if pos < off - SEEK_SCAN_CAP:
                return None, None
        return None, None

    def _in_window(self, ts):
        # The window runs cutoff..now; nothing inside the file is newer than now.
        return ts is not None and ts >= self.cutoff


def _seek_window_start(p, cutoff, size):
    """Where to open a rotated log so the window's first record is not missed.

    The file is appended and timestamp-ordered, so the offset we want - the start of
    its first line at or after cutoff - is monotone in the file. Binary search
    brackets it with whole-line probes and keeps two bounds on it:

      lo      no line before lo can be the window's first line;
      hi + 1  the window's first line starts no later than here.

    A probe at mid resolves the whole line whose text reaches mid (mid may sit inside
    a line, or on a terminator), then scans whole lines forward from there:

      A. that line, or a later one within SEEK_SCAN_CAP, is timestamped and inside
         the window: the window starts no later than that line, and since an older
         or unparseable line reaches mid, nothing before mid can be the window's
         first line, so hi shrinks to max(mid, that line's start) - 1.
      B. that line is timestamped and predates the window: no window record can start
         at or before it (a window record starting earlier would hold mid itself and
         be the timestamped line the probe just read), so lo moves past its start.
      C. no timestamped line is reachable from mid: an unparseable line, or a run of
         continuation lines longer than SEEK_SCAN_CAP, reaches mid. Such a line is
         never the window's first line, but its head may start before mid, so only
         the parseable line before mid says anything: if that one is inside the
         window, every timestamped line up to mid predates nothing and the window
         starts after mid, so lo moves past mid; otherwise step back conservatively.

    Returns (offset, proven). proven=True only for a seek past byte 0: every line
    before that offset was ruled out by a probe, so the read from it cannot skip a
    window record. Byte 0 claims nothing and the caller decides coverage from the
    timestamps it actually parsed.
    """
    try:
        with open(p, 'rb') as f:
            r = _SeekReader(f, size, cutoff)
            lo, hi, best = 0, size - 1, None
            for _ in range(64):
                if lo > hi:
                    break
                mid = (lo + hi) // 2
                n_start, n_ts = r.next_parseable(mid)
                if n_start is not None and n_start < size and r._in_window(n_ts):
                    best = n_start if best is None else min(best, n_start)
                    hi = max(mid, n_start) - 1
                    continue
                if n_start is not None and n_start < size:
                    # A timestamped older line reaches mid: it and everything before
                    # its end predate the window.
                    lo = max(lo, n_start + 1)
                    continue
                p_ts = r.prev_parseable(mid)[1]
                if p_ts is not None and r._in_window(p_ts):
                    lo = max(lo, mid + 1)
                else:
                    hi = mid - 1
            start = lo
            if best is not None:
                start = best
            for _ in range(4096):
                if start >= size:
                    break
                n_start, n_ts = r.next_parseable(start)
                if n_start is None or n_start >= size:
                    break
                if r._in_window(n_ts):
                    best = n_start if best is None else min(best, n_start)
                    start = n_start
                    break
                ts_line = r._next_line(start)
                if ts_line[0] != n_start:
                    break
                start = n_start + len(ts_line[1]) + 1
            if not best:
                # Nothing found, or byte 0: either way the seek claims nothing. A
                # rotated sibling, or a read capped short of byte 0, may still hold
                # earlier window records, so the caller decides coverage.
                return 0, False
            # best > 0: every line before it was ruled out by a probe - an older
            # timestamp the walk stepped past, or an unparseable line the search
            # bracketed - so opening here cannot skip a window record.
            return best, True
    except OSError:
        return 0, False


def _read_stream(p, rot_start, prev_len, start, raw_len):
    rotated=p.with_name(p.name+'.1');prev=b'';raw=b''
    if prev_len and rotated.is_file() and not rotated.is_symlink():
        with rotated.open('rb') as f:
            f.seek(rot_start)
            prev=f.read(prev_len)
    with p.open('rb') as f:
        f.seek(start)
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
    for p in files[:MAX_FILES]:
        size=p.stat().st_size;start=0;_cur_proven=False;_rot_proven=False;parsed=[];unknown=0;lines=0;error=False
        # Weekly rotation leaves the start of the window in <name>.log.1 (uncompressed: delaycompress), so read it
        # too while it was still being written inside the window.
        rotated=p.with_name(p.name+'.1');prev=b'';rot_start=0;rotated_read=False;truncated=False
        try:
            if rotated.is_file() and not rotated.is_symlink() and rotated.stat().st_mtime>=cutoff:
                rotated_read=True
                rsize=rotated.stat().st_size;rot_start,_rot_proven=_seek_window_start(rotated,cutoff,rsize)
                with rotated.open('rb') as f:
                    f.seek(rot_start)  # _seek_window_start returns a line start, so no partial-line skip here
                    prev=f.read(MAX_BYTES)
                    if f.tell()<rsize:truncated=True
        except OSError:error=True
        try:
            start,_cur_proven=_seek_window_start(p,cutoff,size)
            with p.open('rb') as f:
                f.seek(start)  # a line start already, see _seek_window_start
                raw=f.read(MAX_BYTES)
                if f.tell()<size:truncated=True
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
                # The host name in front of the message is the device's own spelling, which is
                # not always the case of the log file's stem, and some devices repeat it (the
                # GL-BE9300 router writes 'GL-BE9300 netifd: ...' after the transport host).
                while message.casefold().startswith(p.stem.casefold()+' '):message=message[len(p.stem)+1:]
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
        # Coverage of the window has two halves: the read must start at or before the window start (proven by a seek
        # that passed byte 0, by a seek inside the rotated sibling, or by reading a line that predates the cutoff),
        # and it must not stop short of the end of the file. A truncated read always leaves a gap, so it reports the
        # window as possibly incomplete however good the start boundary is.
        quiet=not error and lines==0
        proven=_cur_proven or _rot_proven or bool(parsed and min(parsed)<=cutoff)
        coverage.append(dict(host=p.stem,bytes_read=len(prev)+len(raw),lines=lines,unknown_timestamps=unknown,quiet=quiet,
                             rotated_read=rotated_read,truncated=truncated,
                             window_may_be_incomplete=not quiet and (error or unknown>0 or not parsed or truncated or not proven),
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
