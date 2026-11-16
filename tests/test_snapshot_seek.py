from datetime import datetime,timezone
from pathlib import Path
import random,tempfile,unittest
from unittest.mock import patch
from nightly import log_snapshot as s

# The seek decides whether a window record is read at all, so these tests judge it
# against a brute-force scan of the same bytes, not against the seek's own reasoning.
# Every generated file is timestamp-ordered the way an appended syslog file is.

BASE=1000000

def iso(t):
 return datetime.fromtimestamp(t,timezone.utc).isoformat()

def window_line_starts(data,cutoff):
 """Every line start whose own timestamp is at or after cutoff, in file order."""
 out=[];pos=0
 for ln in data.split(b'\n'):
  try:ts=datetime.fromisoformat(ln.split(None,1)[0].decode()).timestamp()
  except (ValueError,IndexError):ts=None
  if ts is not None and ts>=cutoff:out.append(pos)
  pos+=len(ln)+1
 return out

class SeekTests(unittest.TestCase):
 def seek(self,data,cutoff):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.log';p.write_bytes(data)
   return s._seek_window_start(p,cutoff,len(data))
 def gen(self,rnd,lengths):
  """A file of timestamped lines plus blank, continuation and garbage lines."""
  lines=[];t=BASE
  for i in range(rnd.randint(0,40)):
   t+=rnd.choice([0,1,5,60]);k=rnd.random()
   if k<.08:lines.append('')
   elif k<.16:lines.append('  continuation '+'y'*rnd.randint(0,300))
   elif k<.2:lines.append('garbage '+'z'*rnd.randint(0,50))
   else:lines.append(iso(t)+' host msg '+'x'*rnd.choice(lengths))
  # Half the files have no final newline. A line whose tail is not read whole may hold
  # a timestamp before its start, so a seek there can only claim nothing: those cases
  # open at byte 0 (see test_no_final_newline_is_not_claimed), which the check below
  # accepts, and window_line_starts only counts a line whose text was read whole, so
  # nothing gets skipped either way.
  data='\n'.join(lines)+(rnd.choice(['\n','']) if lines else '')
  return data.encode(),BASE+rnd.randint(-10,t-BASE+10)
 def check(self,data,cutoff,trial):
  off,proven=self.seek(data,cutoff)
  starts=window_line_starts(data,cutoff)
  where='seed/trial %s cutoff %s size %d'%(trial,iso(cutoff),len(data))
  if starts:
   # 1. the read must not skip the window's first line, so the offset cannot be past it;
   # 2. and nothing already inside the window may sit before the offset.
   self.assertLessEqual(off,starts[0],'loses the first window line: seek %d, first window line %d (%s)'%(off,starts[0],where))
   self.assertEqual([p for p in starts if p<off],[],'skips a window line before the offset (%s)'%where)
  else:
   # No whole-line record in the window: either the seek claims nothing (byte 0), or it
   # stopped at the last line, which has no terminator and is never skipped.
   tail=data.rfind(b'\n')+1
   self.assertTrue(off==0 or (off==tail and not data.endswith(b'\n')),
    'claims a start with no window line in the file (%s)'%where)
  # 3. the offset is a line start: the read must not begin partway into a line.
  if off:
   self.assertEqual(data[off-1:off],b'\n','offset is not a line start: %d (%s)'%(off,where))
  # 4. proven only ever covers a seek past byte 0.
  self.assertEqual(proven,off>0,'proven disagrees with the offset (%s)'%where)
  return off,starts[0] if starts else None
 def test_repro_older_line_holding_the_probe(self):
  # 15 Nov report: mid landed inside an older line, lo was set to that line's start + 1
  # (barely past nothing, since the line starts before mid), mid repeated, and the
  # 64-round cap returned 63 with proven=True instead of 33. The lines have to differ in
  # length for an older line to hold mid.
  data=(b'2026-11-15T10:00:00-05:00 h mxxx\n'
        b'2026-11-15T10:01:00-05:00 h m\n'
        b'2026-11-15T10:02:00-05:00 h mxxx\n'
        b'2026-11-15T10:02:00-05:00 h m\n')
  cutoff=datetime.fromisoformat('2026-11-15T10:00:30-05:00').timestamp()
  self.assertEqual(self.seek(data,cutoff),(data.index(b'2026-11-15T10:01'),True))
 def test_fuzz_matches_a_brute_force_scan(self):
  for seed in (1,2,3):
   rnd=random.Random(seed)
   for lengths in ([0,5,40,500],[0,5,40,500,8000]):   # short files and 500 KB lines
    for trial in range(150):
     data,cutoff=self.gen(rnd,lengths)
     self.check(data,cutoff,'%d/%d/%s'%(seed,trial,lengths))
 def test_unterminated_tail_line_is_never_skipped(self):
  # The last line of a file with no final newline has an unknown rest, so a seek may
  # stop at that line's own start but must never move past it.
  fresh=iso(1000900)+' host in window'
  old=iso(1000000)+' host older'
  self.assertEqual(self.seek((old+'\n'+fresh).encode(),1000050),(len(old)+1,True))
  # Nothing in the window, and the tail line cannot be read whole: the answer is that
  # line's start, or byte 0 - never a position inside it.
  for off,proven in [self.seek((old+'\n'+old).encode(),1000050)]:
   self.assertTrue(off in (0,len(old)+1) and proven==(off>0),'got %s'%(off,))
  self.assertEqual(self.seek((old+'\n'+old+'\n').encode(),1000050),(0,False))
  self.assertEqual(self.seek(b'',1000050),(0,False))
 def test_seek_stops_at_an_unterminated_tail_line(self):
  # Why the walk must not step past the last line when it has no terminator. Line
  # starts: 0, 28, 56. The final line's text holds a timestamp 10 bytes into it.
  a=b'2026-01-01T00:00:00+00:00 a'; b=b'2026-01-01T00:01:00+00:00 b'
  tail=b'padding 2026-01-01T00:02:00+00:00 c'
  cutoff=datetime.fromisoformat('2026-01-01T00:01:30+00:00').timestamp()
  # An offset anywhere inside that line - byte 66, say - cuts the 00:02 text, and the
  # reader cannot tell an unterminated tail from a line whose rest is still on its way.
  # The seek stops at the tail line's own start, 56, and never goes further.
  off,proven=self.seek(a+b'\n'+b+b'\n'+tail,cutoff)
  self.assertTrue(off in (0,56) and proven==(off>0),'got %s'%(off,))
  # A tail line that begins with an in-window timestamp is a record start: the answer
  # is its start, terminated or not, and proven holds because every line before it was
  # read whole and shown older.
  inw=b'2026-01-01T00:02:00+00:00 c'
  self.assertEqual(self.seek(a+b'\n'+b+b'\n'+inw,cutoff),(56,True))
  self.assertEqual(self.seek(a+b'\n'+b+b'\n'+inw+b'\n',cutoff),(56,True))
 def test_line_too_long_to_read_whole(self):
  # A 300 KB line is longer than SEEK_SCAN_CAP, so a probe sitting inside it cannot be
  # resolved to a line: the search falls back and the walk, which starts at a line
  # start, reads the line whole. (A truncated line text used to send the walk back to
  # the same line forever.)
  data=(''.join(iso(1000000+i*60)+' h '+'x'*(300*1024)+'\n' for i in range(4))).encode()
  for cut in (1000030,1000120,1000200):
   starts=window_line_starts(data,cut)
   want=(starts[0],True) if starts else (0,False)
   self.assertEqual(self.seek(data,cut),want,'cutoff %s: %s expected'%(iso(cut),want))
 def test_long_lines_do_not_stall_the_search(self):
  # 15 Nov: with 300 KB lines every probe landed inside a line the scan could not
  # resolve, and the search made no progress (a candidate past hi left the interval
  # unchanged) until the round cap fired. 200 seeded files of 1-6 lines, some 300 KB:
  # the search must finish and the answer must still be sound.
  for seed in range(200):
   rnd=random.Random(seed)
   lines=[iso(1000000+i*60)+' h '+'x'*rnd.choice([10,300000]) for i in range(rnd.randint(1,6))]
   data=('\n'.join(lines)+'\n').encode()
   cutoff=1000000+rnd.randint(0,len(lines)*60)
   self.check(data,cutoff,'longline %d'%seed)
 def test_search_has_no_round_cap_that_returns_an_answer(self):
  # The old search gave up after 64 rounds and still returned proven=True. Now every
  # round shrinks the interval, so a big file is answered by the loop ending normally.
  data=''.join(iso(1000000+i)+' h msg %d\n'%i for i in range(60000)).encode()
  cutoff=1000300
  starts=window_line_starts(data,cutoff)
  self.assertEqual(self.seek(data,cutoff),(starts[0],True))
 def test_tiny_probe_buffers_never_claim_a_proven_start(self):
  # With the read buffers shrunk below any real line, nothing can be resolved; the seek
  # may read more than it needs, but it must never assert coverage it cannot show.
  with patch.object(s,'SEEK_PROBE_BYTES',4),patch.object(s,'SEEK_SCAN_CAP',8):
   rnd=random.Random(7)
   for trial in range(40):
    data,cutoff=self.gen(rnd,[0,5,40,500])
    self.check(data,cutoff,'tiny %d'%trial)
