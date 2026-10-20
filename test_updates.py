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


if __name__ == '__main__':
    unittest.main()
