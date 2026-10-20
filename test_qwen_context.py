import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parent/'nightly'))
import qwen_context as c

class ContextTests(unittest.TestCase):
    def build(self,prompt,t=None,snapshot=None,storage=None):
        return c.build(prompt,storage or {},t or {},snapshot or {},1000,{'disks':[]})
    def test_missing_future_failed_and_stale_sources_hide_values(self):
        for data in ({},{'last_success_at':1001},{'last_success_at':800},{'last_success_at':1000,'error':'failed'}):
            data.update(cpu=99,ram_used_gb=999)
            source=self.build('proxmox',{'pve':data})['sources']['hosts']['pve']
            self.assertFalse(source['fresh']);self.assertNotIn('cpu',source)
    def test_actual_guest_values_survive_nested_projection(self):
        data={'online':True,'last_success_at':1000,'guests':[{'name':'test','status':'running','ram_mb':512,'secret':'no'}]}
        g=self.build('guests',{'pve':data})['sources']['hosts']['pve']['guests'][0]
        self.assertEqual(g['ram_mb'],512);self.assertNotIn('secret',g)
    def test_private_fields_and_log_secrets_excluded(self):
        t={'pve':{'online':True,'last_success_at':1000,'token':'private'},'wan':{'ip':'private-ip'}}
        snap={'generated_at':1000,'groups':[{'host':'test','message':'password=hunter2 error','token':'private'}]}
        evidence=self.build('proxmox logs',t,snap)
        raw=str(evidence)
        for value in ('hunter2','private-ip',"'token':"):self.assertNotIn(value,raw)
        self.assertIn('[redacted]',raw)
    def test_history_gaps_are_not_zero(self):
        result=c.trend([{'ts':1,'read_bps':None},{'ts':2,'read_bps':4}], 'read_bps')
        self.assertEqual(result['read_bps']['mean'],4)
        self.assertEqual(result['read_bps']['samples'],1)
    def test_question_selects_sources(self):
        self.assertIn('logs',c.topics('Why did filebrowser fail?'))
        self.assertIn('downloads',c.topics('Why is SAB slow?'))
        self.assertIn('ai',c.topics('Gemma context and VRAM?'))
    def test_pruning_explicit_and_eventually_terminates(self):
        ev=self.build('proxmox disk network services')
        for i in range(30):
            if not c.reduce_evidence(ev):break
        else:self.fail('pruning did not terminate')
        self.assertTrue(ev['budget_omissions']);self.assertEqual(len(ev['sources']),1)
    def test_no_live_log_read_claim_on_missing_snapshot(self):
        source=self.build('errors')['sources']['logs']
        self.assertFalse(source['fresh']);self.assertNotIn('groups',source)
    def test_snapshot_coverage_is_visible(self):
        source=self.build('errors',snapshot={'generated_at':1000,'coverage_limited':True,'distinct_groups':15,'groups':[]})['sources']['logs']
        self.assertTrue(source['coverage_limited']);self.assertEqual(source['groups_not_included'],15)
    def test_historical_failure_kept_separate_from_measured_recovery(self):
        snap={'generated_at':1000,'groups':[{'host':'utilities','message':'filebrowser.service: failed','last_at':900,'first_at':800}]}
        services={'utilities/filebrowser.service':{'observed_at':1000,'active_since':950,'load':'loaded','active':'active','sub':'running'}}
        ev=c.build('logs',{}, {},snap,1000,{'disks':[]},services)
        group=ev['sources']['logs']['groups'][0]
        check=ev['sources']['live_service_checks']['checks'][0]
        self.assertEqual(check['state'],'running_again')
        self.assertNotIn('current_service',group)
        self.assertEqual(group['last_at'],'1970-01-01T00:15:00+00:00')
    def test_service_recovery_does_not_select_backup_or_match_unraid(self):
        self.assertNotIn('recovery',c.topics('FileBrowser service recovery after warnings on Unraid'))
        self.assertIn('recovery',c.topics('Did the backup succeed?'))
