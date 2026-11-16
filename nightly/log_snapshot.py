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
# A live re-check only needs the newest lines, so group_recent reads the tail of each file.
GROUP_RECENT_TAIL_BYTES=2*1024*1024
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
SEEK_LINE_SCAN = 64               # lines a probe walks before giving up
SEEK_LINE_MAX = 8 * 1024 * 1024   # a line longer than this is never read whole
SEEK_LINE_MAX = 8 * 1024 * 1024   # a line longer than this is never read whole


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
      prev_parseable(mid) - the last line whose text covers mid (a probe on a newline
        counts as inside the line that newline terminates). If it is inside the window
        it is a candidate for the window's first line; if it predates the window, no
        timestamped line at or before mid can be inside it (syslog timestamps do not go
        backwards), so the window starts strictly after mid.
    A probe sitting on a terminator is bracketed by the line before it and the line
    after it; a probe inside a line is bracketed by that same line on both sides.
    """

    def __init__(self, f, size, cutoff, boundary=True):
        # boundary decides what a probe sitting on a newline belongs to. The search
        # uses True: the probe has read the line that newline terminates, so an old
        # timestamp found at or before mid is that line's own. The walk forward uses
        # False so it never re-reads the line it just stepped past.
        self.f, self.size, self.cutoff, self.boundary = f, size, cutoff, boundary

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
        line's start; callers must then treat the line as unknown, not guess from a
        slice of it.
        """
        if self.boundary and off < self.size and self._read(off, 1) == b'\n':
            # The probe sits on the newline that ends the line before it.
            start = self._line_ending_at(off)
            if start is None:
                return off, off, b'', False   # that line is longer than the scan cap
            return start, off, self._read(start, off - start), True
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
                # Read the line whole: callers step past it with len(text), so a text
                # cut short at the chunk edge would send them back to the same line.
                start = pos + idx + 1
                return start, limit, self._read(start, limit - start), True
            if pos == 0:
                break
            # Step down by what was read, clamped at the floor, so no byte between the
            # floor and limit is left unread: a gap here would look like "no newline".
            pos = max(0, pos - len(chunk))
        # start == 0 means the scan covered byte 0 and found no newline: the line really
        # does start there. Anything else is the cap cutting the scan short.
        return start, limit, self._read(start, limit - start), start == 0

    def _line_ending_at(self, term):
        """Start of the line terminated by the newline at term, or None when the
        scan cap cannot reach it (a line longer than SEEK_SCAN_CAP)."""
        if term == 0:
            return 0   # an empty line at byte 0
        start = max(0, term - SEEK_SCAN_CAP)
        pos = term
        while pos > start:
            from_ = max(start, pos - SEEK_PROBE_BYTES)
            idx = self._read(from_, pos - from_).rfind(b'\n')
            if idx >= 0:
                return from_ + idx + 1
            pos = from_
        return 0 if start == 0 else None

    def _next_line(self, off):
        """First line whose text reaches off or beyond: (start, text, complete).

        start is size when off is past the end of the file (no line there).
        """
        if off >= self.size:
            return self.size, b'', True
        start, end, text, complete = self._line_containing(off)
        if start <= off:
            return start, text, complete
        # off is a terminator: the line containing it begins after off, and end (the
        # first newline past off) is that line's own terminator - so an empty line is
        # read as empty rather than skipped into the next one.
        return start, self._read(start, max(0, end - start)), True

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

    def first_window_line(self, start):
        """Forward whole-line scan from a position known to be a line start: the first
        line whose text parses inside the window, as (offset, found).

        Lines are read from a chunk buffer, so a file of many short lines costs one read
        per SEEK_PROBE_BYTES rather than one per line. A line with no terminator (the
        file's last line, its rest unknown) is never stepped over: its own start is
        returned as the answer, since every line before it was already shown outside the
        window. A line longer than SEEK_LINE_MAX is not read whole: found=False.
        """
        pos = start          # offset of the first byte in buf
        buf = b''
        while True:
            idx = buf.find(b'\n')
            if idx >= 0:
                ts = _parse_ts(buf[:idx])
                if ts is not None and self._in_window(ts):
                    return pos, True
                pos += idx + 1
                buf = buf[idx + 1:]
                continue
            more = self._read(pos + len(buf), SEEK_PROBE_BYTES)
            if pos + len(buf) >= self.size or not more:
                if buf:
                    # The line that began at pos has no terminator: its rest is unknown
                    # and it is never stepped over, so its own start is the answer.
                    return (pos, True) if pos > 0 else (0, False)
                return 0, False          # the file ended on a terminator: nothing left
            buf += more
            if len(buf) > SEEK_LINE_MAX:
                return 0, False
    def _line_start_at_or_after(self, pos):
        """Snap pos to a line start. pos itself when a line begins there; else the start
        of the line after the one holding pos (probes can move pos just past mid, into a
        line already proven outside the window, so dropping the rest of it loses
        nothing); else, when pos sits in the unterminated tail line with no terminator
        to step past, the start of that tail line.
        """
        if pos == 0:
            return 0
        if self._read(pos - 1, 1) == b'\n':
            return pos
        nxt = self._terminator_after(pos - 1)
        if nxt is not None:
            return nxt + 1
        prev = self._terminator_before(pos)
        return 0 if prev is None else prev + 1
    def _prev_line(self, off):
        """Line terminated by the last newline at or before off: (start, text, complete).

        complete=False when off sits before the first newline (no line ends at or before
        off) or when the line cannot be read whole.
        """
        term = self._terminator_before(off)
        if term is None:
            return off, b'', False
        start = self._line_ending_at(term)
        if start is None:
            return term, b'', False
        return start, self._read(start, term - start), True

    def next_parseable(self, off):
        """First line whose text reaches off and parses, scanning whole lines forward.

        Returns (start, ts, cover, blocked). cover is the start of the line holding off
        itself, so a caller can tell where the scan's "no timestamped line here" region
        begins. blocked=True means the line holding off could not be read whole: nothing
        is known about the region around off, and cover is None.
        """
        pos = off
        cover = None
        scanned = 0
        while True:
            start, text, complete = self._next_line(pos)
            if pos == off:
                if not complete:
                    return None, None, None, True      # the line holding off is unreadable
                cover = start if start <= off else off
            if not complete:
                return None, None, cover, False        # a later line is unreadable
            if start >= self.size:
                return self.size, None, cover, False
            ts = _parse_ts(text)
            if ts is not None:
                return start, ts, cover, False
            pos = start + len(text) + 1
            scanned += 1
            # Both bounds matter: the byte cap alone would walk an endless run of empty
            # or one-byte lines, re-scanning around each one.
            if pos > off + SEEK_SCAN_CAP or scanned > SEEK_LINE_SCAN:
                return None, None, cover, False
        return None, None, None, True

    def prev_parseable(self, off):
        """Last line starting at or before off that parses, scanning whole lines back.

        Returns (None, None) when a line too long to read whole blocks the scan.
        """
        pos = off
        scanned = 0
        while True:
            start, text, complete = self._prev_line(pos)
            if not complete:
                return None, None
            ts = _parse_ts(text)
            if ts is not None:
                return start, ts
            if start == 0:
                return None, None
            pos = start - 1
            scanned += 1
            if pos < off - SEEK_SCAN_CAP or scanned > SEEK_LINE_SCAN:
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

      lo      no window record can start at or before lo;
      hi + 1  the window's first line starts no later than here.

    A probe at mid reads the whole line whose text reaches mid (mid may sit inside a
    line, or on the newline that ends one), then scans whole lines forward from there:

      A. a timestamped line at or after mid, reachable within SEEK_SCAN_CAP, is
         inside the window: it is a candidate, so hi tightens to its start - 1. A
         candidate is a real line start, so hi stays a bound that holds. When the
         candidate lies past hi, no timestamped line starts between the line holding
         mid and that candidate, so hi steps back to before the line holding mid.
      B. the line the probe read predates the window: timestamps do not go backwards,
         so no window record can start at or before mid - lo moves past mid, and past
         that line's start when the probe sits on the newline that ends it.
      C. no timestamped line is reachable from mid. What the probe could read decides:
         if the parseable line ending at or before mid is inside the window it is a
         candidate and hi tightens to its start - 1; if that line predates the window
         and the line holding mid was read whole (so it is not itself a window record),
         lo moves past mid; if the line holding mid could not be read whole, or a scan
         hit its cap, nothing is known - hi only steps back, and lo never moves, because
         an unread line at mid might itself be the window's first record.

    lo only moves up, hi only moves down, and every round moves at least one of them,
    so the interval shrinks each round and the search ends on its own: a candidate at or
    before hi tightens it to the candidate, and a candidate past hi - which cannot bind -
    is handled by stepping back to before the line holding mid, which halves the
    interval. That makes the round guard below unreachable; reaching it is a bug, so it
    is raised rather than returned as an answer (the old 64-round cap returned one).

    The answer is then read by walking whole lines forward from lo, starting at the
    line start at or after it. Each line is read to its terminator, never as a
    truncated window, and only a line shown outside the window is stepped over. The
    last line of a file with no final newline is never stepped over: its rest is
    unknown, so the walk stops there and that line's own start is the answer. A line
    longer than SEEK_LINE_MAX is never read whole, and when the walk reaches one it
    stops and the seek claims nothing.

    Returns (offset, proven). proven=True only for a seek past byte 0: every line
    before that offset was read whole by a probe or the walk and shown outside the
    window, so a read from the offset cannot skip a window record. Byte 0 claims
    nothing and the caller decides coverage from the timestamps it actually parsed.
    """
    try:
        with open(p, 'rb') as f:
            r = _SeekReader(f, size, cutoff)
            lo, hi = 0, size - 1
            # Every round shrinks [lo, hi]; halving is the normal step, so twice the
            # bit length is far more rounds than a converging search can use.
            for _round in range(size.bit_length() * 2 + 8):
                if lo > hi:
                    break
                mid = (lo + hi) // 2
                n_start, n_ts, cover, blocked = r.next_parseable(mid)
                if n_start is not None and n_start < size:
                    if r._in_window(n_ts):
                        # A real in-window line starts at n_start, so the window's
                        # first line starts no later than there: hi = n_start - 1.
                        # When that candidate lies past hi it cannot tighten the bound,
                        # but the probe did show that no timestamped line starts from
                        # cover up to n_start - 1, and the window's first line starts
                        # before cover (or is that candidate itself, which the walk
                        # forward from lo will read). So step back to before cover.
                        if n_start <= hi:
                            hi = n_start - 1
                        elif cover is not None:
                            hi = cover - 1
                        else:
                            # The line holding mid is too long to resolve, so nothing
                            # is known about the region: narrow the interval so the
                            # search still ends. lo is untouched, and the answer comes
                            # from the walk, which never starts above a proven bound.
                            hi = mid - 1
                    else:
                        # Case B: the line the probe read predates the window and no
                        # timestamped line reaches mid any earlier, so no window record
                        # can start at or before mid.
                        lo = max(lo, mid + 1, n_start + 1)
                    continue
                p_start, p_ts = r.prev_parseable(mid)
                if p_start is not None and p_start < size and r._in_window(p_ts):
                    # A whole line ending at or before mid is inside the window, so the
                    # window's first line starts no later than there.
                    hi = p_start - 1
                elif p_ts is not None and not blocked:
                    # The line holding mid was read whole and is not a record start, and
                    # the last line ending at or before mid predates the window, so no
                    # window record can start at or before mid.
                    lo = max(lo, mid + 1)
                else:
                    # Nothing was learned: either the line holding mid is too long to
                    # read (it could itself be the window's first line, so lo must not
                    # move), or the scans hit their caps. Narrow the interval only.
                    hi = mid - 1
            else:
                # Unreachable while every round shrinks the interval. A search that
                # does not converge is a bug, and its answer would be unproven, so it
                # must not be returned as one.
                raise RuntimeError('timestamp seek did not converge: lo=%d hi=%d size=%d' % (lo, hi, size))
            walk = _SeekReader(f, size, cutoff)
            # A probe may have moved lo to just past mid, inside a line already proven
            # outside the window; the walk starts at the next line start so the read
            # begins on a line. From there it reads whole lines forward.
            start = walk._line_start_at_or_after(lo)
            if start is None or start >= size:
                return 0, False
            best, found = walk.first_window_line(start)
            if not found:
                best = None
            if not best:
                # Nothing at or after lo is inside the window, or the walk could not
                # read the file whole. Either way the seek claims nothing: opening at
                # byte 0 reads everything, so it cannot lose a window record.
                return 0, False
            # best > 0: lo ruled out every line before it, and the walk stepped over
            # only older timestamped lines, so opening here cannot skip a record.
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


def _group_id(host,level,normalized):return hashlib.sha256(repr((host,level,normalized)).encode()).hexdigest()[:12]


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


def group_recent(directory,group_id,since,critical,warning,tail_bytes=GROUP_RECENT_TAIL_BYTES):
    """How many lines in one group were logged at or after `since` (the snapshot time), read live.

    An act push is made from a snapshot that is hours old; this re-reads the newest lines of each
    log file and counts the ones the snapshot would have put in this group. The line-to-group
    reduction mirrors snapshot() exactly - scrub, drop the transport host (any case) and any [pid],
    normalize, hash - so a group id from the snapshot is the same id here. Only the tail is read
    (GROUP_RECENT_TAIL_BYTES per file); a file that cannot be read sets error=True instead of
    quietly reporting zero. `last_line` is scrubbed.
    """
    tail_bytes=min(max(int(tail_bytes),0),GROUP_RECENT_TAIL_BYTES)
    count=0;files_scanned=0;error=False;last_line=None
    for p in sorted(p for p in Path(directory).glob('*.log') if not p.is_symlink())[:MAX_FILES]:
        try:
            with p.open('rb') as f:
                f.seek(0,os.SEEK_END);size=f.tell()
                f.seek(max(0,size-tail_bytes))
                if f.tell()>0:f.readline()  # a partial first line is not a line
                chunk=f.read().decode('utf-8','replace')
        except OSError:error=True;continue  # never a silent zero: the caller must know the read failed
        files_scanned+=1
        for line in chunk.splitlines():
            try:ts=datetime.fromisoformat(line.split()[0].replace('Z','+00:00')).timestamp()
            except (ValueError,IndexError):continue
            if ts<since:continue
            level='CRITICAL' if any(r.search(line) for r in critical) else 'WARNING' if any(r.search(line) for r in warning) else None
            if not level:continue
            clean=scrub(line)
            message=re.sub(r'^\S+\s+','',clean)
            while message.casefold().startswith(p.stem.casefold()+' '):message=message[len(p.stem)+1:]
            message=re.sub(r'\b([\w.-]+)\[\d+\]:',r'\1:',message)
            if _group_id(p.stem,level,normalize(message))!=group_id:continue
            count+=1
            if last_line is None or ts>=last_line[0]:last_line=(ts,clean[:300])
    return dict(count=count,files_scanned=files_scanned,error=error,
                last_line=(dict(ts=last_line[0],line=last_line[1]) if last_line else None))


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
                    groups[key]=dict(id=_group_id(*key),host=p.stem,level=level,
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
