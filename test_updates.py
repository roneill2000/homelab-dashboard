"""Update runner and proxy regressions; nothing here touches a real host."""
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / 'updates'))
import homelab_update as u
import update_proxy as proxy
from backend_patch import patch_updates


class HelperTests(unittest.TestCase):
    def test_order_puts_dns_dashboard_and_glances_last(self):
        ids = ['lxc:104', 'lxc:106', 'docker:Glances', 'lxc:103', 'docker:Jellyfin']
        self.assertEqual([i['id'] for i in u.order([{'id': x} for x in ids])],
                         ['docker:Jellyfin', 'lxc:103', 'lxc:106', 'docker:Glances', 'lxc:104'])

    def test_parsers(self):
        self.assertEqual(u.parse_pct_list('VMID Status Lock Name\n103 running kasm\n110 stopped   win\n'),
                         [dict(vmid=103, status='running', name='kasm'), dict(vmid=110, status='stopped', name='win')])
        self.assertEqual(u.parse_docker_ps('Minecraft|itzg/minecraft-server|running\nJ|ghcr.io/a/b:1.2|exited')[0]['image'],
                         'itzg/minecraft-server:latest')

    def test_docker_pending_unknown_without_digests(self):
        status = {'a:latest': dict(local='sha256:1', remote='sha256:2'), 'b:latest': dict(local='sha256:1', remote='sha256:1'),
                  'c:latest': dict(local='sha256:1', remote=None)}
        self.assertEqual([u.docker_pending(status, i) for i in ('a:latest', 'b:latest', 'c:latest', 'd:latest')],
                         [True, False, None, None])

    def test_old_snapshots_keep_newest_only(self):
        listing = '`-> preupd_202610190300 ...\n `-> preupd_202610201300 ...\n `-> manual_before_x\n current'
        self.assertEqual(u.old_snapshots(listing, 'preupd_202610201300'), ['preupd_202610190300'])


class HealthParseTests(unittest.TestCase):
    def test_probe_output(self):
        self.assertEqual(u.parse_guest_health('U\nM stale /mnt/UNRAID\nM ok /mnt/NAS\nM missing /mnt/x y'),
                         dict(failed_units=[], mounts=[dict(path='/mnt/UNRAID', state='stale'), dict(path='/mnt/NAS', state='ok'),
                                                        dict(path='/mnt/x y', state='missing')]))
        self.assertEqual(u.parse_guest_health('U tmp.mount a.timer')['failed_units'], ['tmp.mount', 'a.timer'])
        self.assertIsNone(u.parse_guest_health('garbage')['failed_units'])


class DockerRollbackTests(unittest.TestCase):
    def test_pull_happens_before_update_container_so_old_image_survives(self):
        calls = []
        def fake(cmd, timeout=120, check=False):
            calls.append(cmd)
            if cmd.startswith("docker inspect -f '{{.Image}}'"):
                return 0, 'sha256:old\n'
            if cmd.startswith("docker inspect"):
                return 0, 'true||sha256:new'
            return 0, ''
        run = SimpleNamespace(save=lambda: None, log=lambda t: None)
        item = dict(id='docker:Jellyfin', name='Jellyfin', kind='Docker', image='jellyfin/jellyfin:latest')
        with patch.object(u, 'unraid', side_effect=fake):
            u.update_docker(run, item)
        i_tag = next(i for i, c in enumerate(calls) if c.startswith('docker tag sha256:old homelab-rollback/jellyfin:pre-update'))
        i_pull = next(i for i, c in enumerate(calls) if c.startswith('docker pull jellyfin/jellyfin:latest'))
        i_update = next(i for i, c in enumerate(calls) if 'update_container' in c)
        self.assertLess(i_tag, i_pull); self.assertLess(i_pull, i_update)
        self.assertEqual(item['detail'], 'Updated · previous image kept')

    def test_failed_pull_stops_before_recreate(self):
        def fake(cmd, timeout=120, check=False):
            if cmd.startswith('docker pull'): return 1, 'denied'
            return 0, 'sha256:old'
        item = dict(id='docker:X', name='X', kind='Docker', image='x:latest')
        with patch.object(u, 'unraid', side_effect=fake) as un:
            with self.assertRaises(RuntimeError):
                u.update_docker(SimpleNamespace(save=lambda: None, log=lambda t: None), item)
        self.assertFalse(any('update_container' in c.args[0] for c in un.call_args_list))


class WakeTests(unittest.TestCase):
    def test_magic_packet_and_guard(self):
        sent = []
        class Sock:
            def __init__(self, *a): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def setsockopt(self, *a): pass
            def sendto(self, data, addr): sent.append((data, addr))
        with patch.object(proxy.socket, 'socket', Sock):
            proxy.wake_pc()
        self.assertEqual(len(sent[0][0]), 102)
        self.assertEqual(sent[0][0][:6], b'\xff' * 6)
        self.assertEqual(sent[0][0][6:12], bytes.fromhex('345a6001a19a'))
        codes = []
        h = SimpleNamespace(path='/api/action/wake-pc', headers={'Host': '192.168.0.2'}, wfile=io.BytesIO(),
                            send_response=codes.append, send_header=lambda *a: None, end_headers=lambda: None)
        with patch.object(proxy, 'wake_pc') as w:
            self.assertTrue(proxy.handle(h, post=True))
        self.assertEqual(codes, [403]); w.assert_not_called()


class SleepTests(unittest.TestCase):
    def post(self, headers, body=b'{}'):
        codes = []
        h = SimpleNamespace(path='/api/action/sleep-pc', headers={'Content-Length': str(len(body)), **headers}, rfile=io.BytesIO(body),
                            wfile=io.BytesIO(), send_response=codes.append, send_header=lambda *a: None, end_headers=lambda: None)
        with patch.object(proxy, 'sleep_pc', return_value={'ok': True}) as s:
            self.assertTrue(proxy.handle(h, post=True))
        return codes, s

    def test_guard_and_force_flag(self):
        codes, s = self.post({'Host': '192.168.0.2'}); self.assertEqual(codes, [403]); s.assert_not_called()
        ok = {'Host': '192.168.0.2', 'X-Dashboard-Action': '1'}
        codes, s = self.post(ok); self.assertEqual(codes, [200]); s.assert_called_once_with(force=False)
        codes, s = self.post(ok, b'{"force": true}'); s.assert_called_once_with(force=True)
        codes, s = self.post(ok, b'{"force": "yes"}'); s.assert_called_once_with(force=False)


class FleetCacheTests(unittest.TestCase):
    def test_answers_from_cache_and_refreshes_in_background(self):
        import threading, time as t
        proxy.FLEET.clear(); proxy.FLEET.update(at=0, body=None)
        gate = threading.Event(); calls = []
        def remote(cmd, timeout=15):
            calls.append(cmd)
            if len(calls) > 1: gate.wait(2)
            return {'n': len(calls)}
        with patch.object(proxy, 'remote', side_effect=remote):
            self.assertEqual(proxy.fleet()['n'], 1)                 # first call waits for the probe
            self.assertEqual(proxy.fleet()['n'], 1); self.assertEqual(len(calls), 1)  # fresh: cached
            proxy.FLEET['at'] -= 120
            started = t.time(); old = proxy.fleet()
            self.assertEqual(old['n'], 1); self.assertLess(t.time() - started, 0.5)   # stale: old copy at once
            self.assertEqual(old['checked_at'], int(proxy.FLEET['at']))
            proxy.fleet(); gate.set()
            for _ in range(50):
                if proxy.FLEET['body']['n'] == 2: break
                t.sleep(0.02)
        self.assertEqual(len(calls), 2)                              # one background refresh, not one per request
        self.assertEqual(proxy.FLEET['body']['n'], 2)


class FleetLoopTests(unittest.TestCase):
    def test_one_cycle_refreshes_fleet_and_events_then_sleeps(self):
        class Stop(Exception): pass
        proxy.FLEET.clear(); proxy.FLEET.update(at=0, body=None); proxy.EVENTS.clear(); proxy.EVENTS.update(at=0, body=None)
        calls, naps = [], []
        def sleep(s): naps.append(s); raise Stop
        with patch.object(proxy, 'remote', side_effect=lambda cmd, timeout=15: calls.append(cmd) or {'cloud_backup': {'running': False}}), \
             patch.object(proxy.time, 'sleep', side_effect=sleep), \
             patch.object(proxy.threading, 'Thread') as thread:
            with self.assertRaises(Stop): proxy._fleet_loop()
        self.assertEqual(calls, ['health'])
        self.assertEqual(proxy.FLEET['body'], {'cloud_backup': {'running': False}})
        thread.assert_called_once()                      # events refresh handed to a background thread
        self.assertEqual(naps, [50])

    def test_starts_once_and_only_on_the_dashboard_host(self):
        proxy._LOOP['started'] = False
        with patch.object(proxy.threading, 'Thread') as thread, patch('os.path.exists', return_value=False):
            self.assertFalse(proxy.start_fleet_loop())
        with patch.object(proxy.threading, 'Thread') as thread, patch('os.path.exists', return_value=True):
            self.assertTrue(proxy.start_fleet_loop()); self.assertFalse(proxy.start_fleet_loop())
        thread.assert_called_once()
        proxy._LOOP['started'] = False

class PiTests(unittest.TestCase):
    def test_parse_pi_extra(self):
        info = u.parse_pi_extra('C 123\nS 1.15 1.09 1.04|484|981|51092|5|600000|Ubuntu 26.04.1 LTS|6.1.31-sun50iw9|active|4\n'
                                'D uptime-kuma|running|Up 6 days (healthy)|louislam/uptime-kuma:2.5.0\nD nebula-sync|exited|Exited (1)|x')
        self.assertEqual(info['ram_pct'], 49.3); self.assertEqual(info['temp_c'], 51.1); self.assertEqual(info['disk_pct'], 5)
        self.assertEqual(info['pihole'], 'active'); self.assertEqual(info['cpu'], 12.3); self.assertEqual(info['load'], [1.15, 1.09, 1.04])
        self.assertEqual([c['state'] for c in info['containers']], ['running', 'exited'])

    def test_pi_update_refused_when_primary_dns_down(self):
        run = SimpleNamespace(save=lambda: None, log=lambda t: None)
        with patch.object(u, 'sh', return_value=(9, 'timed out')), patch.object(u, 'pi') as pi:
            with self.assertRaises(RuntimeError):
                u.update_pi(run, dict(id=u.PI_ID, name='orangepi', kind='Pi'))
            pi.assert_not_called()


class SshGateTests(unittest.TestCase):
    def test_only_three_commands_reach_the_runner(self):
        for cmd in ('run-update', 'status; rm -rf /', 'bash', '', 'health; id'):
            with patch.dict('os.environ', {'SSH_ORIGINAL_COMMAND': cmd}), patch.object(u, 'spawn') as spawn, \
                 patch.object(u, 'run_job') as job, patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(u.main(['x', 'ssh']), 2)
                spawn.assert_not_called(); job.assert_not_called()
        with patch.dict('os.environ', {'SSH_ORIGINAL_COMMAND': 'start'}), \
             patch.object(u, 'spawn', return_value={'ok': True}) as spawn, patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(u.main(['x', 'ssh']), 0)
            spawn.assert_called_once_with('update')


class RunTests(unittest.TestCase):
    def run_with(self, items, update):
        run = SimpleNamespace(state={'items': items}, save=lambda: None, log=lambda t: None)
        with patch.object(u, 'busy_reasons', return_value=[]), patch.object(u, 'run_check'), \
             patch.object(u, 'update_lxc', side_effect=update), patch.object(u, 'update_docker', side_effect=update):
            try:
                u.run_update(run)
            except Exception as exc:
                return exc
        return None

    def test_first_failure_stops_the_rest_with_rollback_hint(self):
        items = [dict(id='lxc:103', vmid=103, name='kasm', kind='LXC', state='pending'),
                 dict(id='lxc:105', vmid=105, name='speed', kind='LXC', state='pending'),
                 dict(id='lxc:113', vmid=113, name='memos', kind='LXC', state='current')]
        def update(run, item):
            item['snapshot'] = 'preupd_202610201300'
            raise RuntimeError('apt-get upgrade exited 100')
        self.assertIsInstance(self.run_with(items, update), RuntimeError)
        self.assertEqual(items[0]['state'], 'failed')
        self.assertEqual(items[0]['rollback'], 'Roll back: pct rollback 103 preupd_202610201300')
        self.assertEqual(items[1]['state'], 'not started')
        self.assertEqual(items[2]['state'], 'current')

    def test_busy_host_refuses_before_any_change(self):
        run = SimpleNamespace(state={'items': []}, save=lambda: None, log=lambda t: None)
        with patch.object(u, 'busy_reasons', return_value=['Qwen is running']), patch.object(u, 'run_check') as check:
            with self.assertRaises(u.Refused):
                u.run_update(run)
            check.assert_not_called()


class ProxyTests(unittest.TestCase):
    def call(self, headers, body=b'{"action":"start"}'):
        codes = []
        h = SimpleNamespace(path='/api/updates', headers={'Content-Length': str(len(body)), **headers},
                            rfile=io.BytesIO(body), wfile=io.BytesIO(), connection=SimpleNamespace(settimeout=lambda t: None),
                            send_response=codes.append, send_header=lambda *a: None, end_headers=lambda: None)
        with patch.object(proxy, 'remote', return_value={'ok': True}) as remote:
            self.assertTrue(proxy.handle(h, post=True))
        return codes, remote

    def test_cross_site_and_bad_actions_never_reach_proxmox(self):
        for headers in ({'Host': '192.168.0.2'}, {'Host': '192.168.0.2', 'X-Dashboard-Action': '1', 'Origin': 'http://evil.example'}):
            codes, remote = self.call(headers)
            self.assertEqual(codes, [403]); remote.assert_not_called()
        codes, remote = self.call({'Host': '192.168.0.2', 'X-Dashboard-Action': '1'}, b'{"action":"run-update"}')
        self.assertEqual(codes, [400]); remote.assert_not_called()
        codes, remote = self.call({'Host': '192.168.0.2', 'X-Dashboard-Action': '1', 'Origin': 'http://192.168.0.2'})
        self.assertEqual(codes, [200]); remote.assert_called_once_with('start')

    def test_route_patch_is_idempotent(self):
        src = ('class DashboardHandler:\n    def do_POST(self):\n        import qwen_proxy\n        if qwen_proxy.handle(self, post=True): return\n'
               '    def do_GET(self):\n        import qwen_proxy\n        if qwen_proxy.handle(self, post=False): return\n')
        once = patch_updates(src)
        self.assertEqual(patch_updates(once), once)
        self.assertEqual(once.count('update_proxy.handle'), 2)


class WeeklyTests(unittest.TestCase):
    def test_weekly_summary_done_and_skipped(self):
        state = {'items': [{'name': 'memos', 'state': 'done'},
                           {'name': 'kasm', 'state': 'skipped', 'detail': 'Excluded from automatic updates; update by hand'}]}
        title, body = u.weekly_summary(state)
        self.assertEqual(title, 'Weekly updates done')
        self.assertIn('Updated: memos', body)
        self.assertIn('Left for you: kasm', body)

    def test_weekly_summary_stopped_on_failure(self):
        state = {'items': [{'name': 'memos', 'state': 'done'},
                           {'name': 'kasm', 'state': 'failed'}]}
        title, body = u.weekly_summary(state)
        self.assertEqual(title, 'Weekly updates stopped after a failure')

    def test_run_weekly_cloud_backup_busy_skips(self):
        with patch.object(u, 'cloud_backup_busy', return_value=True),              patch.object(u, 'ntfy') as ntfy,              patch.object(u, 'run_update') as run_update:
            u.run_weekly()
            ntfy.assert_called_once_with('Weekly updates skipped',
                                         'The cloud backup or restore test was running. Next try: next Saturday.')
            run_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
