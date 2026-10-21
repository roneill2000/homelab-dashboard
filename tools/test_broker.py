"""Verify command arguments cannot escape the diagnostic allowlist."""
import importlib.util,json,os,pathlib,tempfile,unittest
from unittest.mock import patch
state=tempfile.TemporaryDirectory()
pathlib.Path(state.name,'token').write_text('unit-test-only')
os.environ['STATE_DIRECTORY']=state.name
spec=importlib.util.spec_from_file_location('broker',pathlib.Path(__file__).with_name('broker.py'))
broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
class Boundaries(unittest.TestCase):
 def test_commands_rejected_before_ssh(self):
  with patch.object(broker.subprocess,'run') as run:
   for action in ['reboot','summary;id','$(id)','cat /etc/shadow','summary\nwhoami','../summary']:
    with self.assertRaises(ValueError): broker.diagnose('proxmox',action)
   for host in ['192.168.0.3','root@192.168.0.3','-oProxyCommand=id']:
    with self.assertRaises(ValueError): broker.diagnose(host,'summary')
   run.assert_not_called()
 def test_allowed_check_uses_fixed_argv(self):
  result=type('Result',(),{'returncode':0,'stdout':'observed output','stderr':''})()
  with patch.object(broker.subprocess,'run',return_value=result) as run:
   self.assertTrue(broker.diagnose('unraid','storage')['ok'])
   args,kwargs=run.call_args
   self.assertEqual(args[0][-2:],['root@192.168.0.4','storage'])
   self.assertNotIn('shell',kwargs)
   self.assertEqual(kwargs['timeout'],20)
 def test_redaction_and_output_bound(self):
  value=broker.redact('password=secret token=abc Authorization: Bearer private\nhttps://user:secret@example.com')
  for secret in ['secret','abc','private','user:']: self.assertNotIn(secret,value)
  self.assertEqual(len(broker.redact('a'*20000)),6000)
 def test_overview_is_compact_and_flags_problems(self):
  tele={'updated_at':0,'pve':{'online':True,'cpu':5},'unraid':{'online':True},'services':{'online':True,'memos':False},
        'pihole':{'status':'protected'},'syslog':{'errors_24h':2,'warnings_24h':9},'scrutiny':{'drives':[{'name':'sdb','healthy':False}]}}
  fleet={'observed_at':0,'guests':[{'name':'utilities','status':'running','failed_units':[],'mounts':[{'path':'/mnt/UNRAID','state':'stale'}]},
         {'name':'kali','status':'stopped'}],'containers':[{'name':'Bench','state':'exited'}],
         'updates':{'pending':[{'name':'kasm'}],'failed':[],'checked_at':1}}
  with patch.object(broker,'fetch',side_effect=lambda p:tele if p=='/api/telemetry' else fleet), patch.object(broker.subprocess,'run') as run:
   o=broker.diagnose('all','overview')
  run.assert_not_called()
  self.assertEqual(o['services_down'],['memos']);self.assertEqual(o['bad_mounts'],['utilities:/mnt/UNRAID stale'])
  self.assertEqual(o['drives_unhealthy'],['sdb']);self.assertEqual(o['docker_not_running'],['Bench']);self.assertEqual(o['updates']['pending'],1)
  self.assertLess(len(json.dumps(o)),6000)
if __name__=='__main__': unittest.main()
