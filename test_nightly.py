from datetime import datetime,timezone
from pathlib import Path
import json,re,tempfile,unittest
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
 def test_failure_stops_owned_container_and_saves_failure(self):
  snapshot=dict(groups=[],window_start=1,window_end=1000,coverage_limited=True,files_scanned=0,matched_entries=0,distinct_groups=0,source='patterns',hosts=[])
  values=[{'activity':{'recovery':{'backup':{'running':False,'observed_at':1000}}}},snapshot]
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)),patch('sys.argv',['runner']),patch.object(r.time,'time',return_value=1000),patch.object(r,'fetch',side_effect=values),patch.object(r,'measured_services',return_value={}),patch.object(r.shutil,'disk_usage',return_value=type('Space',(),{'free':2**32})()),patch.object(r.subprocess,'run') as run:
   run.side_effect=[type('Result',(),{'returncode':1})(),RuntimeError('launch failure'),None]
   with self.assertRaises(RuntimeError):r.main()
   saved=json.loads((Path(d)/'reports/latest.json').read_text())
   self.assertEqual(saved['status'],'failed');self.assertEqual(run.call_args.args[0][:2],['docker','stop'])
 def test_report_permissions_and_retention(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'ROOT',Path(d)):
   folder=Path(d)/'reports';folder.mkdir()
   for i in range(31):(folder/f'report-20000101T{i:06}.json').write_text('{}')
   r.save({'status':'complete'},final=True)
   self.assertEqual(len(list(folder.glob('report-*.json'))),30);self.assertEqual((folder/'latest.json').stat().st_mode&0o777,0o600)

if __name__=='__main__':unittest.main()
