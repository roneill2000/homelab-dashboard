import ast
import io
import json
import unittest
from unittest.mock import patch
from backend_patch import LOG_HELPER,patch_log_details

FIXTURE='''
TELEMETRY={}
def task_syslog():
    sl=fetch_json('http://192.168.0.3:8999/api/status')
    with STATE_LOCK:
        TELEMETRY['syslog']={'last_alert':sl.get('last_alert','None')}
def action_handler():
    return 'unchanged'
'''
class LogTests(unittest.TestCase):
    def test_full_entry_selection_and_failure_fallback(self):
        ctx={};exec(LOG_HELPER,ctx);fn=ctx['_dashboard_log_details']
        message='systemd: filebrowser.service: Main process exited, code=exited, status=1/FAILURE'
        events=[dict(ts=10,msg=message,host='utilities',level='WARNING'),dict(ts=1,msg='older')]
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(events).encode())):
            result=fn({'last_alert':'short...'})
        self.assertEqual(result['last_alert'],message);self.assertTrue(result['last_alert_full']);self.assertEqual(result['latest_event']['ts'],10)
        with patch('urllib.request.urlopen',side_effect=OSError):result=fn({'last_alert':'short...'})
        self.assertEqual(result['last_alert'],'short...');self.assertFalse(result['last_alert_full']);self.assertIsNone(result['latest_event'])
    def test_bounded_response(self):
        ctx={};exec(LOG_HELPER,ctx)
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'x'*262145)):
            self.assertFalse(ctx['_dashboard_log_details']({})['last_alert_full'])
    def test_patch_idempotence_and_network_before_lock(self):
        result=patch_log_details(FIXTURE);self.assertEqual(result,patch_log_details(result))
        tree=ast.parse(result);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='task_syslog')
        self.assertEqual(fn.body[1].value.func.id,'_dashboard_log_details');self.assertIsInstance(fn.body[2],ast.With)
        action=lambda source:ast.dump(next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='action_handler'))
        self.assertEqual(action(result),action(FIXTURE))
if __name__=='__main__':unittest.main()
