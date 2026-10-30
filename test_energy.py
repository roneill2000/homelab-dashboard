import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import energy_monitor as m


def amps(stamp, energy):
    return {'amps': [{'name': 'homelab', 'result': json.dumps({'sampled_at': stamp, 'energy': energy})}]}


def pve(uj, top=262143328850):
    return {'rapl': {'package-0': {'uj': uj, 'max_uj': top}}}


def nas(pkg, dram, gpu, disks):
    return {'rapl': {'package-0': {'uj': pkg, 'max_uj': 10**12}, 'dram': {'uj': dram, 'max_uj': 10**12}}, 'gpu_uj': gpu, 'disks': disks}


DISKS = [dict(name='parity', rotational=True, spundown=False), dict(name='disk1', rotational=True, spundown=True),
         dict(name='cache', rotational=False, spundown=False)]


class EnergyTests(unittest.TestCase):
    def setUp(self):
        m._prev.clear(); m._watts.clear(); m._latest.clear(); m._last_tick = None

    def test_counter_rates_handle_wrap_and_reset(self):
        self.assertEqual(m.rates({'a': (100_000_000, 1000_000_000)}, {'a': (200_000_000, 1000_000_000)}, 10), {'a': 10.0})
        self.assertEqual(m.rates({'a': (990_000_000, 1000_000_000)}, {'a': (90_000_000, 1000_000_000)}, 10), {'a': 10.0})
        self.assertEqual(m.rates({'a': (500, None)}, {'a': (100, None)}, 10), {})  # reset without a known range: unknown
        self.assertEqual(m.rates({'a': (0, None)}, {'a': (10, None)}, 500), {})    # too long a gap
        self.assertEqual(m.rates({'a': (0, None)}, {'a': (10**13, None)}, 10), {})  # implausible jump

    def test_repeated_probe_sample_keeps_last_rate_until_stale(self):
        self.assertIsNone(m.host_watts('pve', {'sampled_at': 100, 'energy': pve(0)}, 100))
        self.assertEqual(m.host_watts('pve', {'sampled_at': 110, 'energy': pve(100_000_000)}, 110), {'package-0': 10.0})
        self.assertEqual(m.host_watts('pve', {'sampled_at': 110, 'energy': pve(100_000_000)}, 120), {'package-0': 10.0})
        self.assertIsNone(m.host_watts('pve', {'sampled_at': 110, 'energy': pve(100_000_000)}, 110 + m.FRESH + 1))

    def test_device_totals_mark_measured_and_estimated_parts(self):
        cfg = m.DEFAULTS
        hosts = {'192.168.0.3': amps(100, pve(0)), '192.168.0.4': amps(100, nas(0, 0, 0, DISKS))}
        m.readings(hosts, None, False, 100, cfg)
        hosts = {'192.168.0.3': amps(110, pve(100_000_000)), '192.168.0.4': amps(110, nas(50_000_000, 10_000_000, 120_000_000, DISKS))}
        devs = {d['id']: d for d in m.readings(hosts, {'power_w': 20.0}, True, 110, cfg)}
        self.assertAlmostEqual(devs['proxmox']['watts'], (10 + 6) / 0.88)
        self.assertEqual(devs['proxmox']['measured_w'], 10.0)
        u = devs['unraid']
        self.assertAlmostEqual(u['measured_w'], 5 + 1 + 12)
        self.assertAlmostEqual(u['watts'], (18 + 6.5 + 0.9 + 1.0 + 10) / 0.88)  # one HDD spinning, one standing by, one SSD
        self.assertTrue(any(p['label'].startswith('Disks (1 of 2') for p in u['parts']))
        self.assertAlmostEqual(devs['pc']['watts'], (20 + 55) / 0.9)
        self.assertEqual(devs['router']['watts'], 12.0)

    def test_unknown_host_is_unknown_and_pc_states(self):
        devs = {d['id']: d for d in m.readings({}, None, False, 100, m.DEFAULTS)}
        self.assertIsNone(devs['proxmox']['watts']); self.assertEqual(devs['proxmox']['state'], 'unknown')
        self.assertEqual(devs['pc']['state'], 'asleep'); self.assertEqual(devs['pc']['watts'], 2.0)
        awake = {d['id']: d for d in m.readings({}, {'error': 'x'}, True, 100, m.DEFAULTS)}['pc']
        self.assertEqual(awake['state'], 'awake'); self.assertEqual(awake['measured_w'], 0)
        self.assertAlmostEqual(awake['watts'], 75 / 0.9)

    def test_config_overrides_and_private_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'energy.json'
            with patch.object(m, 'CONFIG', path):
                self.assertIsNone(m.config()['rate_per_kwh'])
                path.write_text(json.dumps({'rate_per_kwh': 0.36, 'pc': {'rest_awake_w': 40}, 'unknown_key': 1}))
                c = m.config()
                self.assertEqual(c['rate_per_kwh'], 0.36); self.assertEqual(c['pc']['rest_awake_w'], 40)
                self.assertEqual(c['pc']['asleep_w'], 2.0); self.assertNotIn('unknown_key', c)
                path.write_text('not json'); self.assertIsNone(m.config()['rate_per_kwh'])

    def test_hourly_energy_cost_and_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'e.db'
            connect = lambda: sqlite3.connect(path)
            m.initialize(connect)
            cfg = dict(m.DEFAULTS, rate_per_kwh=0.5, fixed=[dict(id='router', name='Router', w=100.0)])
            start = 1_793_000_000 - 1_793_000_000 % 3600
            with patch.object(m, 'config', return_value=cfg):
                for t in range(start, start + 3600 + 1, 10):
                    m.collect(connect, {}, None, False, t)
                m.collect(connect, {}, None, False, start + 3600 + 600)  # a 10-minute gap adds nothing
                snap = m.snapshot(connect, start + 3600 + 600)
            router = connect().execute("SELECT SUM(wh), SUM(seconds) FROM energy_hourly WHERE device='router'").fetchone()
            self.assertAlmostEqual(router[0], 100.0)  # 100 W for exactly one hour
            self.assertAlmostEqual(router[1], 3600)
            pc = connect().execute("SELECT SUM(wh) FROM energy_hourly WHERE device='pc'").fetchone()[0]
            self.assertAlmostEqual(pc, 2.0)  # asleep at 2 W
            self.assertIsNone(connect().execute("SELECT SUM(wh) FROM energy_hourly WHERE device='proxmox'").fetchone()[0])
            self.assertEqual(snap['rate_per_kwh'], 0.5)
            self.assertAlmostEqual(sum(d['kwh'] for d in snap['devices_30d']), 0.102)
            self.assertAlmostEqual(sum(d['cost'] for d in snap['devices_30d']), 0.051)
            self.assertFalse(snap['complete'])  # Proxmox and Unraid have no reading
            self.assertIsNone(snap['month']['projected_kwh'])  # under 6 covered hours

    def test_no_rate_means_no_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            connect = lambda: sqlite3.connect(Path(folder) / 'e.db')
            m.initialize(connect)
            with patch.object(m, 'config', return_value=m.DEFAULTS):
                m.collect(connect, {}, None, False, 1000); m.collect(connect, {}, None, False, 1010)
                snap = m.snapshot(connect, 1010)
            self.assertIsNone(snap['rate_per_kwh']); self.assertIsNone(snap['today']['cost']); self.assertIsNone(snap['per_day_at_now']['cost'])


if __name__ == '__main__':
    unittest.main()
