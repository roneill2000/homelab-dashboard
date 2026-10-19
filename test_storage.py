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
