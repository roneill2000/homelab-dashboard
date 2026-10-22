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
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'webui',return_value={'version':'test'}),patch.object(a,'desktop',return_value={'models':[{'id':'a','state':'loaded'}]}):
   a.collect(1000);self.assertTrue(a.snapshot()['desktop']['online'])
   with patch.object(a,'desktop',side_effect=TimeoutError):a.collect(1031)
   result=a.snapshot()['desktop'];self.assertFalse(result['online']);self.assertEqual(result['last_success_at'],1000);self.assertEqual(result['models'][0]['id'],'a')
   self.assertTrue(a.snapshot()['webui']['online'])
   a.collect(1062);self.assertTrue(a.snapshot()['desktop']['online']);self.assertIsNone(a.snapshot()['desktop']['error']);self.assertEqual(a.snapshot()['desktop']['last_success_at'],1062)
 def test_cold_start_while_pc_is_offline(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'webui',return_value={'version':'test'}),patch.object(a,'desktop',side_effect=TimeoutError):
   a.collect(1000)
   result=a.snapshot();self.assertTrue(result['webui']['online']);self.assertFalse(result['desktop']['online']);self.assertNotIn('models',result['desktop']);self.assertNotIn('last_success_at',result['desktop'])
 def test_poll_is_cached_and_snapshot_cannot_mutate_state(self):
  with patch.object(a,'_latest',{}),patch.object(a,'_last_poll',0),patch.object(a,'webui',return_value={}) as web,patch.object(a,'desktop',return_value={'models':[{'id':'a'}]}) as desk:
   a.collect(1000,min_interval=30);a.collect(1010,min_interval=30);self.assertEqual(desk.call_count,1)
   result=a.snapshot();result['desktop']['models'][0]['id']='changed'
   self.assertEqual(a.snapshot()['desktop']['models'][0]['id'],'a')

if __name__=='__main__':unittest.main()
