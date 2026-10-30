import http.client
import http.server
import json
import threading
import unittest
from unittest.mock import patch

import gpu_broker as g


class FakePC(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    seen = []
    release = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakePC.seen.append(('GET', self.path, self.headers.get('Authorization'), None))
        raw = b'{"data":[]}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        FakePC.seen.append(('POST', self.path, self.headers.get('Authorization'), body))
        # A chunked SSE stream whose second event waits until the test has read the first one.
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        for event, wait in ((b'data: one\n\n', False), (b'data: two\n\n', True)):
            if wait:
                FakePC.release.wait(5)
            self.wfile.write(b'%x\r\n%s\r\n' % (len(event), event))
            self.wfile.flush()
        self.wfile.write(b'0\r\n\r\n')


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class BrokerTests(unittest.TestCase):
    def setUp(self):
        FakePC.seen = []
        FakePC.release = threading.Event()
        self.pc = serve(http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakePC))
        self.broker = serve(g.Server(('127.0.0.1', 0), 'llama', ('127.0.0.1', self.pc.server_address[1])))
        self.port = self.broker.server_address[1]

    def tearDown(self):
        FakePC.release.set()
        for s in (self.broker, self.pc):
            s.shutdown()
            s.server_close()

    def conn(self):
        return http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)

    def test_status_reads_pass_through_without_switching(self):
        with patch.object(g, 'ensure') as ensure:
            c = self.conn()
            c.request('GET', '/v1/models', headers={'Authorization': 'Bearer t'})
            r = c.getresponse()
            self.assertEqual((r.status, r.read()), (200, b'{"data":[]}'))
        ensure.assert_not_called()
        self.assertEqual(FakePC.seen, [('GET', '/v1/models', 'Bearer t', None)])

    def test_generation_switches_first_and_streams(self):
        with patch.object(g, 'ensure') as ensure:
            c = self.conn()
            body = json.dumps({'model': 'm', 'stream': True}).encode()
            c.request('POST', '/v1/chat/completions', body=body, headers={'Content-Type': 'application/json'})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read1(64), b'data: one\n\n')  # arrives before the upstream finishes
            FakePC.release.set()
            self.assertEqual(r.read(), b'data: two\n\n')
        ensure.assert_called_once_with('llama')
        self.assertEqual(FakePC.seen[0][3], body)

    def test_failed_switch_is_503_and_never_reaches_the_pc(self):
        with patch.object(g, 'ensure', side_effect=RuntimeError('no')):
            c = self.conn()
            c.request('POST', '/v1/chat/completions', body=b'{}', headers={'Content-Type': 'application/json'})
            r = c.getresponse()
            self.assertEqual(r.status, 503)
            self.assertIn('could not switch', json.loads(r.read())['error']['message'])
        self.assertEqual(FakePC.seen, [])

    def test_unreachable_pc_is_502(self):
        dead = serve(g.Server(('127.0.0.1', 0), 'llama', ('127.0.0.1', 1)))
        try:
            c = http.client.HTTPConnection('127.0.0.1', dead.server_address[1], timeout=10)
            c.request('GET', '/health')
            self.assertEqual(c.getresponse().status, 502)
        finally:
            dead.shutdown()
            dead.server_close()

    def test_only_generation_posts_need_the_gpu(self):
        self.assertTrue(g.needs_gpu('POST', '/v1/chat/completions'))
        self.assertTrue(g.needs_gpu('POST', '/v1/messages?beta=true'))
        for method, path in (('GET', '/v1/chat/completions'), ('POST', '/models/unload'), ('POST', '/load'),
                             ('GET', '/health'), ('GET', '/slots')):
            self.assertFalse(g.needs_gpu(method, path), (method, path))


class EnsureTests(unittest.TestCase):
    def run_ensure(self, engine, strata_loaded, llama_busy, wait=True, game=False):
        calls = []
        with patch.object(g, 'strata_loaded', side_effect=list(strata_loaded)), \
             patch.object(g, 'llama_busy', return_value=llama_busy), \
             patch.object(g, 'game_running', return_value=game), \
             patch.object(g.ai_monitor, 'strata_post', side_effect=lambda p: calls.append(p)), \
             patch.object(g.ai_monitor, 'unload_llama', side_effect=lambda: calls.append('unload-llama')), \
             patch.object(g.ai_monitor, 'wait_strata', return_value=wait):
            g.ensure(engine)
        return calls

    def test_llama_request_unloads_strata_only_when_loaded(self):
        self.assertEqual(self.run_ensure('llama', [True], False), ['/unload'])
        self.assertEqual(self.run_ensure('llama', [False], True), [])

    def test_strata_request_frees_llama_then_loads_strata(self):
        self.assertEqual(self.run_ensure('strata', [False], True), ['unload-llama', '/load'])
        self.assertEqual(self.run_ensure('strata', [True], False), [])

    def test_strata_is_not_loaded_while_a_game_runs(self):
        with self.assertRaises(g.GamePaused):
            self.run_ensure('strata', [False], False, game=True)
        # Already loaded: nothing to load, so no refusal.
        self.assertEqual(self.run_ensure('strata', [True], False, game=True), [])

    def test_game_detection_is_a_refused_llama_port(self):
        with patch.object(g.socket, 'create_connection', side_effect=ConnectionRefusedError):
            self.assertTrue(g.game_running())
        with patch.object(g.socket, 'create_connection', side_effect=TimeoutError):
            self.assertFalse(g.game_running())  # PC asleep or unreachable is not a game

    def test_switch_that_never_finishes_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_ensure('strata', [False], False, wait=False)


if __name__ == '__main__':
    unittest.main()
