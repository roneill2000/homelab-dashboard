import http.client
import http.server
import json
import threading
import time
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
        ensure.assert_called_once_with('llama', hold=True)
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


    def test_cross_site_and_malformed_requests_never_reach_the_pc(self):
        body = b'{"model": "m"}'
        cases = [({'Content-Type': 'application/json', 'Origin': 'http://evil.example'}, 403),
                 ({'Content-Type': 'text/plain'}, 415),  # a "simple" cross-site form/fetch POST
                 ({'Content-Type': 'application/json', 'Host': 'evil.example:8080'}, 421)]  # DNS rebinding
        with patch.object(g, 'ensure') as ensure:
            for headers, code in cases:
                c = self.conn()
                c.request('POST', '/v1/chat/completions', body=body, headers=headers)
                self.assertEqual(c.getresponse().status, code, headers)
            c = self.conn()
            c.putrequest('POST', '/v1/chat/completions')
            c.putheader('Content-Type', 'application/json'); c.putheader('Content-Length', '-1'); c.endheaders()
            self.assertEqual(c.getresponse().status, 400)
        ensure.assert_not_called()
        self.assertEqual(FakePC.seen, [])

    def test_server_clients_pass_the_guard(self):
        h = {'Host': '192.168.0.2:8088', 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': '2'}
        self.assertIsNone(g.refuse('POST', '/v1/chat/completions', h))
        self.assertIsNone(g.refuse('GET', '/health', {'Host': 'localhost:8080'}))  # status reads need no JSON
        page = {'Host': 'strata.lan', 'Origin': 'https://strata.lan', 'Content-Type': 'application/json'}
        self.assertIsNone(g.refuse('POST', '/v1/chat/completions', page))  # Strata's chat page via Caddy
        self.assertEqual(g.refuse('POST', '/v1/chat/completions', dict(page, Origin='https://evil.example'))[0], 403)
        self.assertEqual(g.refuse('POST', '/v1/chat/completions', dict(page, Host='192.168.0.2:8088'))[0], 403)


    def test_strata_pages_load_button_frees_the_gpu_first(self):
        strata = serve(g.Server(('127.0.0.1', 0), 'strata', ('127.0.0.1', self.pc.server_address[1])))
        try:
            with patch.object(g, 'ensure') as ensure, patch.object(g, 'remember') as remember:
                c = http.client.HTTPConnection('127.0.0.1', strata.server_address[1], timeout=10)
                c.request('POST', '/load', body=b'{}', headers={'Content-Type': 'application/json'})
                r = c.getresponse(); FakePC.release.set(); r.read()
            ensure.assert_called_once_with('strata', hold=False)  # a page Load holds no stream
            remember.assert_not_called()  # a load is not a request in the history
        finally:
            strata.shutdown(); strata.server_close()


class EnsureTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        p = patch.object(g.ai_monitor, 'GPU_LOCK', __import__('pathlib').Path(d.name) / 'gpu.lock'); p.start(); self.addCleanup(p.stop)

    def run_ensure(self, engine, loaded, llama_busy, wait=True, game=False):
        """loaded: which Strata engines are loaded, e.g. {'strata': True}; returns the calls made, in order."""
        calls = []
        state = {'strata': False, 'coder': False, **loaded}
        with patch.object(g, 'strata_loaded', side_effect=lambda e='strata': state[e]), \
             patch.object(g, 'llama_busy', return_value=llama_busy), \
             patch.object(g, 'game_running', return_value=game), \
             patch.object(g.ai_monitor, 'strata_post', side_effect=lambda p, port=8088: calls.append(f'{p}:{port}')), \
             patch.object(g.ai_monitor, 'unload_llama', side_effect=lambda: calls.append('unload-llama')), \
             patch.object(g.ai_monitor, 'wait_strata', return_value=wait):
            g.ensure(engine)
        return calls

    def test_llama_request_unloads_strata_only_when_loaded(self):
        self.assertEqual(self.run_ensure('llama', {'strata': True}, False), ['/unload:8088'])
        self.assertEqual(self.run_ensure('llama', {'coder': True}, False), ['/unload:8089'])
        self.assertEqual(self.run_ensure('llama', {}, True), [])

    def test_strata_request_frees_llama_then_loads_strata(self):
        self.assertEqual(self.run_ensure('strata', {}, True), ['unload-llama', '/load:8088'])
        self.assertEqual(self.run_ensure('strata', {'strata': True}, False), [])

    def test_coder_and_general_strata_swap_each_other_out(self):
        self.assertEqual(self.run_ensure('coder', {'strata': True}, False), ['/unload:8088', '/load:8089'])
        self.assertEqual(self.run_ensure('strata', {'coder': True}, False), ['/unload:8089', '/load:8088'])
        self.assertEqual(self.run_ensure('coder', {'coder': True}, True), ['unload-llama'])

    def test_strata_is_not_loaded_while_a_game_runs(self):
        with self.assertRaises(g.GamePaused):
            self.run_ensure('strata', {}, False, game=True)
        # Already loaded: nothing to load, so no refusal.
        self.assertEqual(self.run_ensure('strata', {'strata': True}, False, game=True), [])

    def test_only_known_clients_may_use_the_broker(self):
        for ip in ('127.0.0.1', '192.168.0.16', '192.168.0.21', '192.168.0.20', '192.168.0.4', '::ffff:192.168.0.3'):
            self.assertTrue(g.client_allowed(ip), ip)
        for ip in ('192.168.0.55', '192.168.0.12', '10.0.0.5', '', None):
            self.assertFalse(g.client_allowed(ip), ip)

    def test_game_detection_is_a_refused_llama_port(self):
        with patch.object(g.ai_monitor.socket, 'create_connection', side_effect=ConnectionRefusedError):
            self.assertTrue(g.game_running())
        with patch.object(g.ai_monitor.socket, 'create_connection', side_effect=TimeoutError):
            self.assertFalse(g.game_running())  # PC asleep or unreachable is not a game

    def test_switch_that_never_finishes_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_ensure('strata', {}, False, wait=False)


if __name__ == '__main__':
    unittest.main()


NAMES = {'strata': 'qwen3.8-flash-next-iq3_xxs', 'coder': 'qwen3.8-flash-next-coder-iq1_m'}


class UnifiedPortTests(unittest.TestCase):
    """:8000, one URL for every model (2 Nov 2026): the request's model picks the engine."""

    def setUp(self):
        FakePC.seen = []
        FakePC.release = threading.Event()
        FakePC.release.set()
        self.pc = serve(http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakePC))
        self.broker = serve(g.Server(('127.0.0.1', 0), 'auto', ('127.0.0.1', 0)))
        self.broker.ports = {e: self.pc.server_address[1] for e in ('llama', 'strata', 'coder')}
        self.port = self.broker.server_address[1]
        self.patches = [patch.object(g, 'strata_names', return_value=NAMES),
                        patch.object(g, 'llama_token', return_value='secret')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for s in (self.broker, self.pc):
            s.shutdown()
            s.server_close()

    def post(self, model, auth=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Content-Type': 'application/json', **({'Authorization': auth} if auth else {})}
        c.request('POST', '/v1/chat/completions', body=json.dumps({'model': model}).encode(), headers=headers)
        r = c.getresponse()
        return r.status, r.read()

    def test_route_by_model_name(self):
        self.assertEqual(g.route('qwen3.8-flash-next-coder-iq1_m', NAMES), 'coder')
        self.assertEqual(g.route('qwen3.8-flash-next-iq3_xxs', NAMES), 'strata')
        self.assertEqual(g.route('QWEN3.8-FLASH-NEXT-IQ3_XXS', NAMES), 'strata')
        self.assertEqual(g.route('qwen3.8-flash-next-coder-q2', {}), 'coder')   # a name the servers no longer report
        self.assertEqual(g.route('qwen3.8-27b', NAMES), 'llama')
        self.assertEqual(g.route(None, NAMES), 'llama')

    def test_coder_request_switches_to_coder_without_the_llama_token(self):
        with patch.object(g, 'ensure') as ensure:
            self.assertEqual(self.post(NAMES['coder'])[0], 200)
        ensure.assert_called_once_with('coder', hold=True)
        self.assertIsNone(FakePC.seen[0][2])

    def test_llama_request_gets_the_brokers_token_not_the_clients(self):
        with patch.object(g, 'ensure') as ensure:
            self.assertEqual(self.post('qwen3.8-27b', auth='Bearer whatever')[0], 200)
        ensure.assert_called_once_with('llama', hold=True)
        self.assertEqual(FakePC.seen[0][2], 'Bearer secret')

    def test_llama_reasoning_effort_is_normalized_and_copied_to_template_kwargs(self):
        with patch.object(g, 'ensure'):
            c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            c.request('POST', '/v1/chat/completions',
                      body=json.dumps({'model': 'qwen3.8-27b-uncensored', 'reasoning_effort': 'high'}).encode(),
                      headers={'Content-Type': 'application/json'})
            self.assertEqual(c.getresponse().status, 200)
            sent = json.loads(FakePC.seen[-1][3])
            self.assertEqual(sent['reasoning_effort'], 'xhigh')
            self.assertEqual(sent['chat_template_kwargs'], {'reasoning_effort': 'xhigh'})
        self.assertEqual(json.loads(g.llama_body(b'{"reasoning_effort":"minimal"}')),
                         {'reasoning_effort': 'low', 'chat_template_kwargs': {'reasoning_effort': 'low'}})
        self.assertEqual(json.loads(g.llama_body(b'{"reasoning_effort":"off"}')),
                         {'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False}})

    def test_qwen_27b_family_never_runs_without_thinking(self):
        for model in ('qwen3.8-27b', 'qwen3.8-27b-long', 'qwen3.8-27b-uncensored', 'qwen3.8-27b-uncensored-fast'):
            for ask in ({'reasoning_effort': 'off'}, {'reasoning_effort': 'none'},
                        {'chat_template_kwargs': {'enable_thinking': False}},
                        {'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False, 'x': 1}}):
                sent = json.loads(g.llama_body(json.dumps({'model': model, **ask}).encode()))
                self.assertEqual(sent['reasoning_effort'], 'medium', (model, ask))
                self.assertNotIn('enable_thinking', sent['chat_template_kwargs'], (model, ask))
                self.assertEqual(sent['chat_template_kwargs']['reasoning_effort'], 'medium')
        # levels other than off are kept; a plain request is passed through unchanged
        self.assertEqual(json.loads(g.llama_body(b'{"model":"qwen3.8-27b","reasoning_effort":"low"}'))['reasoning_effort'], 'low')
        self.assertEqual(g.llama_body(b'{"model":"qwen3.8-27b","messages":[]}'), b'{"model":"qwen3.8-27b","messages":[]}')

    def test_gemma_may_still_turn_thinking_off(self):
        sent = json.loads(g.llama_body(b'{"model":"gemma-4-26b-a4b","reasoning_effort":"off"}'))
        self.assertEqual(sent['reasoning_effort'], 'none')
        self.assertEqual(sent['chat_template_kwargs'], {'enable_thinking': False})

    def test_models_lists_every_engine(self):
        with patch.object(g.ai_monitor, 'fetch', return_value={'data': [{'id': 'qwen3.8-27b'}, {'id': 'gemma-4-26b-a4b'}]}):
            c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            c.request('GET', '/v1/models')
            ids = [m['id'] for m in json.loads(c.getresponse().read())['data']]
        self.assertEqual(ids, ['qwen3.8-27b', 'gemma-4-26b-a4b', NAMES['strata'], NAMES['coder']])

    def test_llama_model_status_passes_through_for_the_approver(self):
        listed = {'data': [{'id': 'qwen3.8-27b', 'status': {'value': 'loaded'}}]}
        with patch.object(g.ai_monitor, 'fetch', return_value=listed):
            data = g.all_models()['data']
        self.assertEqual(data[0]['status'], {'value': 'loaded'})
        self.assertNotIn('status', data[1])  # Strata entries carry none, so a judge never picks Strata
        self.assertEqual(FakePC.seen, [])

    def test_unknown_or_missing_model_is_refused_before_any_swap(self):
        with patch.object(g, 'llama_ids', return_value=['qwen3.8-27b']), patch.object(g, 'ensure') as ensure:
            self.assertEqual(self.post('gpt-4o-mini')[0], 404)
            self.assertEqual(self.post('')[0], 400)
        ensure.assert_not_called()
        self.assertEqual(FakePC.seen, [])

    def test_other_paths_and_bad_bodies_never_reach_the_pc(self):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('POST', '/models/unload', body=b'{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(c.getresponse().status, 404)
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('POST', '/v1/chat/completions', body=b'[1]', headers={'Content-Type': 'application/json'})
        self.assertEqual(c.getresponse().status, 400)
        self.assertEqual(FakePC.seen, [])



class DrainTests(unittest.TestCase):
    """A swap waits for the other engine's streaming replies (Sonnet audit, 2 Nov 2026)."""

    def setUp(self):
        self.p = [patch.object(g, '_switch', return_value=[]), patch.object(g.ai_monitor, 'gpu_switch')]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        for e in g._inflight:
            g._inflight[e] = 0

    def test_swap_waits_until_the_other_engines_stream_ends(self):
        g.ensure('coder', hold=True)
        self.assertEqual(g._inflight['coder'], 1)
        done = threading.Event()
        t = threading.Thread(target=lambda: (g.ensure('llama', hold=True), done.set()))
        t.start()
        self.assertFalse(done.wait(1.5))   # still waiting: the coder reply is streaming
        g.release('coder')
        self.assertTrue(done.wait(5))
        self.assertEqual(g._inflight, {'llama': 1, 'strata': 0, 'coder': 0})

    def test_same_engine_requests_do_not_wait(self):
        g.ensure('coder', hold=True)
        t0 = time.time()
        g.ensure('coder', hold=True)
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(g._inflight['coder'], 2)

    def test_drain_gives_up_after_its_timeout(self):
        g._inflight['strata'] = 1
        self.assertFalse(g.drain('llama', timeout=0.2))


def _convo(steps, result='same answer', args='{"command": "grep -n foo x"}', tail=()):
    """A user message, then `steps` identical tool steps (assistant tool call + its result)."""
    msgs = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'fix it'}]
    for n in range(steps):
        msgs.append({'role': 'assistant', 'content': None, 'reasoning_content': f'try {n}',
                     'tool_calls': [{'id': f'c{n}', 'type': 'function', 'function': {'name': 'bash', 'arguments': args}}]})
        msgs.append({'role': 'tool', 'tool_call_id': f'c{n}', 'content': result(n) if callable(result) else result})
    return msgs + list(tail)


class LoopGuardTests(unittest.TestCase):
    """11 Nov 2026: one tool call repeated with the same result 55-80 times. Normal sessions never repeat a step
    more than twice, so the 3rd identical step gets a note and the 6th is refused before any GPU swap."""

    def test_the_run_counts_identical_steps_at_the_end_only(self):
        self.assertEqual(g.loop_run(_convo(1)), 1)
        self.assertEqual(g.loop_run(_convo(5)), 5)
        self.assertEqual(g.loop_run(_convo(5, result=lambda n: f'line {n}')), 1)        # results change: polling
        self.assertEqual(g.loop_run(_convo(5, tail=[{'role': 'user', 'content': 'go on'}])), 0)  # Ryan spoke
        self.assertEqual(g.loop_run(_convo(0)), 0)
        self.assertEqual(g.loop_run(None), 0)
        # arguments compare as JSON, so key order and spacing do not hide a repeat
        msgs = _convo(2) + _convo(1, args='{ "command":"grep -n foo x" }')[2:]
        for i, m in enumerate(msgs[2:]):
            if m['role'] == 'assistant':
                m['tool_calls'][0]['id'] = m['tool_calls'][0]['id'] + str(i)
        self.assertEqual(g.loop_run(msgs), 3)

    def test_a_different_step_in_between_ends_the_run(self):
        msgs = _convo(3)
        msgs[4]['tool_calls'][0]['function']['arguments'] = '{"command": "ls"}'   # the 2nd step differs
        self.assertEqual(g.loop_run(msgs), 1)

    def test_parallel_calls_count_as_one_step(self):
        msgs = [{'role': 'user', 'content': 'x'}]
        for n in range(3):
            msgs.append({'role': 'assistant', 'tool_calls': [
                {'id': f'a{n}', 'function': {'name': 'read', 'arguments': '{"path": "a"}'}},
                {'id': f'b{n}', 'function': {'name': 'read', 'arguments': '{"path": "b"}'}}]})
            msgs += [{'role': 'tool', 'tool_call_id': f'a{n}', 'content': 'A'},
                     {'role': 'tool', 'tool_call_id': f'b{n}', 'content': [{'type': 'text', 'text': 'B'}]}]
        self.assertEqual(g.loop_run(msgs), 3)

    def test_the_note_goes_on_the_last_result_from_the_third_step(self):
        for steps in (1, 2):
            body = json.dumps({'model': 'm', 'messages': _convo(steps)}).encode()
            self.assertEqual(g.loop_guard(body), (body, steps))   # untouched
        sent, run = g.loop_guard(json.dumps({'model': 'm', 'messages': _convo(3)}).encode())
        msgs = json.loads(sent)['messages']
        self.assertEqual(run, 3)
        self.assertEqual(msgs[:-1], _convo(3)[:-1])               # the cached prefix is unchanged
        self.assertTrue(msgs[-1]['content'].startswith('same answer\n\n[Homelab broker: you have now made'))
        self.assertIn('3 times in a row', msgs[-1]['content'])
        listed = _convo(4, result=[{'type': 'text', 'text': 'same'}])
        msgs = json.loads(g.loop_guard(json.dumps({'messages': listed}).encode())[0])['messages']
        self.assertEqual(msgs[-1]['content'][0], {'type': 'text', 'text': 'same'})
        self.assertIn('Do not call it again', msgs[-1]['content'][-1]['text'])
        self.assertEqual(g.loop_guard(b'not json'), (b'not json', 0))

    def test_the_sixth_identical_step_is_refused_before_any_swap(self):
        FakePC.seen = []
        pc = serve(http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakePC))
        broker = serve(g.Server(('127.0.0.1', 0), 'auto', ('127.0.0.1', 0)))
        broker.ports = {e: pc.server_address[1] for e in ('llama', 'strata', 'coder')}
        FakePC.release.set()
        try:
            with patch.object(g, 'strata_names', return_value=NAMES), patch.object(g, 'llama_token', return_value='t'), \
                 patch.object(g, 'llama_ids', return_value=['qwen3.8-27b']), \
                 patch.object(g, 'ensure') as ensure, patch.object(g, 'remember') as remember:
                def post(steps, model='qwen3.8-27b'):
                    c = http.client.HTTPConnection('127.0.0.1', broker.server_address[1], timeout=10)
                    c.request('POST', '/v1/chat/completions', headers={'Content-Type': 'application/json'},
                              body=json.dumps({'model': model, 'messages': _convo(steps)}).encode())
                    r = c.getresponse()
                    return r.status, r.read()
                self.assertEqual(post(5)[0], 200)
                self.assertIn('5 times in a row', json.loads(FakePC.seen[-1][3])['messages'][-1]['content'])
                ensure.reset_mock(); FakePC.seen = []
                for model in ('qwen3.8-27b', NAMES['strata']):    # Strata conversations too
                    status, raw = post(6, model)
                    self.assertEqual(status, 409)
                    message = json.loads(raw)['error']['message']
                    self.assertIn('Loop guard', message)
                ensure.assert_not_called()
                self.assertEqual(FakePC.seen, [])
                self.assertEqual(remember.call_args[0][0]['swap'], 'loop')
        finally:
            for s in (broker, pc):
                s.shutdown(); s.server_close()
        # pi retries an error whose text matches its retry list; this one must not, or the loop just goes on
        for word in ('429', '500', '502', '503', 'rate limit', 'overloaded', 'timeout', 'timed out', 'terminated',
                     'server error', 'internal error', 'try your request again', 'unavailable', 'connection'):
            self.assertNotIn(word, message.lower())
