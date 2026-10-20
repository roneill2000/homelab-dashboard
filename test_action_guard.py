"""Action POSTs need the homepage's custom header; DNS pauses are bounded."""
import io
import json
import unittest
from types import SimpleNamespace

from backend_patch import patch_actions

FIXTURE = '''
import json
def set_pihole_cluster_blocking(enable_bool, timer_sec=300):
    payload = {"blocking": bool(enable_bool), "timer": None if enable_bool else int(timer_sec)}
    return payload

class DashboardHandler(object):
    def do_POST(self):
        if self.path == "/api/action/pihole-toggle":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.body if length > 0 else "{}"
            try:
                req_data = json.loads(raw)
            except Exception:
                req_data = {}
            enable = bool(req_data.get("enable", True))
            timer = int(req_data.get("timer", 300))
            self.result = set_pihole_cluster_blocking(enable, timer)
        elif self.path == "/api/action/syslog-clear":
            self.result = "cleared"

    def do_GET(self):
        if self.path.startswith("/api/telemetry"):
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
'''


class ActionGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = patch_actions(FIXTURE)
        self.ns = {}
        exec(compile(self.source, '<fixture>', 'exec'), self.ns)

    def post(self, path, headers, body='{}'):
        h = self.ns['DashboardHandler']()
        codes = []
        h.path, h.body, h.result, h.wfile = path, body, None, io.BytesIO()
        h.headers = {'Content-Length': str(len(body)), **headers}
        h.send_response, h.send_header, h.end_headers = codes.append, lambda *a: None, lambda: None
        h.do_POST()
        return codes, h.result

    def test_idempotent_and_cors_removed(self):
        self.assertEqual(patch_actions(self.source), self.source)
        self.assertNotIn('Access-Control-Allow-Origin', self.source)

    def test_cross_site_or_headerless_requests_never_run(self):
        for path in ('/api/action/pihole-toggle', '/api/action/syslog-clear'):
            for headers in ({'Host': '192.168.0.2'},
                            {'Host': '192.168.0.2', 'X-Dashboard-Action': '1', 'Origin': 'http://evil.example'},
                            {'Host': 'evil.example', 'X-Dashboard-Action': '1'}):
                codes, result = self.post(path, headers, '{"enable": false, "timer": 0}')
                self.assertEqual((codes, result), ([403], None))

    def test_bad_length_rejected_before_handler(self):
        codes, result = self.post('/api/action/syslog-clear', {'Host': '192.168.0.2', 'X-Dashboard-Action': '1', 'Content-Length': 'x'})
        self.assertEqual((codes, result), ([400], None))

    def test_homepage_request_runs_with_bounded_timer(self):
        ok = {'Host': '192.168.0.2', 'X-Dashboard-Action': '1', 'Origin': 'http://192.168.0.2'}
        for timer, expected in ((300, 300), (10**9, 3600), (0, 1), (-5, 1), ('x', 300)):
            _, result = self.post('/api/action/pihole-toggle', ok, json.dumps({'enable': False, 'timer': timer}))
            self.assertEqual(result, {'blocking': False, 'timer': expected})
        _, result = self.post('/api/action/pihole-toggle', ok, '[1]')
        self.assertEqual(result, {'blocking': True, 'timer': None})
        _, result = self.post('/api/action/syslog-clear', ok)
        self.assertEqual(result, 'cleared')

    def test_shape_drift_fails_before_mutation(self):
        with self.assertRaises(ValueError):
            patch_actions('class DashboardHandler(object):\n    pass\n')


if __name__ == '__main__':
    unittest.main()
