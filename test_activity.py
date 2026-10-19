import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import activity_monitor as a
import storage_monitor as s
import host_probe as h

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=s.DB;s.DB=self.temp.name+'/metrics.sqlite3';s.initialize()
    def tearDown(self): s.DB=self.old;self.temp.cleanup()
    def reading(self,ts=10):
        return dict(sampled_at=ts,boot_id='boot',disks={'sda':dict(read_ops=10,write_ops=20,read_ms=50,write_ms=100,busy_ms=100,weighted_ms=200)})
    def test_saturation_units_and_idle(self):
        before=self.reading();after=self.reading(20);after['disks']['sda'].update(read_ops=12,write_ops=23,read_ms=250,write_ms=400,busy_ms=5100,weighted_ms=20200)
        self.assertEqual(a.saturation(before,after,'sda'),dict(busy_pct=50,await_ms=100,queue_depth=2))
        self.assertEqual(a.saturation(before,self.reading(20),'sda'),dict(busy_pct=0,await_ms=None,queue_depth=0))
    def test_resets_reboot_missing_and_long_gap_are_unknown(self):
        before=self.reading()
        for after in [None,self.reading(200),self.reading(10),dict(self.reading(20),boot_id='new'),dict(self.reading(20),disks={})]:
            self.assertTrue(all(v is None for v in a.saturation(before,after,'sda').values()))
        after=self.reading(20);after['disks']['sda']['busy_ms']=0
        self.assertIsNone(a.saturation(before,after,'sda')['busy_pct'])
    def test_kernel_fields_and_unassigned_parity_slot(self):
        d=h.diskstats('8 0 sda 10 0 100 50 20 0 200 100 3 300 400\n')
        self.assertEqual(d['sda']['weighted_ms'],400);self.assertEqual(d['sda']['in_flight'],3)
        root=Path(self.temp.name);(root/'var/local/emhttp').mkdir(parents=True)
        (root/'var/local/emhttp/var.ini').write_text('mdResync="0"\nmdResyncPos="0"\nsbSynced2="1234"\nsbSyncExit="0"\nsbSyncErrs="0"\n')
        (root/'var/local/emhttp/disks.ini').write_text('["parity"]\ndevice="sdb"\nname="parity"\ntype="Parity"\nstatus="DISK_OK"\n["parity2"]\ndevice=""\ntype="Parity"\nstatus="DISK_NP_DSBL"\n')
        p=h.parity(root);self.assertEqual(len(p['assigned_disks']),1);self.assertEqual(p['operation'],'idle');self.assertEqual(p['last_finished_at'],1234)
    def test_backup_running_is_separate_from_completed_history(self):
        archive=[dict(starttime=1,endtime=2,status='OK')]
        active=[dict(starttime=3)]
        with patch.object(h.subprocess,'check_output',side_effect=[json.dumps(archive),json.dumps(active)]) as call:
            result=h.backup(Path(self.temp.name)/'backup.json')
        self.assertTrue(result['running']);self.assertEqual(result['last_success']['finished_at'],2)
        self.assertIn('active',call.call_args_list[1].args[0])
    def test_parity_start_edge_and_missing_state(self):
        root=Path(self.temp.name);folder=root/'var/local/emhttp';folder.mkdir(parents=True)
        (folder/'disks.ini').write_text('[disk1]\ndevice="sdc"\ntype="Data"\nstatus="DISK_OK"\n')
        for fields,expected in [('mdResync="1"\nmdResyncPos="0"','running'),('mdResync="0"\nmdResyncPos="1"','paused'),('mdResyncPos="0"','unknown')]:
            (folder/'var.ini').write_text(fields)
            self.assertEqual(h.parity(root)['operation'],expected)
    def test_probe_stale_or_malformed_is_unknown(self):
        p={'sampled_at':100,'disks':{}}
        payload={'amps':[{'name':'Homelab','result':json.dumps(p)}]}
        self.assertIsNotNone(a.probe(payload,120));self.assertIsNone(a.probe(payload,150));self.assertIsNone(a.probe({},100))
    def test_additive_schema_retention_and_gap_preservation(self):
        now=int(time.time())
        with s.connect() as db:
            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?)',('scratch',now,1,2,3,None))
            db.executemany('INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?)',[(now-20,*([0]*8)),(now-10,*([None]*8)),(now,*([10]*8))])
        a.initialize(s.connect)
        snap=a.snapshot(s.connect,900,60,s.aggregate_history)
        self.assertTrue(any(p['sab_bps'] is None for p in snap['history']))
        with s.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM samples').fetchone()[0],1)
    def test_explicit_zero_download_rate_is_numeric(self):
        self.assertTrue(a.numeric(0));self.assertFalse(a.numeric(None));self.assertFalse(a.numeric(float('nan')));self.assertFalse(a.numeric(-1))
    def test_missing_report_is_unavailable_even_when_host_probe_succeeds(self):
        before=copy.deepcopy(a._latest);previous=copy.deepcopy(a._previous)
        try:
            report={'status':'complete','completed_at':90}
            p={'sampled_at':100,'disks':{},'nightly':report}
            disk=dict(host='192.168.0.3',device='sda',read_bps=0,write_bps=0)
            hosts={'192.168.0.4':{'amps':[{'name':'Homelab','result':json.dumps(p)}]}}
            a.collect(s.connect,hosts,{'scratch':disk},100,{})
            self.assertTrue(a._latest['nightly_source_available'])
            del p['nightly'];p['sampled_at']=110
            hosts['192.168.0.4']['amps'][0]['result']=json.dumps(p)
            a.collect(s.connect,hosts,{'scratch':disk},110,{})
            self.assertFalse(a._latest['nightly_source_available'])
            self.assertEqual(a._latest['nightly'],report)
        finally:
            a._latest.clear();a._latest.update(before)
            a._previous.clear();a._previous.update(previous)

if __name__=='__main__': unittest.main()
