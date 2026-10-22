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

    def test_duplicates_of_other_senders_are_not_checked(self):
        t = {'updated_at': 0, 'unraid': {'online': True, 'cache_pct': 99, 'array_pct': 99}, 'scrutiny': {'online': True, 'drives': [{'name': 'sdb', 'healthy': False}]}}
        s = {'activity': {'recovery': {'backup': {'latest': {'status': 'error'}}}}}
        c = a.checks(t, s, None, 0)
        for k in ('cache-full', 'array-full', 'drives', 'backup'):
            self.assertNotIn(k, c)

class PiAlertTests(unittest.TestCase):
    def test_pi_checks(self):
        f = {'pi': {'online': True, 'failed_units': [], 'pihole': 'inactive', 'disk_pct': 90, 'temp_c': 50,
                    'containers': [{'name': 'uptime-kuma', 'state': 'exited', 'status': 'Exited (1)'}]}}
        c = a.checks(None, None, f, 0)
        self.assertNotIn('pi-pihole', c); self.assertTrue(c['pi-disk'][0]); self.assertFalse(c['pi-temp'][0])
        self.assertTrue(c['pi-docker:uptime-kuma'][0]); self.assertNotIn('pi-reachable', c)


class CloudBackupTests(unittest.TestCase):
    def test_stale_after_36h_and_silent_before_first_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'last_success'
            self.assertEqual(a.cloud_backup_check(1000, f), {})
            f.write_text('0')
            self.assertFalse(a.cloud_backup_check(35 * 3600, f)['cloud-backup'][0])
            self.assertTrue(a.cloud_backup_check(37 * 3600, f)['cloud-backup'][0])


    def test_unraid_stale_after_30h_and_silent_without_data(self):
        self.assertEqual(a.unraid_backup_check(1000, None), {})
        self.assertEqual(a.unraid_backup_check(1000, {'photos': 1}), {})
        self.assertFalse(a.unraid_backup_check(29 * 3600, {'unraid': 0})['cloud-unraid'][0])
        self.assertTrue(a.unraid_backup_check(31 * 3600, {'unraid': 0})['cloud-unraid'][0])

    def test_latest_by_tag_reads_restic_times(self):
        snaps = [{'time': '2026-10-22T08:08:32.123456789-04:00', 'tags': ['unraid']},
                 {'time': '2026-10-21T23:25:40.5-04:00', 'tags': ['unraid']},
                 {'time': '2026-10-22T06:00:12Z', 'tags': ['photos']}]
        latest = a.latest_by_tag(snaps)
        self.assertEqual(latest['unraid'], 1792670912)
        self.assertEqual(latest['photos'], 1792648812)

    def test_daily_summary_once_after_7am(self):
        import tempfile, time as t
        with tempfile.TemporaryDirectory() as d:
            sent = Path(d) / 'day'
            base = t.mktime((2026, 10, 22, 6, 30, 0, 0, 0, -1))
            fresh = {'photos': base - 3600, 'proxmox': base - 3600, 'unraid': base - 3600}
            self.assertIsNone(a.daily_summary(base, fresh, sent))
            title, body, prio = a.daily_summary(base + 3600, fresh, sent)
            self.assertEqual((title, prio), ('Backups OK', 2))
            self.assertEqual(body.count('\n'), 2)
            sent.write_text(t.strftime('%Y-%m-%d', t.localtime(base)))
            self.assertIsNone(a.daily_summary(base + 3600, fresh, sent))
            sent.unlink()
            title, _, prio = a.daily_summary(base + 3600, dict(fresh, unraid=base - 40 * 3600), sent)
            self.assertEqual((title, prio), ('Backups: Unraid stale', 3))

if __name__ == '__main__':
    unittest.main()
