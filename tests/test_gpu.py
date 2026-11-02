import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from nightly import gpu_probe as p
import gpu_monitor as m
import storage_monitor as storage


class GpuTests(unittest.TestCase):
    def test_adjacent_json_samples_use_last_valid_interval(self):
        first={'period':{'duration':20},'engines':{'Compute':{'busy':99}}}
        last={'period':{'duration':1005},'engines':{'Compute':{'busy':0},'Video':{'busy':37.5}},'frequency':{'actual':0}}
        result=p.engine_reading('[\n'+json.dumps(first)+'\n'+json.dumps(last)+']')
        self.assertEqual(result['compute_pct'],0);self.assertEqual(result['video_pct'],37.5)
        self.assertIsNone(result['render_pct']);self.assertEqual(result['frequency_mhz'],0)
        with self.assertRaises(ValueError):p.engine_reading(json.dumps(first))
    def test_invalid_engine_percent_is_unknown(self):
        text=json.dumps({'period':{'duration':1000},'engines':{'Compute':{'busy':101},'Video':{'busy':-2}}})
        self.assertIsNone(p.engine_reading(text)['compute_pct']);self.assertIsNone(p.engine_reading(text)['video_pct'])
    def test_vram_reads_local_not_system_memory(self):
        text='system-ttm total:100000 bytes\nlocal0: use_type: 1\nchunk_size: 4KiB, total: 4048MiB, free: 4030MiB, clear_free: 0MiB'
        self.assertEqual(p.memory_reading(text),{'vram_total_mib':4048,'vram_used_mib':18})
        self.assertEqual(p.memory_reading(text.replace('4030MiB','5000MiB')), {})
        self.assertEqual(p.memory_reading('total: 4048MiB, free: 4030MiB'), {})
    def test_stale_future_and_wrong_device_are_unknown(self):
        value=dict(online=True,pci=p.PCI,observed_at=1000,compute_pct=0,vram_used_mib=18)
        self.assertEqual(m.normalized(value,1000)['compute_pct'],0)
        for bad in [dict(value,observed_at=969),dict(value,observed_at=1001),dict(value,pci='0000:00:02.0'),{}]:
            result=m.normalized(bad,1000);self.assertFalse(result['online']);self.assertIsNone(result['compute_pct'])
    def test_history_persists_zero_and_outage_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'gpu.db'
            connect=lambda:sqlite3.connect(path)
            m.initialize(connect)
            value=dict(online=True,pci=p.PCI,observed_at=1000,compute_pct=0,vram_used_mib=18)
            m.collect(connect,value,1000);m.collect(connect,None,1010)
            with patch.object(m.time,'time',return_value=1020):result=m.snapshot(connect,900,10,storage.aggregate_history)
            self.assertEqual(result['history'][0]['compute_pct'],0)
            self.assertIsNone(result['history'][1]['compute_pct']);self.assertFalse(result['latest']['online'])
            m.collect(connect,dict(value,observed_at=100000),100000)
            with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM gpu_samples').fetchone()[0],1)

if __name__=='__main__':unittest.main()
