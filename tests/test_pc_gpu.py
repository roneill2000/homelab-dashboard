import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gpu_monitor as m
import storage_monitor as storage
import energy_monitor


def good_payload(ts=1000):
    return {'name': 'NVIDIA GeForce RTX 5070 Ti', 'observed_at': ts, 'power_w': 152.5, 'power_limit_w': 300,
            'temperature_c': 55, 'utilization_pct': 84, 'memory_util_pct': 10,
            'vram_used_mib': 14670, 'vram_total_mib': 16303, 'clock_mhz': 2100, 'fan_pct': 40}


class PcGpuTable:
    """Fresh temp DB with the pc_gpu_samples schema; a new connection per call, like the real connect()."""

    def __init__(self, folder):
        self.path = str(Path(folder) / 'pcgpu.db')
        m.initialize(self.connect)

    def connect(self):
        return sqlite3.connect(self.path)

    def rows(self):
        with self.connect() as db:
            return db.execute('SELECT * FROM pc_gpu_samples ORDER BY ts').fetchall()


class PcCollectTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.t = PcGpuTable(self.folder.name)
        energy_monitor._pc_cpu.update(prev=None, at=None, j=None, w=None)

    def test_good_payload_stores_one_row(self):
        m.pc_collect(self.t.connect, good_payload(), 1000)
        rows = self.t.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1000)
        row = dict(zip(['ts'] + list(m.PC_METRICS), rows[0]))
        self.assertEqual(row['power_w'], 152.5)
        self.assertEqual(row['utilization_pct'], 84)
        self.assertEqual(row['clock_mhz'], 2100)
        self.assertIsNone(row['cpu_w'])  # first cpu_energy_j read has no window yet

    def test_cpu_w_stored_from_the_energy_difference(self):
        m.pc_collect(self.t.connect, dict(good_payload(), cpu_energy_j=1000.0), 1000)
        m.pc_collect(self.t.connect, dict(good_payload(), cpu_energy_j=1108.516804), 1010)
        rows = self.t.rows()
        self.assertEqual(len(rows), 2)
        row = dict(zip(['ts'] + list(m.PC_METRICS), rows[1]))
        self.assertAlmostEqual(row['cpu_w'], 10.8516804, places=6)

    def test_cpu_reset_gives_no_cpu_w(self):
        m.pc_collect(self.t.connect, dict(good_payload(), cpu_energy_j=1000.0), 1000)
        m.pc_collect(self.t.connect, dict(good_payload(), cpu_energy_j=999.0), 1010)  # reboot: counter restarted
        row = dict(zip(['ts'] + list(m.PC_METRICS), self.t.rows()[1]))
        self.assertIsNone(row['cpu_w'])
        self.assertEqual(row['power_w'], 152.5)  # the GPU columns still stored

    def test_asleep_or_bad_payloads_store_nothing(self):
        for bad in (None, {'error': 'GPU reading failed'}, 'not a dict', [1, 2], {},
                    {'power_w': None, 'temperature_c': None, 'name': 'x'}):
            m.pc_collect(self.t.connect, bad, 1000)
        self.assertEqual(self.t.rows(), [])

    def test_bad_fields_null_good_fields_stay(self):
        m.pc_collect(self.t.connect, dict(power_w=1500.0, temperature_c=55, utilization_pct=101,
                                          memory_util_pct=-5, vram_used_mib=14670), 1000)
        row = dict(zip(['ts'] + list(m.PC_METRICS), self.t.rows()[0]))
        self.assertIsNone(row['power_w'])          # outside 0-1000
        self.assertIsNone(row['utilization_pct'])  # _pct over 100
        self.assertIsNone(row['memory_util_pct'])  # negative
        self.assertEqual(row['temperature_c'], 55)
        self.assertEqual(row['vram_used_mib'], 14670)

    def test_rows_older_than_24h_are_pruned(self):
        m.pc_collect(self.t.connect, good_payload(), 1000)
        m.pc_collect(self.t.connect, good_payload(), 1000 + 86401)
        self.assertEqual([r[0] for r in self.t.rows()], [1000 + 86401])

    def test_snapshot_shape_constants_in_latest_gaps_not_zeros(self):
        value = good_payload()
        m.pc_collect(self.t.connect, value, 1000)
        m.pc_collect(self.t.connect, None, 1060)                 # PC asleep: nothing stored
        m.pc_collect(self.t.connect, value, 1660)                # back up: 10-minute gap
        m.pc_collect(self.t.connect, value, 1670)                # a normal sample after the gap
        with patch.object(m.time, 'time', return_value=1760):
            result = m.pc_snapshot(self.t.connect, 3600, 10, storage.aggregate_history)
        self.assertEqual(set(result), {'latest', 'history', 'history_since', 'bucket_seconds'})
        self.assertEqual(result['bucket_seconds'], 10)
        self.assertEqual(result['history_since'], 1000)
        self.assertEqual(result['latest']['vram_total_mib'], 16303)  # constants live in latest only
        self.assertEqual(result['latest']['power_limit_w'], 300)
        self.assertNotIn('vram_total_mib', m.PC_METRICS)
        self.assertNotIn('power_limit_w', m.PC_METRICS)
        self.assertIn('cpu_w', m.PC_METRICS)
        points = result['history']
        self.assertTrue(points[0]['power_w'])
        self.assertTrue(points[-1]['power_w'])
        # the sleep window shows as marked null points, never zeros
        self.assertTrue(any(p['power_w'] is None for p in points))
        self.assertFalse(any(p['power_w'] == 0 for p in points))


class CollectorIsolationTests(unittest.TestCase):
    """storage_monitor.collect() keeps the energy log running even when the PC GPU collector raises,
    and the /api/storage snapshot survives pc_gpu failing (the panel just shows the empty state)."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = str(Path(self.folder.name) / 'storage.db')
        p = patch.object(storage, 'DB', self.path)
        p.start()
        self.addCleanup(p.stop)
        storage.initialize()

    def test_energy_runs_when_pc_collect_raises(self):
        calls = []
        def energy_collect(connect, hosts, pc_gpu, pc_online, now):
            calls.append(pc_gpu)
        with patch.object(m, 'fetch', lambda: None), \
             patch.object(m, 'pc_collect', side_effect=RuntimeError('disk full')), \
             patch.object(storage.energy_monitor, 'fetch_pc', lambda: {'power_w': 20.0}), \
             patch.object(storage.energy_monitor, 'collect', energy_collect), \
             patch.object(storage.ai_monitor, 'collect', lambda: None), \
             patch.object(storage.activity_monitor, 'sab_rate', side_effect=RuntimeError), \
             patch.object(storage.activity_monitor, 'debrid_rate', return_value=0):
            storage.collect()
        self.assertEqual(calls, [{'power_w': 20.0}])  # energy log still got the PC GPU reading

    def test_snapshot_gives_pc_gpu_none_when_pc_snapshot_raises(self):
        with patch.object(m, 'pc_snapshot', side_effect=sqlite3.OperationalError('no table')):
            snap = storage.snapshot(3600)
        self.assertIsNone(snap['pc_gpu'])
        self.assertEqual(len(snap['disks']), 4)  # /api/storage still answers


if __name__ == '__main__':
    unittest.main()
