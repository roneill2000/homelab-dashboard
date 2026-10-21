import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import homelab_alerts as a

class AlertTests(unittest.TestCase):
    def test_two_bad_runs_then_one_alert_then_recovery(self):
        cur = {'x': (True, 'T', 'd', 4)}
        s1, st = a.transitions({}, cur); self.assertEqual(s1, [])
        s2, st = a.transitions(st, cur); self.assertEqual([x[1] for x in s2], ['down'])
        s3, st = a.transitions(st, cur); self.assertEqual(s3, [])
        s4, st = a.transitions(st, {'x': (False, 'T', 'd', 4)}); self.assertEqual([x[1] for x in s4], ['up'])

    def test_failed_update_alerts_immediately_and_missing_data_is_not_recovery(self):
        s, st = a.transitions({}, {'updates-failed': (True, 'U', 'd', 5)}); self.assertEqual(len(s), 1)
        s, st = a.transitions(st, {}); self.assertEqual(s, []); self.assertTrue(st['updates-failed']['alerted'])

    def test_checks_skip_intentional_and_flag_real_problems(self):
        f = {'guests': [{'vmid': 102, 'name': 'utilities', 'failed_units': [], 'mounts': [{'path': '/mnt/UNRAID', 'state': 'stale'}]}],
             'containers': [{'name': 'zurg', 'state': 'exited'}, {'name': 'Jellyfin', 'state': 'exited'}],
             'updates': {'failed': [{'name': 'kasm', 'detail': 'apt exited 100', 'rollback': 'pct rollback 103 x'}]}}
        c = a.checks(None, None, f, 0)
        self.assertNotIn('docker:zurg', c); self.assertTrue(c['docker:Jellyfin'][0])
        self.assertTrue(c['mount:utilities:/mnt/UNRAID'][0]); self.assertIn('pct rollback', c['updates-failed'][2])

if __name__ == '__main__':
    unittest.main()
