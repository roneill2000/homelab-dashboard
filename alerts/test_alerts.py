import json, sys, unittest
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

    def test_digest_lists_backups_and_flags_problems(self):
        now = 1792700000
        fresh = {k: now - 3600 for k in a.SOURCES}
        t = {'unraid': {'cache_pct': 21.1, 'array_pct': 68.8}, 'syslog': {'online': True, 'errors_24h': 0, 'warnings_24h': 2, 'hosts_reporting': 14},
             'speedtest': {'dl': 2342.8, 'ul': 298.0}}
        f = {'updates': {'pending': [{'name': 'kasm'}]}, 'smart': {'disks': [{'role': 'parity', 'attrs': {'pending': 0}, 'running_remaining_pct': 40}]}}
        title, body, problems = a.digest_text(now, t, f, fresh, [])
        self.assertEqual((title, problems), ('Homelab: all good', []))
        for text in ('Backups: Photos', 'cache 21.1%', '2 warnings', '2343 down', 'Updates waiting: 1 (kasm)', 'Self-test running on parity (40% left)'):
            self.assertIn(text, body)
        f['smart']['disks'][0]['attrs'] = {'pending': 3}
        title, body, problems = a.digest_text(now, t, f, dict(fresh, unraid=now - 40 * 3600), ['Orange Pi running hot'])
        self.assertEqual(title, 'Homelab: 3 things to check')
        self.assertTrue(body.startswith('Needs attention: Unraid backup stale; parity disk: 3 pending; Active alert: Orange Pi running hot'))
        self.assertIn('unknown', a.digest_text(now, None, None, None, [])[1])

    def test_smart_checks_failed_test_and_overdue_only_after_settling(self):
        now = 1792700000
        disk = {'device': 'sdb', 'role': 'parity', 'model': 'HGST', 'rotational': True, 'running_remaining_pct': None,
                'last_extended': {'result': 'Completed: read failure', 'at_hours': 100, 'hours_ago': 6500}}
        new = a.smart_checks({'scheduler_installed_at': now - 3600, 'disks': [disk]}, now)
        self.assertTrue(new['smart-test:sdb'][0]); self.assertNotIn('smart-overdue:sdb', new)
        old = a.smart_checks({'scheduler_installed_at': now - 10 * 86400, 'disks': [dict(disk, last_extended=dict(disk['last_extended'], result='Completed without error'))]}, now)
        self.assertFalse(old['smart-test:sdb'][0]); self.assertTrue(old['smart-overdue:sdb'][0])
        self.assertEqual(a.smart_checks(None, now), {})
        launches = {'scheduler_installed_at': now - 10 * 86400, 'launches': {'sdb': {'ok': False, 'kind': 'extended', 'at': now, 'message': 'Device busy'}},
                    'disks': [dict(disk, last_extended=None)]}
        out = a.smart_checks(launches, now)
        self.assertTrue(out['smart-launch:sdb'][0]); self.assertIn('Device busy', out['smart-launch:sdb'][2])
        self.assertTrue(out['smart-overdue:sdb'][0])  # no test ever recorded, after the grace period

    def test_nightly_alerts_only_after_two_failed_nights_and_digest_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h.json'
            self.assertFalse(a.nightly_check({'status': 'failed', 'local_date': '2026-10-22', 'error': 'X'}, h)['nightly-failing'][0])
            self.assertTrue(a.nightly_check({'status': 'failed', 'local_date': '2026-10-23', 'error': 'X'}, h)['nightly-failing'][0])
            self.assertFalse(a.nightly_check({'status': 'complete', 'local_date': '2026-10-24'}, h)['nightly-failing'][0])
            self.assertEqual(a.nightly_check({'status': 'running', 'local_date': '2026-10-25'}, h), {})
        n = {'status': 'complete', 'model_label': 'Gemma 4 12B \u00b7 A310 + RAM', 'findings': [1, 2], 'skipped_groups': [1], 'evidence': {'distinct_groups': 3}, 'fallback_reason': None}
        body = a.digest_text(1792700000, None, None, None, [], n)[1]
        self.assertIn('Nightly review: 3 log groups, 2 explained on Gemma 4 12B, 1 skipped', body)
        title, body, problems = a.digest_text(1792700000, None, None, None, [], {'status': 'failed', 'error': 'TimeoutError'})
        self.assertIn('Nightly log review failed', problems)

    def test_restore_check_recent_snapshots_and_event_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / 'rt.json'
            self.assertEqual(a.restore_test_check(1000, r), {})
            r.write_text(json.dumps({'finished_at': 0, 'checks': [{'label': 'Photos', 'ok': False, 'detail': '18 of 20 match'}, {'label': 'Configs', 'ok': None, 'detail': 'Could not run'}]}))
            out = a.restore_test_check(41 * 86400, r)
            self.assertTrue(out['restore-test'][0]); self.assertIn('Photos: 18 of 20', out['restore-test'][2]); self.assertNotIn('Configs', out['restore-test'][2])
            self.assertTrue(out['restore-overdue'][0])
            ev = Path(d) / 'ev.jsonl'
            a.log_event('Disk hot', 'sdb 60 C', False, 1000, ev)
            self.assertEqual(json.loads(ev.read_text())['title'], 'Disk hot')
        snaps = [{'time': '2026-10-22T08:08:32Z', 'tags': ['unraid']}, {'time': '2026-10-21T08:08:32Z', 'tags': ['unraid']}]
        rec = a.recent_by_tag(snaps)['unraid']
        self.assertEqual(len(rec), 2); self.assertGreater(rec[0], rec[1])

    def test_config_backup_check_and_header_encoding(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'last'
            self.assertEqual(a.config_backup_check(1000, f), {})
            f.write_text('0')
            self.assertTrue(a.config_backup_check(37 * 3600, f)['config-backup'][0])
        self.assertEqual(a.header_text('Plain title'), 'Plain title')
        self.assertTrue(a.header_text('Orange Pi 75 °C · hot').startswith('=?UTF-8?B?'))
        self.assertIn('120 words', a.why_prompt('Disk full', '97% used'))

class SnapshotsDueTests(unittest.TestCase):
    def test_refreshes_after_a_backup_or_while_stale(self):
        from homelab_alerts import snapshots_due
        now = 1_000_000
        fresh = {'checked_at': now - 60, 'latest': {t: now - 3600 for t in ('photos', 'proxmox', 'unraid', 'configs')}}
        self.assertFalse(snapshots_due(fresh, now))
        self.assertTrue(snapshots_due(fresh, now, last_success=now - 10))
        self.assertTrue(snapshots_due(dict(fresh, latest=dict(fresh['latest'], unraid=now - 31 * 3600)), now))
        self.assertTrue(snapshots_due(dict(fresh, checked_at=now - 1800), now))
        self.assertTrue(snapshots_due(None, now))


if __name__ == '__main__':
    unittest.main()
