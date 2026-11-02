import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import json
import host_probe as h

class CoverageTests(unittest.TestCase):
    def guests(self):
        return [dict(vmid=100,name='one',type='lxc',node='n1',status='running'),dict(vmid=101,name='two',type='qemu',node='n2',status='stopped'),dict(vmid=102,name='template',type='qemu',node='n1',template=1)]
    def test_disabled_job_does_not_count_stopped_guest_still_in_scope(self):
        result=h.backup_coverage(self.guests(),[{'enabled':1,'vmid':'100'},{'enabled':0,'vmid':'101'}])
        self.assertEqual((result['covered'],result['total']),(1,2))
        self.assertEqual(result['uncovered'][0]['vmid'],101)
    def test_all_exclusions_and_node_constraints(self):
        result=h.backup_coverage(self.guests(),[{'all':1,'exclude':'101'}])
        self.assertEqual(result['covered'],1)
        result=h.backup_coverage(self.guests(),[{'all':1,'node':'n2'}])
        self.assertEqual(result['uncovered'][0]['vmid'],100)
    def test_multiple_jobs_whitespace_delimiters_and_no_double_count(self):
        result=h.backup_coverage(self.guests(),[{'vmid':'100 101'},{'vmid':'100,101'}])
        self.assertEqual(result['covered'],2);self.assertEqual(result['uncovered'],[])
    def test_empty_jobs_proves_all_uncovered(self):
        self.assertEqual(len(h.backup_coverage(self.guests(),[])['uncovered']),2)
    def test_empty_inventory_and_unknown_selector_never_claim_success(self):
        for guests,jobs in [([],[]),(self.guests(),[{'pool':'unknown'}]),(self.guests(),[{}])]:
            with self.assertRaises(ValueError):h.backup_coverage(guests,jobs)
    def test_job_success_and_failed_coverage_read_are_independent(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(h.subprocess,'check_output',side_effect=[json.dumps([{'endtime':10,'status':'OK'}]),'[]',OSError('no inventory')]):
            result=h.backup(Path(folder)/'cache')
        self.assertEqual(result['latest']['status'],'OK')
        self.assertEqual(result['coverage']['status'],'unknown')
