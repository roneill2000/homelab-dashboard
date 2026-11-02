import unittest
import storage_monitor as m
import cache_guard as g

class CacheTests(unittest.TestCase):
 def test_mover_only_when_available_and_needed(self):
  self.assertTrue(g.should_start(70,False,True,True))
  for args in [(69,False,True,True),(99,True,True,True),(99,False,False,True),(99,False,True,False)]:self.assertFalse(g.should_start(*args))
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

if __name__=='__main__':unittest.main()
