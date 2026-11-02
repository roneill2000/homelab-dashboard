import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import log_monitor as m
import host_probe as h
from backend_patch import patch_log_details,patch_log_groups
from test_log_details import FIXTURE

class GroupTests(unittest.TestCase):
    def event(self,ts,pid=1,code=1,host='utilities'):
        return dict(ts=ts,host=host,level='WARNING',msg=f'2026-10-19T13:02:{int(ts)%60:02}-04:00 {host} systemd[{pid}]: filebrowser.service: Main process exited, status={code}/FAILURE')
    def group(self):return m.group_events([self.event(10)])[0][0]
    def test_repeats_ignore_timestamp_and_pid_but_keep_failure_codes_and_host(self):
        groups,events=m.group_events([self.event(10),self.event(20,9),self.event(30,code=2),self.event(40,host='other')])
        self.assertEqual(len(groups),3)
        repeated=next(g for g in groups if g['count']==2)
        self.assertEqual((repeated['first_at'],repeated['last_at']),(10,20));self.assertEqual(repeated['unit'],'filebrowser.service')
        self.assertNotIn('2026-',repeated['summary']);self.assertIn('status=1',repeated['summary'])
    def test_service_requires_new_start_after_event(self):
        group=self.group()
        def status(active='active',sub='running',since=11,observed=20):
            return m.service_state(group,{'utilities/filebrowser.service':dict(load='loaded',active=active,sub=sub,active_since=since,observed_at=observed)},25)
        self.assertEqual(status()['state'],'running_again')
        self.assertEqual(status(since=9)['state'],'running')
        self.assertEqual(status(active='failed',sub='failed')['state'],'failed')
        self.assertEqual(status(active='activating',sub='auto-restart')['state'],'restarting')
        self.assertEqual(status(active='inactive',sub='dead')['state'],'stopped')
        self.assertEqual(status(observed=-100)['state'],'unverified')
    def test_unknown_services_never_borrow_another_services_health(self):
        g=self.group();g['host']='other'
        self.assertEqual(m.service_state(g,{'utilities/filebrowser.service':dict(load='loaded',active='active',sub='running',observed_at=20)},25)['state'],'unverified')
    def test_window_counts_and_source_failure(self):
        with patch.object(m,'read_json',side_effect=[[self.event(10),self.event(20)],OSError()]):
            result=m.details({'warnings_24h':150})
        self.assertEqual(result['warnings_24h'],150);self.assertEqual(result['log_window']['returned'],2)
        self.assertEqual(result['log_groups'][0]['count'],2);self.assertEqual(result['log_groups'][0]['service_status']['state'],'unverified')
        with patch.object(m,'read_json',side_effect=OSError()):result=m.details({'last_alert':'short...'})
        self.assertIsNone(result['log_groups']);self.assertFalse(result['last_alert_full'])
    def test_probe_clock_staleness_does_not_recover_service(self):
        event=self.event(10)
        probe=dict(sampled_at=1,services={'utilities/filebrowser.service':dict(load='loaded',active='active',sub='running',active_since=11,observed_at=98)})
        with patch.object(m,'read_json',side_effect=[[event],[dict(name='Homelab',result=json.dumps(probe))]]),patch('time.time',return_value=100):
            result=m.details({})
        self.assertEqual(result['log_groups'][0]['service_status']['state'],'unverified')
    def test_invalid_entries_and_bounds(self):
        groups,events=m.group_events([self.event(i+1) for i in range(110)])
        self.assertEqual(len(events),100);self.assertEqual(groups[0]['count'],100)
        self.assertEqual(m.group_events([dict(ts=float('nan'),host='h',level='W',msg='x'),None])[0],[])
    def test_service_probe_uses_fixed_command_and_utc_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(h.subprocess,'check_output',return_value='LoadState=loaded\nActiveState=active\nSubState=running\nActiveEnterTimestamp=Mon 2026-10-19 17:02:59 UTC\n') as command:
            result=h.service_status(Path(tmp)/'cache.json')['utilities/filebrowser.service']
        self.assertEqual(result['active_since'],1792429379)
        self.assertIn('filebrowser.service',command.call_args.args[0]);self.assertEqual(command.call_args.args[0][:4],['pct','exec','102','--'])
    def test_backend_migration_idempotent(self):
        result=patch_log_groups(patch_log_details(FIXTURE))
        self.assertEqual(patch_log_groups(result),result);self.assertIn("sl.get('log_groups')",result)

if __name__=='__main__':unittest.main()
