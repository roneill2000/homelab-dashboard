import unittest,os,json,tempfile
from pathlib import Path
import storage_monitor as m
import cache_guard as g
import install_cache_policy as policy

# The live Unraid cron line (hand-fixed on 11 Nov 2026, backup *.bak-20261111-fsstate). The installer must write this, byte for byte.
LIVE_LINE = """*/5 * * * * grep -qs '^fsState="Started"' /var/local/emhttp/var.ini && /usr/bin/python3 /boot/config/plugins/dynamix/homelab-cache-guard.py >/dev/null 2>&1"""

class CacheTests(unittest.TestCase):
 def test_threshold_is_85_with_ceiling_at_92(self):
  self.assertEqual((g.THRESHOLD,g.CEILING,g.ACTIVE_WINDOW_S,g.MAX_DEPTH),(85,92,180,3))
  self.assertEqual(str(g.SHARE),'/mnt/cache/4TB')
 def test_below_threshold_starts_nothing(self):
  for pct in (0,25.7,69,84,84.9):self.assertFalse(g.should_start(pct,False,True,True),pct)
  self.assertFalse(g.should_start(84.9,False,True,True,True))
 def test_start_at_threshold_with_no_download_active(self):
  self.assertTrue(g.should_start(85,False,True,True))
  self.assertTrue(g.should_start(91.9,False,True,True))
 def test_deferred_while_a_download_is_writing(self):
  for pct in (85,85.5,91,91.9):self.assertFalse(g.should_start(pct,False,True,True,True),pct)
 def test_hard_ceiling_starts_during_a_download(self):
  self.assertTrue(g.should_start(92,False,True,True,True))
  self.assertTrue(g.should_start(99.5,False,True,True,True))
 def test_mover_already_running_or_array_not_started_starts_nothing(self):
  for active in (False,True):
   self.assertFalse(g.should_start(99,True,True,True,active))     # mover already running
   self.assertFalse(g.should_start(99,False,False,True,active))   # array stopped
   self.assertFalse(g.should_start(99,False,True,False,active))   # cache not mounted
   self.assertFalse(g.should_start(None,False,True,True,active))  # usage unknown
 def test_download_active_sees_a_file_written_60_s_ago(self):
  root=Path(tempfile.mkdtemp());now=1_700_000_000
  (root/'Shows'/'Show A').mkdir(parents=True)
  f=root/'Shows'/'Show A'/'S01E01.mkv';f.write_text('x')
  os.utime(f,(now-60,now-60))
  self.assertTrue(g.download_active(root,now))
  self.assertFalse(g.download_active(root,now+200))  # the write ages past the 180 s window
 def test_download_active_ignores_stale_files_missing_roots_and_symlinks(self):
  root=Path(tempfile.mkdtemp());now=1_700_000_000
  (root/'Movies').mkdir(parents=True)
  f=root/'Movies'/'Old Film (1999)'/'film.mkv';f.parent.mkdir();f.write_text('x')
  os.utime(f,(now-181,now-181))                      # just outside the 180 s window
  self.assertFalse(g.download_active(root,now))
  self.assertFalse(g.download_active(root/'nope',now))
  link=root/'Movies'/'link.mkv';os.symlink(f,link)
  fresh=root/'Movies'/'New (2026)'/'new.mkv';fresh.parent.mkdir();fresh.write_text('y')
  os.utime(fresh,(now-1,now-1))
  self.assertFalse(g.download_active(root,now,window=0))           # window 0: even a 1 s old file is outside it
  self.assertTrue(g.download_active(root,now+179))                 # inside the window again, symlink not the reason
  os.remove(fresh)
  self.assertFalse(g.download_active(root,now))                  # only the symlink is fresh now
 def test_walk_stops_at_depth_3(self):
  root=Path(tempfile.mkdtemp());now=1_700_000_000
  deep=root/'Movies'/'Item'/'extra'/'new.mkv';deep.parent.mkdir(parents=True);deep.write_text('x')
  os.utime(deep,(now-1,now-1))
  self.assertFalse(g.download_active(root,now))                     # level 4: not scanned
  self.assertTrue(g.download_active(root,now,max_depth=4))
 def test_walk_budget_treats_too_much_work_as_active(self):
  root=Path(tempfile.mkdtemp());now=1_700_000_000
  (root/'Movies').mkdir(parents=True)
  for n in range(6):(root/'Movies'/f'x{n}').mkdir();os.utime(root/'Movies'/f'x{n}',(now-9999,now-9999))
  self.assertFalse(g.download_active(root,now))
  self.assertTrue(g.download_active(root,now,max_entries=1))        # over budget -> defer, ceiling still fires
 def test_report_carries_the_new_fields(self):
  for name in ('observed_at','used_pct','threshold_pct','ceiling_pct','active_window_s','active_download','action','reason'):
   self.assertIn(name,(Path(__file__).resolve().parent.parent/'backend'/'cache_guard.py').read_text())
 def test_fill_uses_capacity_change_not_write_counters(self):
  rows=[(t,(20-(t-100)/60)*1024**3,100*1024**3) for t in range(100,401,10)]
  result=m.exhaustion_estimate(rows,400)
  self.assertEqual(result['state'],'filling');self.assertEqual(result['seconds'],900)
  self.assertAlmostEqual(result['growth_bps'],1024**3/60)
 def test_flat_draining_and_full(self):
  flat=[(t,20*1024**3,100*1024**3) for t in range(100,401,10)]
  self.assertEqual(m.exhaustion_estimate(flat,400)['state'],'flat')
  flat[-1]=(400,25*1024**3,100*1024**3)
  self.assertEqual(m.exhaustion_estimate(flat,400)['state'],'draining')
  flat[-1]=(400,0,100*1024**3)
  self.assertEqual(m.exhaustion_estimate(flat,400)['state'],'full')
 def test_recent_growth_is_not_hidden_by_an_earlier_mover_release(self):
  rows=[(t,(1 if t<200 else 40-(t-200)/10)*1024**3,100*1024**3) for t in range(100,401,10)]
  result=m.exhaustion_estimate(rows,400)
  self.assertEqual(result['state'],'filling');self.assertEqual(result['seconds'],200);self.assertEqual(result['window_seconds'],60)
 def test_gaps_stale_missing_resize_short_window(self):
  for rows,now,state in [([],400,'collecting'),([(400,1e9,1e10)],400,'collecting'),([(100,1e9,1e10)],400,'unavailable'),([(400,None,1e10)],400,'unavailable'),([(100,2e9,1e10),(400,1e9,1e10)],400,'collecting')]:
   self.assertEqual(m.exhaustion_estimate(rows,now)['state'],state)

class InstallerTests(unittest.TestCase):
 def test_installer_writes_the_live_fsState_guarded_line(self):
  self.assertEqual(policy.GUARD_LINE,LIVE_LINE)
  self.assertIn("grep -qs '^fsState=\"Started\"' /var/local/emhttp/var.ini &&",policy.GUARD_LINE)
  self.assertTrue(policy.GUARD_LINE.startswith('*/5 * * * * '))
  self.assertIn('deferred while a download is writing',policy.CRON_COMMENT)
  self.assertIn('85',policy.CRON_COMMENT)
  self.assertNotIn('70',policy.CRON_COMMENT)
 def test_generated_remote_script_carries_the_guard_not_an_unguarded_line(self):
  source=(Path(__file__).resolve().parent.parent/'backend'/'cache_guard.py').read_text()
  payload={'source':source,'cron_line':policy.GUARD_LINE,'cron_comment':policy.CRON_COMMENT}
  script=policy.REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(payload))+')')
  compact=script.replace(chr(92),'')  # embedding escapes only add backslashes before quotes and backslashes
  self.assertIn(LIVE_LINE,compact)
  self.assertIn('deferred while a download is writing',compact)
  self.assertNotIn("*/5 * * * * /usr/bin/python3 /boot/config/plugins/dynamix/homelab-cache-guard.py",compact)
  compile(source,'cache_guard.py','exec')
 def test_installed_source_is_the_repo_guard_with_new_thresholds(self):
  source=(Path(__file__).resolve().parent.parent/'backend'/'cache_guard.py').read_text()
  self.assertIn('THRESHOLD=85',source);self.assertIn('CEILING=92',source);self.assertIn('ACTIVE_WINDOW_S=180',source)
  self.assertNotIn('THRESHOLD=70',source)

if __name__=='__main__':unittest.main()
