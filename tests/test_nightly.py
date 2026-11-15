from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,re,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from nightly import log_snapshot as s,runner as r
from nightly.regression import run_regression as g

class SnapshotTests(unittest.TestCase):
 def scan(self,lines,now=200000,baseline=None):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'utilities.log').write_text('\n'.join(lines)+'\n')
   return s.snapshot(Path(d),[re.compile('CRITICAL')],[re.compile('FAILURE')],now,baseline)
 def line(self,ts,msg):
  return datetime.fromtimestamp(ts,timezone.utc).isoformat()+' utilities '+msg
 def test_window_grouping_and_redaction(self):
  data=self.scan([self.line(110000,'old FAILURE'),self.line(199990,'systemd[1]: FAILURE token=abc'),self.line(199999,'systemd[4]: FAILURE token=def'),self.line(200090,'future FAILURE')])
  self.assertEqual(data['matched_entries'],2);self.assertEqual(data['distinct_groups'],1)
  self.assertEqual(data['groups'][0]['count'],2);self.assertNotIn('abc',json.dumps(data));self.assertFalse(data['coverage_limited'])
 def test_incomplete_timestamp_and_empty_sources(self):
  data=self.scan([self.line(199990,'FAILURE'),'unknown FAILURE'])
  self.assertTrue(data['coverage_limited']);self.assertEqual(data['hosts'][0]['unknown_timestamps'],1)
  with tempfile.TemporaryDirectory() as d:
   self.assertTrue(s.snapshot(Path(d),[],[],200000)['coverage_limited'])
 def test_tail_limits_do_not_claim_whole_window(self):
  with patch.object(s,'MAX_BYTES',100):
   data=self.scan([self.line(100000+i,'x'*70+' FAILURE') for i in range(5)]+[self.line(199999,'FAILURE')])
  self.assertTrue(data['hosts'][0]['truncated']);self.assertTrue(data['coverage_limited'])
 def test_rotation_and_quiet_sources_are_full_coverage(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);import os
   (p/'utilities.log.1').write_text(self.line(110000,'older than the window')+'\n'+self.line(150000,'FAILURE before rotation')+'\n'+self.line(190000,'ok')+'\n')
   os.utime(p/'utilities.log.1',(190000,190000))
   (p/'utilities.log').write_text(self.line(195000,'FAILURE after rotation')+'\n')
   (p/'kasm.log').write_text('')
   data=s.snapshot(p,[re.compile('CRITICAL')],[re.compile('FAILURE')],200000)
  self.assertFalse(data['coverage_limited']);self.assertEqual(data['matched_entries'],2)
  hosts={h['host']:h for h in data['hosts']}
  self.assertTrue(hosts['utilities']['rotated_read']);self.assertTrue(hosts['kasm']['quiet'])
  # An old rotated file (outside the window) is not read, so a recent-only current file still counts as a gap.
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'utilities.log.1').write_text(self.line(50000,'x')+'\n');os.utime(p/'utilities.log.1',(50000,50000))
   (p/'utilities.log').write_text(self.line(195000,'FAILURE')+'\n')
   self.assertTrue(s.snapshot(p,[],[re.compile('FAILURE')],200000)['coverage_limited'])
 def test_timestamp_seek_covers_a_window_bigger_than_the_read_budget(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   line=lambda ts,msg:datetime.fromtimestamp(ts,timezone.utc).isoformat()+' utilities '+msg
   lines=[line(113000+i,'x'*60) for i in range(400)]  # all predate the cutoff (113600)
   lines.append(line(114000,'early-in-window FAILURE start of the window'))
   lines.append(line(199990,'late FAILURE end'))
   (p/'utilities.log').write_text('\n'.join(lines)+'\n')
   with patch.object(s,'MAX_BYTES',8000):
    data=s.snapshot(p,[re.compile('CRITICAL')],[re.compile('FAILURE')],200000)
  hosts={h['host']:h for h in data['hosts']}
  self.assertFalse(hosts['utilities']['window_may_be_incomplete'])
  self.assertFalse(hosts['utilities']['truncated'])
  msgs=' '.join(g['message'] for g in data['groups'])
  self.assertIn('start of the window',msgs)  # the old tail read had skipped it
  self.assertIn('late FAILURE end',msgs)
 def test_critical_priority_and_symlink_ignored(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'real.log').write_text(self.line(199900,'CRITICAL')+'\n'+self.line(199999,'FAILURE'))
   (p/'copy.log').symlink_to(p/'real.log')
   data=s.snapshot(p,[re.compile('CRITICAL')],[re.compile('FAILURE')],200000)
  self.assertEqual(data['files_scanned'],1);self.assertEqual(data['groups'][0]['level'],'CRITICAL')
 def test_context_extraction_before_after_latest_bounds_and_scrubbing(self):
  lines=[self.line(199000+i,f'pre1-{i}') for i in range(10)]
  lines.append(self.line(199020,'svc[1]: FAILURE boom'))
  lines.extend(self.line(199021+i,f'post1-{i}') for i in range(10))
  lines.extend(self.line(199100+i,f'pre2-{i}') for i in range(10))
  lines[-2]=self.line(199108,'ctx token=SECRET_CTX_TOKEN https://u:SECRET_PASS@example.invalid/p '+('z'*350))
  lines.append(self.line(199120,'svc[2]: FAILURE boom'))
  lines.extend(self.line(199121+i,f'post2-{i}') for i in range(6))
  data=self.scan(lines,now=200000)
  g=data['groups'][0];ctx=g['context']
  self.assertEqual(len(ctx),13)
  self.assertTrue(any('pre2-2' in l for l in ctx))
  self.assertFalse(any('pre2-1' in l for l in ctx))
  self.assertTrue(any('post2-3' in l for l in ctx))
  self.assertFalse(any('post2-4' in l for l in ctx))
  self.assertTrue(all(len(l)<=300 for l in ctx))
  self.assertNotIn('SECRET_CTX_TOKEN',json.dumps(ctx))
  self.assertNotIn('SECRET_PASS',json.dumps(ctx))
  self.assertEqual(len(g['hours']),24);self.assertEqual(sum(g['hours']),2)
 def test_normalize_folds_identifiers_keeps_known_cause_tokens(self):
  self.assertEqual(s.normalize('Failed password for root from 10.9.8.7 port 51544 ssh2'),'Failed password for root from <ip> port <n> ssh2')
  self.assertEqual(s.normalize('Failed password for root from 192.168.0.157 port 51544 ssh2'),'Failed password for root from <lan:.157> port <n> ssh2')
  self.assertEqual(s.normalize('Main process exited, code=exited, status=143/n/a'),'Main process exited, code=exited, status=143/n/a')  # small causes and =-attached statuses stay
  self.assertEqual(s.normalize('wlan0 sdc1 dm-7 status=3 rebooted in 45s'),'wlan0 sdc1 dm-7 status=3 rebooted in <n>s')
  self.assertEqual(s.normalize('ll ac:de:48:00:11:22 brd ff:ff:ff:ff:ff:ff'),'ll <mac> brd <mac>')
  self.assertEqual(s.normalize('job id 3f9a1b2c-4d5e-6f70-8192-a3b4c5d6e7f8 done'),'job id <uuid> done')
  self.assertEqual(s.normalize('job gid 7f2e status deadbeef12'),'job gid 7f2e status <hex>')
  self.assertEqual(s.normalize('ran at 2026-11-12T03:04:05-05:00 after Nov 12 03:04:06'),'ran at <ts> after <ts>')
  self.assertEqual(s.normalize('took 12.3s copied 512MB used 45% of 4096 blocks'),'took <n>s copied <n>MB used <n>% of <n> blocks')
  self.assertEqual(s.normalize('fe80::1 via dev eth0 link up'),'<ip> via dev eth0 link up')
 def test_near_duplicate_lines_fold_into_one_group_with_variants(self):
  data=self.scan([self.line(199990,'sshd: FAILURE Failed password for root from 10.9.8.7 port 51544 ssh2'),
                  self.line(199994,'sshd: FAILURE Failed password for root from 10.9.8.9 port 51561 ssh2'),
                  self.line(199997,'sshd: FAILURE Failed password for root from 10.40.1.2 port 40022 ssh2')])
  self.assertEqual(data['distinct_groups'],1);self.assertEqual(data['matched_entries'],3)
  g=data['groups'][0]
  self.assertEqual(g['variants']['count'],3)
  self.assertEqual(len(g['variants']['examples']),3)
  self.assertIn('10.9.8.7',g['variants']['examples'][0])  # raw scrubbed examples are kept
  self.assertEqual(g['message'],'sshd: FAILURE Failed password for root from 10.40.1.2 port 40022 ssh2')  # latest raw example
  self.assertEqual(g['normalized'],'sshd: FAILURE Failed password for root from <ip> port <n> ssh2')
  self.assertEqual(g['id'],hashlib.sha256(repr(('utilities','WARNING',g['normalized'])).encode()).hexdigest()[:12])
 def test_variant_example_list_is_bounded(self):
  data=self.scan([self.line(199990+i,'sshd: from 10.9.8.{} port 515{}4 FAILURE'.format(i,i)) for i in range(1,5)])
  self.assertEqual(data['groups'][0]['variants']['count'],4);self.assertEqual(len(data['groups'][0]['variants']['examples']),3)
 def test_exit_status_differences_stay_separate_groups(self):
  data=self.scan([self.line(199990,'dl-A.service: FAILURE Main process exited, code=exited, status=143/n/a'),
                  self.line(199995,'dl-A.service: FAILURE Main process exited, code=exited, status=3/NOTIMPLEMENTED')])
  self.assertEqual(data['distinct_groups'],2)
  self.assertTrue(any('143' in g['normalized'] for g in data['groups']))
  self.assertTrue(any('3/NOTIMPLEMENTED' in g['normalized'] for g in data['groups']))
 def test_novelty_ranks_a_first_seen_line_above_a_nightly_one(self):
  with tempfile.TemporaryDirectory() as d:
   base=str(Path(d)/'baseline.json')
   first=self.scan([self.line(1794499990,'nightly.service: FAILURE heartbeat')],now=1794500000,baseline=base)
   self.assertEqual(first['groups'][0]['nights_seen_30'],0);self.assertEqual(first['groups'][0]['novelty'],5)
   today1=datetime.fromtimestamp(1794500000).strftime('%Y-%m-%d')
   saved=json.loads(Path(base).read_text())
   key=json.dumps(['utilities','WARNING','nightly.service: FAILURE heartbeat'])
   self.assertEqual(saved['keys'][key]['counts'],[1]);self.assertEqual(saved['keys'][key]['first_seen'],today1)
   data=self.scan([self.line(1794586390,'nightly.service: FAILURE heartbeat'),
                   self.line(1794586395,'brandnew FAILURE first time ever')],now=1794586400,baseline=base)
   top=data['groups'][0]
   self.assertEqual(top['normalized'],'brandnew FAILURE first time ever');self.assertEqual(top['nights_seen_30'],0)
   nightly=next(g for g in data['groups'] if 'heartbeat' in g['message'])
   self.assertEqual((nightly['nights_seen_30'],nightly['baseline_count'],nightly['novelty']),(1,1,0))
   self.assertEqual(nightly['first_seen'],today1)
 def test_group_id_is_stable_while_raw_examples_change(self):
  with tempfile.TemporaryDirectory() as d:
   base=str(Path(d)/'baseline.json')
   d1=self.scan([self.line(1794499990,'sshd: from 10.9.8.7 port 51544 FAILURE')],now=1794500000,baseline=base)
   d2=self.scan([self.line(1794586390,'sshd: from 10.9.8.9 port 51561 FAILURE')],now=1794586400,baseline=base)
   self.assertEqual(d1['groups'][0]['id'],d2['groups'][0]['id'])
   self.assertEqual((d2['groups'][0]['nights_seen_30'],d2['groups'][0]['baseline_count']),(1,1))
   entry=json.loads(Path(base).read_text())['keys'][json.dumps(['utilities','WARNING','sshd: from <ip> port <n> FAILURE'])]
   self.assertEqual(entry['counts'],[1,1])
 def test_baseline_keeps_30_nights_and_records_repeat_counts(self):
  groups={'g':dict(host='h',level='WARNING',normalized='m',count=1)}
  base={};day=datetime(2026,10,1)
  for i in range(32):s.update_baseline(base,groups,(day+timedelta(days=i)).strftime('%Y-%m-%d'))
  entry=base[json.dumps(['h','WARNING','m'])]
  self.assertEqual(len(entry['dates']),30);self.assertEqual(entry['dates'][0],'2026-10-03')
  self.assertEqual(entry['first_seen'],'2026-10-01');self.assertEqual(entry['last_seen'],'2026-11-01')
  s.update_baseline(base,{'g':dict(host='h',level='WARNING',normalized='m',count=7)},'2026-11-01')
  self.assertEqual(base[json.dumps(['h','WARNING','m'])]['counts'][-1],7)  # a repeat run the same night takes the larger count
 def test_baseline_key_cap_and_size_bound_evict_oldest_first(self):
  groups=lambda n:{n:dict(host='h',level='WARNING',normalized=n,count=1)}
  with patch.object(s,'BASELINE_MAX_KEYS',2):
   base={}
   for i,n in enumerate(('a','b','c')):s.update_baseline(base,groups(n),(datetime(2026,11,1)+timedelta(days=i)).strftime('%Y-%m-%d'))
   self.assertEqual(len(base),2);self.assertNotIn(json.dumps(['h','WARNING','a']),base)
  with patch.object(s,'BASELINE_MAX_BYTES',600):
   base={}
   for i in range(6):s.update_baseline(base,groups('key%02d-%s'%(i,'x'*40)),(datetime(2026,11,1)+timedelta(days=i)).strftime('%Y-%m-%d'))
   self.assertLess(len(base),6)
   self.assertLessEqual(len(json.dumps(dict(updated_at=1,keys=base))),600)

class RunnerTests(unittest.TestCase):
 def test_backup_requires_fresh_explicit_idle(self):
  def value(**kw):return {'activity':{'recovery':{'backup':kw}}}
  self.assertTrue(r.backup_clear(value(running=False,observed_at=950),1000))
  for v in [value(running=True,observed_at=950),value(running=False,observed_at=1),value(running=False,observed_at=1001),{}]:self.assertFalse(r.backup_clear(v,1000))
 def test_recovered_state_uses_current_probe_even_without_recent_alert_ring(self):
  snap=dict(window_start=1,window_end=1000,coverage_limited=True,files_scanned=1,matched_entries=1,distinct_groups=1,source='patterns',groups=[dict(id='a',host='utilities',level='WARNING',count=1,first_at=800,last_at=800,message='filebrowser.service: FAILURE',secret='discard')])
  svc={'utilities/filebrowser.service':dict(load='loaded',active='active',sub='running',active_since=850,observed_at=990)}
  with patch.object(r.time,'time',return_value=1000):e=r.prepare_evidence(snap,svc,{})
  self.assertEqual(e['groups'][0]['current_service_state']['state'],'running_again');self.assertNotIn('secret',e['groups'][0])
  with patch.object(r.time,'time',return_value=1200):e=r.prepare_evidence(snap,svc,{})
  self.assertEqual(e['groups'][0]['current_service_state']['state'],'unverified')
 def test_container_loads_only_local_read_only_model(self):
  command=r.container_command();self.assertIn('--model',command);self.assertNotIn('--source_model',command)
  self.assertIn('/dev/dri/renderD129',command);self.assertIn('127.0.0.1:11437:8000',command)
  self.assertTrue(command[command.index('-v')+1].endswith(':ro'))
  self.assertIn('127.0.0.1:8000/health',command[command.index('--health-cmd')+1])
 def test_prompt_includes_context_hours_local_times_and_notes(self):
  group=dict(host='gl-be9300',message='wlan0: link down',count=3,
             first_at=1000,last_at=2000,hours=[0]*24,context=['line1','line2'],
             current_service_state={'state':'unverified'},secret='private')
  body=r.request_body(group);payload=json.loads(body['messages'][-1]['content'])
  self.assertEqual(payload['host'],'gl-be9300')
  self.assertEqual(payload['count'],3)
  self.assertEqual(payload['context'],['line1','line2'])
  self.assertEqual(len(payload['hours']),24)
  self.assertIsInstance(payload['first_at'],str)
  self.assertIn('notes',payload)
  self.assertNotIn('secret',payload)
  self.assertTrue(body['chat_template_kwargs']['enable_thinking'])
  self.assertLessEqual(body['max_tokens'],1152)
  self.assertIn('--reasoning-budget',r.container_command())
 def test_known_causes_matching(self):
  self.assertTrue(r.match_known_causes({'host':'utilities','message':'dl-123.service: exited status=143'}))
  self.assertTrue(r.match_known_causes({'host':'gl-be9300','message':'hostapd: wlan1: disconnected'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'scrutiny-collector.service failed'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'Memory cgroup out of memory: Killed process'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'dns-health: 192.168.0.10 timed out'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'bench-dflash2.service: Main process exited, code=exited, status=1/FAILURE'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'agy-audit-dflash2b.service: Failed with result exit-code.'}))
  self.assertTrue(r.match_known_causes({'host':'proxmox','message':'pi-queue.service: Failed'}))
  self.assertEqual(r.match_known_causes({'host':'proxmox','message':'benchmark.service failed'}),[])
  self.assertIn('not a Stop', r.match_known_causes({'host':'proxmox','message':'dl-x.service: Main process exited, code=exited, status=3/NOTIMPLEMENTED'})[0])
  self.assertNotIn('not a Stop', ' '.join(r.match_known_causes({'host':'proxmox','message':'dl-x.service: Main process exited, code=exited, status=143/n/a'})))
  self.assertTrue(r.boot_notes({'context':['GL kernel: [   47.882101] port 5 link down']}))
  self.assertTrue(r.boot_notes({'context':['GL kernel: [    0.000000] Booting Linux on physical CPU 0']}))
  self.assertFalse(r.boot_notes({'context':['GL kernel: [ 1232.842060] wlan: stop']}))
  self.assertEqual(r.request_body({'host':'h','message':'m','context':[]})['top_k'],0)
  self.assertEqual(r.match_known_causes({'host':'utilities','message':'unrelated warning'}),[])
 def test_parse_finding_valid_fenced_and_malformed(self):
  ok=r.parse_finding('{"what":"Likely stopped by user.","verdict":"ignore","next":"check"}')
  self.assertEqual(ok,{'what':'Likely stopped by user.','verdict':'ignore','next':''})
  fenced=r.parse_finding('```json\n{"what":"Disk full.","verdict":"act","next":"Free space"}\n```')
  self.assertEqual(fenced,{'what':'Disk full.','verdict':'act','next':'Free space'})
  malformed=r.parse_finding('Not JSON at all, just plain text')
  self.assertEqual(malformed,{'what':'Not JSON at all, just plain text','verdict':'watch','next':''})
 def test_repeat_skipping(self):
  gs=[dict(id='g1',host='h',level='WARNING',count=1,first_at=1,last_at=2,message='x'),
      dict(id='g2',host='h',level='WARNING',count=1,first_at=1,last_at=2,message='y')]
  snapshot=dict(groups=gs,window_start=1,window_end=1000,coverage_limited=False,files_scanned=1,matched_entries=2,distinct_groups=2,source='patterns',hosts=[])
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot]
  with tempfile.TemporaryDirectory() as d:
   reports=Path(d)/'reports';reports.mkdir()
   key=r.review_key(gs[0])
   (reports/'latest.json').write_text(json.dumps({'status':'complete','local_date':'2000-01-01','findings':[{'group_id':'g1','interpretation':'Old','verdict':'watch','next':'Monitor','review_key':key},{'group_id':'g2','interpretation':'Stale','verdict':'act','next':'','review_key':'other-model'}]}))
   with patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',return_value=1000),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),patch.object(r,'start_model',return_value=10.0) as st,patch.object(r,'review_group',return_value=({'group_id':'g2','interpretation':'New','verdict':'act','next':'Fix'},{},None)) as rv,patch.object(r,'stop_model'):
    r.main()
   saved=json.loads((reports/'latest.json').read_text())
   self.assertEqual(st.call_count,1);self.assertEqual(rv.call_count,1)
   f1=next(f for f in saved['findings'] if f['group_id']=='g1')
   self.assertEqual(f1['repeat_nights'],2);self.assertTrue(f1['repeated'])
   f2=next(f for f in saved['findings'] if f['group_id']=='g2')
   self.assertEqual(f2['interpretation'],'New')  # a verdict from another model/prompt is reviewed again
   self.assertEqual(f2['review_key'],r.review_key(gs[1]))
 def test_explanation_cannot_omit_explicit_exit_status(self):
  group={'id':'x','message':'filebrowser.service: Main process exited, code=exited, status=1/FAILURE'}
  withheld=r.checked_finding(group,'The log does not reveal the specific error code.')
  self.assertEqual(withheld['withheld_reason'],'exit_status_omitted')
  self.assertIn('exit status 1',withheld['interpretation'])
  accepted=r.checked_finding(group,'The process exited with status 1; the underlying cause is unknown.')
  self.assertNotIn('withheld_reason',accepted)
  self.assertIn('withheld_reason',r.checked_finding(group,'The process returned status 10.'))
  self.assertIn('withheld_reason',r.checked_finding(group,'There is 1 recorded failure, but the exit code is unknown.'))
  self.assertNotIn('withheld_reason',r.checked_finding(group,'The exit code was unknown; status=1 was recorded.'))
 def test_failure_stops_owned_container_and_saves_failure(self):
  snapshot=dict(groups=[],window_start=1,window_end=1000,coverage_limited=True,files_scanned=0,matched_entries=0,distinct_groups=0,source='patterns',hosts=[])
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot]
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',return_value=1000),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=type('Space',(),{'free':2**32})()),patch.object(r.subprocess,'run') as run,patch.object(r,'stop_model') as stop:
   run.side_effect=[type('Result',(),{'returncode':1})(),RuntimeError('launch failure'),None]
   with self.assertRaises(RuntimeError):r.main()
   saved=json.loads((Path(d)/'reports/latest.json').read_text())
   self.assertEqual(saved['status'],'failed');stop.assert_called_once()
 def test_success_publishes_only_final_answer_and_releases_gpu(self):
  group=dict(id='g',host='utilities',level='WARNING',count=1,first_at=800,last_at=800,message='filebrowser.service: FAILURE')
  snapshot=dict(groups=[group],window_start=1,window_end=1000,coverage_limited=True,files_scanned=1,matched_entries=1,distinct_groups=1,source='patterns',hosts=[])
  answer={'choices':[{'finish_reason':'stop','message':{'content':'Historical process failure.','reasoning_content':'Private speculation that must not be published.'}}],'usage':{'prompt_tokens':10,'completion_tokens':20}}
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot,{'data':[{'id':r.MODEL}]},answer]
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',return_value=1000),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=type('Space',(),{'free':2**32})()),patch.object(r.subprocess,'run') as run,patch.object(r,'stop_model') as stop:
   run.return_value=type('Result',(),{'returncode':1})()
   r.main();raw=(Path(d)/'reports/latest.json').read_text();saved=json.loads(raw)
   self.assertEqual(saved['status'],'complete');self.assertEqual(saved['findings'][0]['interpretation'],'Historical process failure.')
   self.assertNotIn('Private speculation',raw);self.assertEqual(saved['usage']['completion_tokens'],20)
   self.assertIn('load_seconds',saved);self.assertIn('generation_seconds',saved)
   stop.assert_called_once()
 def test_command_runs_e4b_fully_on_the_a310(self):
  cmd=r.container_command()
  self.assertEqual(cmd[cmd.index('--gpu-layers')+1],'all');self.assertIn('--override-tensor',cmd)
  self.assertEqual(cmd[cmd.index('--alias')+1],r.MODEL);self.assertEqual(cmd[cmd.index('--memory')+1],'7g')
 def _run(self,groups,start=None,review=None,clock=None):
  snapshot=dict(groups=groups,window_start=1,window_end=1000,coverage_limited=False,files_scanned=1,matched_entries=len(groups),distinct_groups=len(groups),source='patterns',hosts=[])
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot]
  d=tempfile.mkdtemp()
  with patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',side_effect=clock or (lambda:1000)),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),patch.object(r,'start_model',side_effect=start or [12.0]) as st,patch.object(r,'review_group',side_effect=review or []) as rv,patch.object(r,'stop_model') as stop:
   try:r.main()
   except Exception:pass
   return json.loads((Path(d)/'reports/latest.json').read_text()),st,rv,stop
 def test_model_load_failure_fails_the_run(self):
  g=dict(id='g1',host='h',level='WARNING',count=1,first_at=1,last_at=2,message='x')
  saved,st,rv,stop=self._run([g],start=[RuntimeError('oom')])
  self.assertEqual(saved['status'],'failed');self.assertEqual(st.call_count,1);rv.assert_not_called()
 def test_failed_group_is_skipped_not_fatal(self):
  gs=[dict(id=f'g{i}',host='h',level='WARNING',count=1,first_at=1,last_at=2,message='x') for i in (1,2)]
  saved,st,rv,stop=self._run(gs,review=[(None,None,'TimeoutError'),({'group_id':'g2','interpretation':'ok'},{},None)])
  self.assertEqual(saved['status'],'complete');self.assertEqual(saved['skipped_groups'],[{'group_id':'g1','reason':'TimeoutError'}])
  self.assertEqual(saved['model_label'],'Nemotron Bolt E4B · A310')
  all_bad,*_=self._run(gs,review=[(None,None,'empty'),(None,None,'empty')])
  self.assertEqual(all_bad['status'],'failed')
 def test_run_time_limit_skips_remaining_groups(self):
  gs=[dict(id=f'g{i}',host='h',level='WARNING',count=1,first_at=1,last_at=2,message='x') for i in (1,2)]
  now=[1000]
  def slow_review(group,profile):  # the first answer uses up the whole run budget
   now[0]+=r.RUN_LIMIT+1;return {'group_id':group['id'],'interpretation':'ok'},{},None
  saved,st,rv,stop=self._run(gs,review=slow_review,clock=lambda:now[0])
  self.assertEqual(saved['status'],'complete');self.assertEqual(rv.call_count,1)
  self.assertIn({'group_id':'g2','reason':'run_time_limit'},saved['skipped_groups'])
 def test_review_group_retries_once(self):
  ok={'choices':[{'finish_reason':'stop','message':{'content':'Fine.'}}],'usage':{'completion_tokens':3}}
  with patch.object(r,'fetch',side_effect=[TimeoutError(),ok]):
   finding,used,reason=r.review_group({'id':'g','host':'h','message':'m'},'quick')
  self.assertEqual(finding['interpretation'],'Fine.');self.assertIsNone(reason)
  with patch.object(r,'fetch',side_effect=[TimeoutError(),TimeoutError()]):
   self.assertEqual(r.review_group({'id':'g','host':'h','message':'m'},'quick'),(None,None,'TimeoutError'))
 def test_stop_model_waits_for_removal_and_falls_back_to_remove(self):
  result=lambda code=0,out='',err='':SimpleNamespace(returncode=code,stdout=out,stderr=err)
  gone=result(1,err='Error: No such object: '+r.NAME)
  with patch.object(r.subprocess,'run',side_effect=[result(),gone]) as run:
   r.stop_model();self.assertEqual(run.call_count,2)
  # stopped but not yet removed by --rm: keep waiting, never start a new one over it
  with patch.object(r.subprocess,'run',side_effect=[result(),result(out='false'),result(out='false'),gone]) as run,patch.object(r.time,'sleep'):
   r.stop_model();self.assertEqual(run.call_count,4);self.assertNotIn(['docker','rm','-f',r.NAME],[c.args[0] for c in run.call_args_list])
  with patch.object(r,'_wait_gone',side_effect=[False,True]),patch.object(r.subprocess,'run',return_value=result()) as run:
   r.stop_model();self.assertEqual(run.call_args_list[1].args[0][:3],['docker','rm','-f'])
  with patch.object(r.subprocess,'run',side_effect=[result(1),result(1,err='daemon unavailable'),result(1),result(1,err='daemon unavailable')]):
   with self.assertRaisesRegex(RuntimeError,'cleanup failed'):r.stop_model()
 def test_lock_contention_manual_returns_and_scheduled_retries(self):
  with patch.object(r.fcntl,'flock',side_effect=BlockingIOError):
   self.assertFalse(r.acquire_run_lock(object()))
  with patch.object(r.fcntl,'flock',side_effect=[BlockingIOError,None]),patch.object(r.time,'sleep') as sleep:
   self.assertTrue(r.acquire_run_lock(object(),True));sleep.assert_called_once()
  with patch.object(r.fcntl,'flock',side_effect=BlockingIOError),patch.object(r.time,'monotonic',side_effect=[0,1200]):
   self.assertFalse(r.acquire_run_lock(object(),True))
 def test_cleanup_failure_replaces_complete_report(self):
  snapshot=dict(groups=[],window_start=1,window_end=1000,coverage_limited=True,files_scanned=0,matched_entries=0,distinct_groups=0,source='patterns',hosts=[])
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot,{'data':[{'id':r.MODEL}]}]
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',return_value=1000),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=SimpleNamespace(free=2**32)),patch.object(r.subprocess,'run',return_value=SimpleNamespace(returncode=1)),patch.object(r,'stop_model',side_effect=RuntimeError('cleanup failed')):
   with self.assertRaises(RuntimeError):r.main()
   report=json.loads((Path(d)/'reports/latest.json').read_text())
   self.assertEqual(report['status'],'failed');self.assertEqual(report['error'],'cleanup_failed')
 def test_report_permissions_and_retention(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)):
   folder=Path(d)/'reports';folder.mkdir()
   for i in range(31):(folder/f'report-20000101T{i:06}.json').write_text('{}')
   r.save({'status':'complete'},final=True)
   self.assertEqual(len(list(folder.glob('report-*.json'))),30);self.assertEqual((folder/'latest.json').stat().st_mode&0o777,0o600)

class RegressionTests(unittest.TestCase):
 def test_fixtures_match_and_fit_request_body(self):
  events=g.load_events();expected=g.load_expected()
  self.assertEqual(len(events),15)
  self.assertEqual({e['id'] for e in events},set(expected))
  for e in events:
   grp=e['group']
   self.assertEqual(grp['id'],e['id'])
   self.assertIn(grp['level'],('CRITICAL','WARNING'))
   self.assertEqual(len(grp['hours']),24)
   self.assertLessEqual(grp['first_at'],grp['last_at'])
   self.assertIsInstance(grp['context'],list);self.assertIsInstance(grp['current_service_state'],dict)
   entry=expected[e['id']]
   self.assertTrue(entry['acceptable_verdicts'] and entry['key_phrases'])
   for rx in [*entry['key_phrases'],*entry.get('forbidden_phrases',[]),*entry.get('cap1_phrases',[])]:re.compile(rx)
   body=r.request_body(grp)  # goes through as live
   payload=json.loads(body['messages'][-1]['content'])
   for key in ('host','message','count','hours','context','current_service_state'):self.assertIn(key,payload)
   self.assertIsInstance(payload['first_at'],str);self.assertEqual(body['seed'],42)
 def test_rubric_scoring_rule(self):
  f=lambda verdict,what,next='':dict(verdict=verdict,interpretation=what,next=next)
  e=g.load_expected()
  # 2: acceptable verdict + key fact, nothing invented
  self.assertEqual(g.score(e['h5_smart_pending'],f('act','disk2 (sdc) SMART attribute 197 Current_Pending_Sector raw value rising 0 to 2 to 5 to 8 with I/O read errors','back up the array and plan to replace the disk')),2)
  self.assertEqual(g.score(e['h8_ssh_flood'],f('act','412 failed password attempts for root from 192.168.0.157 in one hour','identify what device .157 is')),2)
  self.assertEqual(g.score(e['h13_watchdog_ok'],f('ignore','the watchdog killed homelab-dashboard and systemd restarted it; it recovered with 200 OK 30 s later')),2)
  # 1: verdict acceptable but key missing
  self.assertEqual(g.score(e['h5_smart_pending'],f('act','a SMART attribute changed on disk2','check it')),1)
  # 1: key right but verdict off by one step
  self.assertEqual(g.score(e['h5_smart_pending'],f('watch','pending sectors on sdc are rising 0 to 2 to 5 to 8, a failing-disk signal','plan a replacement')),1)
  # 0: verdict two steps off
  self.assertEqual(g.score(e['h5_smart_pending'],f('ignore','a SMART attribute changed once, probably harmless')),0)
  self.assertEqual(g.score(e['h13_watchdog_ok'],f('act','the dashboard keeps crashing, investigate it now')),0)
  # 0: forbidden phrase (trap/invented fact), even with verdict and key present
  self.assertEqual(g.score(e['h4_oom_lxc'],f('act','the host ran out of memory and the kernel killed a process','add RAM')),0)
  self.assertEqual(g.score(e['h6_jellyfin_cancel'],f('ignore','files were corrupt so the scan failed')),0)
  # 0: trap verdict the rubric calls out explicitly
  self.assertEqual(g.score(e['h1_dl_404'],f('ignore','status=3 means the download was stopped like a 143 Stop')),0)
  self.assertEqual(g.score(e['h12_wifi_flap'],f('ignore','brief radio flaps happen on channel changes, harmless')),0)
  # 1: wrong framing the rubric caps at 1 (hardware call, key still there)
  self.assertEqual(g.score(e['h7_ext4'],f('act','ext4 errors on dm-7: the disk is failing, replace it')),1)
  self.assertEqual(g.score(e['h7_ext4'],f('act','ext4 corruption on dm-7 (LXC 113 memos disk); run fsck and restore from backup')),2)
  # 0: h15 - mentioning only the harmless download stop misses the NVMe (key_required)
  self.assertEqual(g.score(e['h15_dl_stop_plus'],f('watch','exit 143 is a user Stop of the download, harmless')),0)
  self.assertEqual(g.score(e['h15_dl_stop_plus'],f('watch','the Stop is harmless but nvme0 I/O timeouts and a controller reset right after need checking','run an NVMe health/SMART check')),2)
  # h11: watch with the key = one step off = 1 ("watch without urgency")
  self.assertEqual(g.score(e['h11_cert'],f('watch','the cert expires in about 47 hours and renewal is rate-limited (429)','retry the renewal tomorrow')),1)
  # a failed run (no finding) still scores through verdict=''
  self.assertEqual(g.score(e['h5_smart_pending'],f('','')),0)
 def test_blocked_windows(self):
  self.assertTrue(g.blocked_now(datetime(2026,11,13,2,45)))
  self.assertFalse(g.blocked_now(datetime(2026,11,13,2,44)))
  self.assertFalse(g.blocked_now(datetime(2026,11,13,4,0)))
  self.assertTrue(g.blocked_now(datetime(2026,11,13,6,45)))
  self.assertTrue(g.blocked_now(datetime(2026,11,13,7,14)))
  self.assertFalse(g.blocked_now(datetime(2026,11,13,7,15)))
  self.assertFalse(g.blocked_now(datetime(2026,11,13,13,28)))

if __name__=='__main__':unittest.main()
