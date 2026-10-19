import importlib
import os
import tempfile
import unittest
import time
from unittest.mock import patch

import storage_monitor as monitor

class StorageTests(unittest.TestCase):
    def test_cumulative_rates_and_latency(self):
        a = dict(read_bytes_gauge=1000, write_bytes_gauge=3000, read_time_gauge=100, write_time_gauge=200, read_count_gauge=10, write_count_gauge=20)
        b = dict(read_bytes_gauge=2000, write_bytes_gauge=6000, read_time_gauge=104, write_time_gauge=210, read_count_gauge=12, write_count_gauge=22)
        self.assertEqual(monitor.rates(a,b,10),(100,300,3.5))
        self.assertEqual(monitor.rates(b,a,10),(None,None,None))
        self.assertEqual(monitor.rates(a,b,100),(None,None,None))
        self.assertEqual(monitor.rates(None,b,10),(None,None,None))

    def test_unknown_is_not_idle(self):
        self.assertEqual(monitor.rates({}, {}, 10), (None,None,None))
        a = dict(read_bytes_gauge=0,write_bytes_gauge=0)
        self.assertEqual(monitor.rates(a,a,10),(0,0,None))

    def test_aggregation_does_not_average_away_missing_samples(self):
        history = monitor.aggregate_history([
            ('scratch', 100, 0, 100, 25, None),
            ('scratch', 110, None, None, 26, None),
            ('scratch', 120, 0, 300, 27, 4),
        ], 1000)['scratch']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['ts'], 100)
        self.assertEqual(history[-1]['ts'], 120)
        self.assertIsNone(history[0]['read_bps'])
        self.assertIsNone(history[0]['write_bps'])
        self.assertEqual(history[0]['used_pct'], 26)
        self.assertIsNone(history[0]['latency_ms'])
        verified_idle = monitor.aggregate_history([
            ('array', 100, 0, 0, 60, None),
            ('array', 110, 0, 0, 60, None),
        ], 1000)['array'][0]
        self.assertEqual(verified_idle['write_bps'], 0)

    def test_rowless_outages_survive_inside_and_across_buckets(self):
        for bucket in (100, 1000):
            with self.subTest(bucket=bucket):
                points = monitor.aggregate_history([
                    ('scratch', 100, 10, 100, 25, 3),
                    ('scratch', 110, 10, 100, 25, 3),
                    # No rows at all during the process outage.
                    ('scratch', 510, 20, 200, 26, 4),
                    ('scratch', 520, 20, 200, 26, 4),
                ], bucket)['scratch']
                by_time = {point['ts']: point for point in points}
                for stamp in (111, 509):
                    self.assertIn(stamp, by_time)
                    self.assertTrue(all(by_time[stamp][metric] is None for metric in ('read_bps', 'write_bps', 'used_pct', 'latency_ms')))
                self.assertIsNone(by_time[520]['write_bps'])
                self.assertEqual([p['ts'] for p in points], sorted(p['ts'] for p in points))
                self.assertEqual(points[0]['ts'], 100)
                self.assertEqual(points[-1]['ts'], 520)

    def test_persistent_history_and_no_parity_double_count(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(monitor,'DB',folder+'/metrics.sqlite3'):
            monitor.initialize()
            now=int(time.time())
            with monitor.connect() as db:
                db.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?)', [('array',now,100,200,60,None),('parity',now,100,200,None,None),('scratch',now-90000,1,1,20,None)])
            result=monitor.snapshot(3600)
            disks={d['id']:d for d in result['disks']}
            self.assertEqual(disks['array']['history'][0]['write_bps'],200)
            self.assertEqual(disks['parity']['history'][0]['write_bps'],200)
            self.assertEqual(disks['scratch']['history'],[])
            self.assertEqual(monitor.snapshot(3600)['disks'][1]['history'],disks['array']['history'])

if __name__=='__main__':
    unittest.main()
