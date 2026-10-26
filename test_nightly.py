from datetime import datetime,timezone
from pathlib import Path
import json,re,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from nightly import log_snapshot as s,runner as r

class SnapshotTests(unittest.TestCase):
 def scan(self,lines,now=200000):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'utilities.log').write_text('\n'.join(lines)+'\n')
   return s.snapshot(Path(d),[re.compile('CRITICAL')],[re.compile('FAILURE')],now)
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
 def test_critical_priority_and_symlink_ignored(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'real.log').write_text(self.line(199900,'CRITICAL')+'\n'+self.line(199999,'FAILURE'))
   (p/'copy.log').symlink_to(p/'real.log')
   data=s.snapshot(p,[re.compile('CRITICAL')],[re.compile('FAILURE')],200000)
  self.assertEqual(data['files_scanned'],1);self.assertEqual(data['groups'][0]['level'],'CRITICAL')

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
 def test_prompt_keeps_recovery_and_counts_out_of_model_input(self):
  group=dict(host='utilities',message='filebrowser.service: FAILURE',count=9000,
             first_at=1,last_at=900,current_service_state={'state':'running_again'},secret='private')
  body=r.request_body(group);text=body['messages'][-1]['content']
  self.assertEqual(json.loads(text),{'host':'utilities','message':'filebrowser.service: FAILURE'})
  self.assertTrue(body['chat_template_kwargs']['enable_thinking'])
  self.assertLessEqual(body['max_tokens'],1152)
  self.assertIn('--reasoning-budget',r.container_command())
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
  self.assertEqual(saved['model_label'],'Gemma 4 E4B · A310')
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

if __name__=='__main__':unittest.main()
