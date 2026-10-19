import ast
import threading
import unittest
from unittest.mock import patch

from backend_patch import patch_backend

FIXTURE = '''
import time
TELEMETRY = {'pve': {}, 'unraid': {}, 'scrutiny': {}, 'media': {'radarr': {'online': True, 'movies': 99}}, 'pihole': {'primary': {}, 'secondary': {}}, 'services': {}}
SECRET = 'fixture-do-not-log-123'
def poll_proxmox():
    try:
        cpu_temp = 62
        nvme_temp = 39
        scratch_pct = 3.0
        try:
            sensors = fetch_sensors()
            for s in sensors:
                cpu_temp = round(float(s.get('value', 62)))
        except Exception:
            pass
        TELEMETRY['pve'] = {'online': True, 'cpu_temp': cpu_temp, 'nvme_temp': nvme_temp, 'scratch_pct': scratch_pct}
    except Exception:
        TELEMETRY['pve']['online'] = False

def poll_unraid_and_scrutiny():
    dev = {}
    TELEMETRY['scrutiny'] = {'online': True, 'drives': [{'healthy': dev.get('device_status', 0) == 0}]}

def poll_dns_and_speed():
    if dns_primary:
        TELEMETRY['pihole']['primary'] = {'online': True, 'blocking': dns_primary['blocking']}
        TELEMETRY['pihole']['blocking'] = dns_primary['blocking']
    else:
        TELEMETRY['pihole']['primary']['online'] = False
    if dns_secondary:
        TELEMETRY['pihole']['secondary'] = {'online': True, 'blocking': dns_secondary['blocking']}
    else:
        TELEMETRY['pihole']['secondary']['online'] = False

def poll_media_and_syslog():
    if media_ok:
        TELEMETRY['media']['radarr'] = {'online': True, 'movies': 3, 'queue': 0}
    else:
        TELEMETRY['media']['radarr']['online'] = False

def poll_network_and_matrix():
    TELEMETRY['services'] = {'router': True}

def action_handler():
    return SECRET
'''


class BackendPatchTests(unittest.TestCase):
    def context(self):
        source = patch_backend(FIXTURE)
        namespace = {'STATE_LOCK': threading.Lock(), 'fetch_sensors': lambda: [], 'media_ok': True}
        exec(compile(source, '<fixture>', 'exec'), namespace)
        return source, namespace

    def test_idempotence_and_unrelated_actions(self):
        source, ctx = self.context()
        self.assertEqual(patch_backend(source), source)
        self.assertTrue(patch_backend('#!/usr/bin/python3\n' + FIXTURE).startswith('#!/usr/bin/python3\n'))
        self.assertEqual(ctx['action_handler'](), 'fixture-do-not-log-123')
        original = ast.parse(FIXTURE)
        updated = ast.parse(source)
        action = lambda t: next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == 'action_handler')
        self.assertEqual(ast.dump(action(original)), ast.dump(action(updated)))

    def test_absent_sensors_and_missing_health_are_unknown(self):
        _, ctx = self.context()
        ctx['poll_proxmox']()
        pve = ctx['TELEMETRY']['pve']
        self.assertIsNone(pve['cpu_temp'])
        self.assertIsNone(pve['nvme_temp'])
        self.assertIsNone(pve['scratch_pct'])
        ctx['fetch_sensors'] = lambda: [{'value': None}]
        ctx['poll_proxmox']()
        self.assertIsNone(ctx['TELEMETRY']['pve']['cpu_temp'])
        ctx['fetch_sensors'] = lambda: [{'value': '73.2'}]
        ctx['poll_proxmox']()
        self.assertEqual(ctx['TELEMETRY']['pve']['cpu_temp'], 73)
        ctx['poll_unraid_and_scrutiny']()
        self.assertIsNone(ctx['TELEMETRY']['scrutiny']['drives'][0]['healthy'])

    def test_failure_preserves_last_success_and_values(self):
        _, ctx = self.context()
        with patch('time.time', return_value=100):
            ctx['poll_media_and_syslog']()
        self.assertNotIn('radarr', ctx['TELEMETRY']['media'])
        first = ctx['TELEMETRY']['media']['readarr']
        self.assertEqual(first['books'], 3)
        self.assertEqual(first['last_success_at'], 100)
        ctx['media_ok'] = False
        with patch('time.time', return_value=200):
            ctx['poll_media_and_syslog']()
        latest = ctx['TELEMETRY']['media']['readarr']
        self.assertEqual(latest['books'], 3)
        self.assertEqual(latest['last_success_at'], 100)
        self.assertEqual(latest['last_attempt_at'], 200)
        self.assertFalse(latest['online'])
        self.assertEqual(latest['error'], 'Source poll failed')

    def test_dns_requires_consistent_available_nodes(self):
        _, ctx = self.context()
        ctx.update(dns_primary={'blocking': True}, dns_secondary={'blocking': False})
        ctx['poll_dns_and_speed']()
        dns = ctx['TELEMETRY']['pihole']
        self.assertIsNone(dns['blocking'])
        self.assertEqual(dns['status'], 'mixed')
        ctx['dns_secondary'] = {'blocking': True}
        with patch('time.time', return_value=100):
            ctx['poll_dns_and_speed']()
        self.assertTrue(ctx['TELEMETRY']['pihole']['blocking'])
        ctx['dns_secondary'] = None
        with patch('time.time', return_value=200):
            ctx['poll_dns_and_speed']()
        dns = ctx['TELEMETRY']['pihole']
        self.assertIsNone(dns['blocking'])
        self.assertFalse(dns['online'])
        self.assertEqual(dns['last_success_at'], 100)
        self.assertEqual(dns['secondary']['last_success_at'], 100)
        self.assertEqual(dns['status'], 'unknown')

    def test_source_shape_drift_fails_before_mutation(self):
        with self.assertRaises(ValueError):
            patch_backend('def poll_proxmox(): pass\n')


if __name__ == '__main__':
    unittest.main()
