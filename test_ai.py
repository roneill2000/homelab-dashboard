import json
import unittest
from unittest.mock import patch
import ai_monitor as a

class AITests(unittest.TestCase):
 def test_context_is_runtime_or_configured_not_training_maximum(self):
  data={'data':[{'id':'large','context_length':262144,'status':{'value':'loaded','args':['--ctx-size','16384'],'preset':'private'},'meta':{'n_ctx':32768}}, {'id':'small','context_length':131072,'status':{'value':'unloaded','args':['--ctx-size','16384','--api-key','do-not-export']}}]}
  items=a.models(data)
  self.assertEqual(items[0]['context_tokens'],32768);self.assertEqual(items[0]['context_source'],'runtime')
  self.assertEqual(items[1]['context_tokens'],16384);self.assertEqual(items[1]['state'],'unloaded')
  self.assertNotIn('do-not-export',json.dumps(items));self.assertNotIn('private',json.dumps(items))
 def test_missing_context_is_unknown_and_bad_shapes_rejected(self):
  self.assertIsNone(a.models({'data':[{'id':'a','context_length':262144}]})[0]['context_tokens'])
  self.assertEqual(a.models({'data':[]}),[])
  for value in ({},{'data':'wrong'},{'data':[{}]}):
   with self.assertRaises(ValueError):a.models(value)
 def test_duplicate_ids_are_not_counted_twice(self):
  self.assertEqual(len(a.models({'data':[{'id':'a'},{'id':'a'}]})),1)
 def test_failure_keeps_last_inventory_but_marks_unavailable(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'strata',return_value={'model':'m','loaded':True}),patch.object(a,'webui',return_value={'version':'test'}),patch.object(a,'desktop',return_value={'models':[{'id':'a','state':'loaded'}]}):
   a.collect(1000);self.assertTrue(a.snapshot()['desktop']['online'])
   with patch.object(a,'desktop',side_effect=TimeoutError):a.collect(1031)
   result=a.snapshot()['desktop'];self.assertFalse(result['online']);self.assertEqual(result['last_success_at'],1000);self.assertEqual(result['models'][0]['id'],'a')
   self.assertTrue(a.snapshot()['webui']['online'])
   a.collect(1062);self.assertTrue(a.snapshot()['desktop']['online']);self.assertIsNone(a.snapshot()['desktop']['error']);self.assertEqual(a.snapshot()['desktop']['last_success_at'],1062)
 def test_cold_start_while_pc_is_offline(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'strata',return_value={'model':'m','loaded':True}),patch.object(a,'webui',return_value={'version':'test'}),patch.object(a,'desktop',side_effect=TimeoutError):
   a.collect(1000)
   result=a.snapshot();self.assertTrue(result['webui']['online']);self.assertFalse(result['desktop']['online']);self.assertNotIn('models',result['desktop']);self.assertNotIn('last_success_at',result['desktop'])
 def test_poll_is_cached_and_snapshot_cannot_mutate_state(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'strata',return_value={'model':'m','loaded':True}),patch.object(a,'webui',return_value={}) as web,patch.object(a,'desktop',return_value={'models':[{'id':'a'}]}) as desk:
   a.collect(1000,min_interval=30);a.collect(1010,min_interval=30);self.assertEqual(desk.call_count,1)
   result=a.snapshot();result['desktop']['models'][0]['id']='changed'
   self.assertEqual(a.snapshot()['desktop']['models'][0]['id'],'a')

 def test_strata_health_is_parsed_and_foreign_services_rejected(self):
  with patch.object(a,'fetch',return_value={'service':'strata','model':'swift-1.5-iq3_s','loaded':True,'max_context':32768,'images':True,'api_key':False}):
   self.assertEqual(a.strata(),{'model':'swift-1.5-iq3_s','loaded':True,'context_tokens':32768,'images':True})
  with patch.object(a,'fetch',return_value={'service':'strata','loaded':'yes','max_context':-1}):
   r=a.strata();self.assertFalse(r['loaded']);self.assertIsNone(r['context_tokens']);self.assertEqual(r['model'],'')
  with patch.object(a,'fetch',return_value={'status':'ok'}):
   with self.assertRaises(ValueError):a.strata()
 def test_strata_offline_does_not_affect_desktop(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'webui',return_value={}),patch.object(a,'desktop',return_value={'models':[]}),patch.object(a,'strata',side_effect=TimeoutError):
   a.collect(1000);r=a.snapshot();self.assertFalse(r['strata']['online']);self.assertTrue(r['desktop']['online'])

if __name__=='__main__':unittest.main()

class SlotPollingTests(unittest.TestCase):
 # GET /slots resets llama.cpp's idle timer, so it may only be polled while watched or every WATCHDOG_INTERVAL.
 def run_desktop(self,t,slots,state='loaded'):
  from unittest.mock import MagicMock
  cfg=MagicMock();cfg.read_text.return_value=json.dumps({'url':'http://192.168.0.12:8080/v1','token':'k'})
  calls=[]
  def fetch(url,token=None):
   calls.append(url)
   if url.endswith('/models'):return {'data':[{'id':'m','status':{'value':state}}]}
   return slots
  with patch.object(a,'CONFIG',cfg),patch.object(a,'fetch',side_effect=fetch),patch('time.time',return_value=t):
   a.desktop()
  return [c for c in calls if '/slots' in c]
 def setUp(self):
  for name,val in (('_live_until',0),('_last_slot_check',0),('_stuck',None),('_watchdog',{}),('_token_samples',[])):
   p=patch.object(a,name,val);p.start();self.addCleanup(p.stop)
 def test_idle_model_is_not_polled_between_watchdog_checks(self):
  idle=[{'id':0,'is_processing':False}]
  self.assertEqual(len(self.run_desktop(10000,idle)),1)          # first check is due
  for t in (10001,10100,10350):self.assertEqual(self.run_desktop(t,idle),[])
  self.assertEqual(len(self.run_desktop(10361,idle)),1)          # next one after 360 s, longer than the 300 s idle timer
 def test_watching_polls_every_time_and_sleeping_model_never(self):
  idle=[{'id':0,'is_processing':False}]
  with patch('time.time',return_value=20000):a.request_live(30)
  for t in (20001,20002,20003):self.assertEqual(len(self.run_desktop(t,idle)),1)
  self.assertTrue(all('autoload=false' in c for c in self.run_desktop(20004,idle)))
  self.assertEqual(self.run_desktop(20005,idle,state='sleeping'),[])
 def test_stuck_generation_unloads_once_and_progress_does_not(self):
  stuck=[{'id':0,'is_processing':True,'id_task':7,'next_token':[{'n_decoded':42}]}]
  with patch.object(a,'unload') as un:
   self.run_desktop(30000,stuck);self.run_desktop(30361,stuck)
   un.assert_called_once_with('http://192.168.0.12:8080','k','m')
   self.assertEqual(a._watchdog['action'],'unloaded')
  with patch.object(a,'unload') as un:
   for i,t in enumerate((40000,40361,40722)):
    self.run_desktop(t,[{'id':0,'is_processing':True,'id_task':8,'next_token':[{'n_decoded':100+i*50}]}])
   un.assert_not_called()

class PcGpuTests(unittest.TestCase):
 # The gaming PC's GPU endpoint is read only while someone is watching, at most every 2 s, never with zeros invented.
 def setUp(self):
  p=patch.object(a,'_pc_gpu',{'latest':None,'history':[],'error':None,'last_try':0});p.start();self.addCleanup(p.stop)
 def test_throttled_sampled_and_capped(self):
  reading={'name':'RTX','power_w':120.5,'temperature_c':61,'utilization_pct':97,'vram_used_mib':15000,'vram_total_mib':16303,'fan_pct':45,'clock_mhz':2700,'bogus':'x','power_limit_w':True}
  with patch.object(a,'fetch',return_value=reading) as f:
   a.pc_gpu_poll(1000);a.pc_gpu_poll(1001);self.assertEqual(f.call_count,1)
   for t in range(1002,1400,2):a.pc_gpu_poll(t)
  latest=a._pc_gpu['latest'];self.assertEqual(latest['power_w'],120.5);self.assertNotIn('bogus',latest);self.assertNotIn('power_limit_w',latest)
  self.assertEqual(len(a._pc_gpu['history']),150)
 def test_failure_keeps_last_reading_and_reports_error(self):
  with patch.object(a,'fetch',return_value={'power_w':80}):a.pc_gpu_poll(2000)
  with patch.object(a,'fetch',side_effect=TimeoutError):a.pc_gpu_poll(2003)
  self.assertEqual(a._pc_gpu['latest']['power_w'],80);self.assertEqual(a._pc_gpu['error'],'GPU metrics unavailable')
  with patch.object(a,'fetch',return_value={'error':'GPU reading failed'}):a.pc_gpu_poll(2006)
  self.assertEqual(a._pc_gpu['error'],'GPU metrics unavailable')
