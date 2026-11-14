"""Session lease + queue rules (SPEC-session-queue, 14 Nov 2026). Socket tests use a stub engine and patched
small constants (same shape as Phase 1's 600/600/600/1800/4h/120/15 s); the lease state machine is checked
with an explicit fake `now`, so grace/idle/fairness/max boundaries are tested to the second, not by sleeping."""
import http.client
import http.server
import json
import threading
import time
from contextlib import closing
import unittest
from unittest.mock import patch

import gpu_broker as g


class StubEngine(http.server.BaseHTTPRequestHandler):
    """One SSE answer per request. The session header decides the content: plain reply ends finish_reason 'stop'
    (broker marks the holder done); 'T' replies with a bash tool call (lease must stay working); 'K' with an
    ask tool call (lease becomes waiting)."""
    protocol_version = 'HTTP/1.1'
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        sess = self.headers.get('x-session-affinity')
        StubEngine.seen.append(sess)
        delta = {'content': 'hi ' + (sess or '-')}
        finish = 'stop'
        if sess in ('T', 'K'):
            delta['tool_calls'] = [{'index': 0, 'id': 'c1',
                                    'function': {'name': 'ask' if sess == 'K' else 'bash', 'arguments': '{}'}}]
            finish = 'tool_calls'
        payload = {'id': 'x', 'choices': [{'finish_reason': finish, 'delta': delta}]}
        event = ('data: %s\n\n' % json.dumps(payload)).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        self.wfile.write(b'%x\r\n%s\r\n' % (len(event), event))
        self.wfile.flush()
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()


def chat(sess, stream=True, marker='go'):
    body = json.dumps({'model': 'm', 'stream': stream,
                       'messages': [{'role': 'system', 'content': 's'},
                                    {'role': 'user', 'content': marker}]})
    headers = {'Content-Type': 'application/json'}
    if sess:
        headers['x-session-affinity'] = sess
    return body, headers


def send_recv(conn, sess, stream=True, marker='go'):
    body, headers = chat(sess, stream, marker)
    conn.request('POST', '/v1/chat/completions', body=body, headers=headers)
    r = conn.getresponse()
    return r.status, r.read()


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class QueueIT(unittest.TestCase):
    def setUp(self):
        StubEngine.seen = []
        for k, v in (('key', None), ('client', None), ('since', 0.0), ('last_req', 0.0),
                     ('release_at', float('inf')), ('state', None)):
            g._lease[k] = v
        del g._queue[:]
        self.rows = []
        self.patches = [patch.object(g, 'QUEUE_ENABLED', True), patch.object(g, 'QUEUE_KEEPALIVE_S', 0.05),
                        patch.object(g, 'DONE_GRACE_S', 0.3), patch.object(g, 'ASK_GRACE_S', 0.3),
                        patch.object(g, 'LEASE_IDLE_S', 0.3), patch.object(g, 'FAIR_HOLD_S', 0.5),
                        patch.object(g, 'LEASE_MAX_S', 1.0), patch.object(g, 'QUEUE_HOLD_S', 0.3),
                        patch.object(g.pc_net, 'pc_awake', return_value=True),
                        patch.object(g, 'ensure', lambda engine, hold=False: None),
                        patch.object(g, 'release', lambda engine: None),
                        patch.object(g.ai_history, 'record', lambda row: self.rows.append(dict(row)))]
        g._awake['at'] = 0.0
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.engine = _serve(http.server.ThreadingHTTPServer(('127.0.0.1', 0), StubEngine))
        self.broker = _serve(g.Server(('127.0.0.1', 0), 'llama', ('127.0.0.1', self.engine.server_address[1])))
        self.addCleanup(lambda: [s.shutdown() and s.server_close() for s in (self.broker, self.engine)])

    def conn(self):
        return http.client.HTTPConnection('127.0.0.1', self.broker.server_address[1], timeout=10)

    def thread_send(self, sess, stream=True, marker='go'):
        """Send from a helper thread; returns (handle, result-box) so the test can act while it is parked."""
        box = {}
        def run():
            c = self.conn()
            try:
                box['result'] = send_recv(c, sess, stream, marker)
            except Exception as e:
                box['error'] = repr(e)
            finally:
                box['done'] = True
        t = threading.Thread(target=run)
        t.start()
        return t, box

    def test_holder_takes_lease_and_next_request_rides(self):
        with closing(self.conn()) as c:
            s1, b1 = send_recv(c, 'A')
            s2, b2 = send_recv(c, 'A')
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(StubEngine.seen, ['A', 'A'])
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'A')
        self.assertEqual(snap['queue'], [])

    def test_second_session_parks_then_gets_its_own_stream_after_grace(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)  # holder A, done at stream end, grace 0.3 s
        t, box = self.thread_send('B')
        time.sleep(0.12)  # B arrived mid-grace: parked behind A
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'A')
        self.assertEqual([(q['position'], q['session']) for q in snap['queue']], [(1, 'B')])
        self.assertEqual(StubEngine.seen, ['A'])  # B has not reached the engine yet
        t.join(5)
        self.assertEqual(box['result'][0], 200)
        self.assertIn(b'hi B', box['result'][1])
        self.assertEqual(StubEngine.seen, ['A', 'B'])
        waits = [r['queued_s'] for r in self.rows if r.get('queued_s')]
        self.assertTrue(waits and waits[-1] > 0.05)  # history saw the wait

    def test_parked_stream_commits_headers_and_queued_comments(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)
        c = self.conn()
        c.request('POST', '/v1/chat/completions', *(), **{'body': chat('B')[0], 'headers': chat('B')[1]})
        r = c.getresponse()
        self.assertEqual((r.status, r.getheader('Content-Type')), (200, 'text/event-stream'))
        head = b''
        while b'\n\n' not in head:
            chunk = r.read(1)
            if not chunk:
                break
            head += chunk
        self.assertTrue(head.startswith(b': queued, position 1'))  # 200 + comment committed while parked
        time.sleep(0.4)  # grace passes; B's 15 s (0.05 s) tick promotes it
        body = r.read()
        self.assertIn(b': queued, position 1', head + body)
        self.assertIn(b'hi B', body)
        self.assertEqual(StubEngine.seen, ['A', 'B'])

    def test_empty_queue_lease_stays_past_idle(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'T')[0], 200)  # working holder (bash tool call)
        time.sleep(0.45)  # past LEASE_IDLE_S, nobody waiting: lease stays put, costs nothing
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'T')
        self.assertEqual(snap['holder']['state'], 'working')
        self.assertEqual(StubEngine.seen, ['T'])

    def test_pass_through_client_never_takes_or_moves_the_lease(self):
        # Every socket peer here is 127.0.0.1, so the rule is exercised at the seam it reads: is_pass_through
        # is flipped only around the pass-through client's own request (documented test seam, not product behaviour).
        pt = patch.object(g, 'is_pass_through', lambda addr: getattr(g, '_pt', False))
        pt.start()
        self.addCleanup(pt.stop)
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)  # holder A, done, 0.3 s grace
        g._pt = True
        with closing(self.conn()) as c:
            time.sleep(0.15)  # mid-grace: a pass-through request must NOT queue behind A
            self.assertEqual(send_recv(c, None, marker='webui chat')[0], 200)
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'A')  # holder kept its lease...
        self.assertEqual(snap['queue'], [])  # ...and its place
        self.assertGreater(StubEngine.seen.index(None), 0)
        g._pt = False
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)  # holder still there, rides straight through
        self.assertEqual(StubEngine.seen, ['A', None, 'A'])

    def test_non_streaming_wait_caps_at_503_retry_after_and_keeps_its_place(self):
        for name, val in (('LEASE_IDLE_S', 5.0), ('LEASE_MAX_S', 5.0), ('FAIR_HOLD_S', 5.0)):
            patch.object(g, name, val).start()
        self.addCleanup(lambda: [setattr(g, n, v) for n, v in
                                 (('LEASE_IDLE_S', 600), ('LEASE_MAX_S', 4 * 3600), ('FAIR_HOLD_S', 1800))])
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'T')[0], 200)  # working holder (bash tool call)
        with closing(self.conn()) as c:
            s, b = send_recv(c, 'B2', stream=False)  # queues; QUEUE_HOLD_S 0.3 -> 503, no SSE needed
        self.assertEqual(s, 503)
        self.assertIn(b'gpu_broker', b)
        snap = g.lease_snapshot()
        self.assertEqual(snap['queue'][0]['session'], 'B2')  # entry kept: a retry keeps its place in line
        self.assertTrue(any(r['queued_s'] is not None for r in self.rows))  # the wait was recorded
        g.LEASE_IDLE_S = 0.05  # now the holder really is idle-waiting: any tick may hand over
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'B2', stream=False)[0], 200)  # the retry is the head: granted
        self.assertEqual(StubEngine.seen[-1], 'B2')

    def test_fairness_demotes_the_holder_to_the_back_of_the_queue(self):
        # Widen idle/max/hold for this test only: the holder must not idle out before fairness fires.
        for name, val in (('LEASE_IDLE_S', 5.0), ('LEASE_MAX_S', 5.0), ('QUEUE_HOLD_S', 5.0)):
            patch.object(g, name, val).start()
        self.addCleanup(lambda: [setattr(g, n, v) for n, v in
                                 (('LEASE_IDLE_S', 600), ('LEASE_MAX_S', 4 * 3600), ('QUEUE_HOLD_S', 120))])
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'T')[0], 200)  # holder working (bash tool call)
        tb, boxB = self.thread_send('B')  # B queues behind the working holder
        time.sleep(0.6)  # past FAIR_HOLD_S (0.5) with B waiting
        tc, boxC = self.thread_send('T')  # holder's next request: demoted to the back, it parks
        time.sleep(0.15)
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'B')  # the queue head took the lease...
        self.assertEqual([q['session'] for q in snap['queue']], ['T'])  # demoted holder is last in line
        for t, box in ((tb, boxB), (tc, boxC)):
            t.join(8)
            self.assertEqual(box['result'][0], 200)  # B served now; T served after B's done-grace
        self.assertEqual(StubEngine.seen, ['T', 'B', 'T'])

    def test_lease_max_cuts_a_stuck_holder_even_without_a_waiter(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'T')[0], 200)
        time.sleep(1.1)  # past LEASE_MAX_S 1.0: the next request's own tick releases the lease
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'X')[0], 200)
        self.assertEqual(StubEngine.seen, ['T', 'X'])
        self.assertEqual(g.lease_snapshot()['holder']['session'], 'X')

    def test_release_endpoint_rules(self):
        hdr = {'Content-Type': 'application/json', 'X-Dashboard-Action': '1'}
        with closing(self.conn()) as c:
            c.request('POST', '/queue/release', body=b'{}', headers={'Content-Type': 'application/json'})
            self.assertEqual(c.getresponse().status, 401)  # no action header: refused
            c.request('POST', '/queue/release', body=b'{}', headers=hdr)
            self.assertEqual(json.loads(c.getresponse().read())['reason'], 'no holder')
            self.assertEqual(send_recv(c, 'T')[0], 200)  # holder mid-turn (working)
            c.request('POST', '/queue/release', body=b'{}', headers=hdr)
            self.assertEqual(c.getresponse().status, 409)  # refused: working, no session given
            c.request('POST', '/queue/release', body=json.dumps({'session': 'WRONG'}).encode(), headers=hdr)
            self.assertEqual(json.loads(c.getresponse().read())['released'], False)  # only its holder
            c.request('POST', '/queue/release', body=json.dumps({'session': 'T'}).encode(), headers=hdr)
            self.assertEqual(json.loads(c.getresponse().read())['released'], True)  # exact by session id
        self.assertIsNone(g.lease_snapshot()['holder'])

    def test_plain_release_works_after_a_finished_reply(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)  # 'stop': the broker marked the holder done
            c.request('POST', '/queue/release', body=b'{}', headers={'Content-Type': 'application/json',
                                                                   'X-Dashboard-Action': '1'})
            self.assertEqual(json.loads(c.getresponse().read())['released'], True)

    def test_ask_response_marks_the_lease_waiting(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'K')[0], 200)  # ask tool call in the reply tail
        snap = g.lease_snapshot()
        self.assertEqual(snap['holder']['session'], 'K')
        self.assertEqual(snap['holder']['state'], 'waiting')

    def test_status_endpoint_never_queues(self):
        with closing(self.conn()) as c:
            self.assertEqual(send_recv(c, 'A')[0], 200)
        with closing(self.conn()) as c:  # fresh connection: the HTTP/1.0 broker closes after every reply
            c.request('GET', '/queue/status')
            r = c.getresponse()
            data = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertEqual((data['enabled'], data['holder']['session']), (True, 'A'))
        self.assertEqual(StubEngine.seen, ['A'])  # the status read itself never reached the engine

    def test_disabled_flag_changes_nothing_but_history_columns(self):
        with patch.object(g, 'QUEUE_ENABLED', False):
            with closing(self.conn()) as c1, closing(self.conn()) as c2:
                self.assertEqual(send_recv(c1, 'T')[0], 200)
                self.assertEqual(send_recv(c2, 'B')[0], 200)  # straight through: today's behaviour
            snap = g.lease_snapshot()
            self.assertFalse(snap['enabled'])
            self.assertIsNone(snap['holder'])
            self.assertEqual(snap['queue'], [])
            self.assertEqual(StubEngine.seen, ['T', 'B'])
            self.assertTrue(all(r['client'] == '127.0.0.1' and r['queued_s'] is None for r in self.rows))
            with closing(self.conn()) as c:
                c.request('POST', '/queue/release', body=b'{}',
                          headers={'Content-Type': 'application/json', 'X-Dashboard-Action': '1'})
                self.assertEqual(json.loads(c.getresponse().read())['reason'], 'queue disabled')


class LeaseClock(unittest.TestCase):
    """The real 600/600/600/1800/4h rules at their exact boundaries, driven by an explicit now."""
    def setUp(self):
        for k, v in (('key', None), ('client', None), ('since', 0.0), ('last_req', 0.0),
                     ('release_at', float('inf')), ('state', None)):
            g._lease[k] = v
        del g._queue[:]

    def test_done_grace_only_matters_with_a_waiter(self):
        g.lease_grant('A', 'x', 0)
        g.lease_end(10, 'done', 'A')
        self.assertIsNone(g.lease_release(609))
        self.assertIsNone(g.lease_release(610))  # grace passed but nobody waits: the lease stays, costs nothing
        g.lease_update(611)
        self.assertEqual(g._lease['key'], 'A')
        g._queue.append({'key': 'B', 'client': 'x', 'first': 612})
        g.lease_update(612)
        self.assertEqual(g._lease['key'], 'B')  # with a waiter and grace past: hand-over
        self.assertEqual(g._queue, [])

    def test_ask_grace_600(self):
        g.lease_grant('A', 'x', 0)
        g.lease_end(5, 'waiting', 'A')
        self.assertEqual(g._lease['state'], 'waiting')
        self.assertIsNone(g.lease_release(604))  # nobody waiting: keep
        g._queue.append({'key': 'B', 'client': 'x', 'first': 605})
        self.assertEqual(g.lease_release(605), 'grace')  # now the hand-over can happen

    def test_working_holder_graces_never_only_idle_fairness_max(self):
        g.lease_grant('A', 'x', 0)
        g.lease_end(10, 'working', 'A')
        self.assertIsNone(g.lease_release(1799))
        g._queue.append({'key': 'B', 'client': 'x', 'first': 1800})
        self.assertEqual(g.lease_release(1800), 'idle')  # 1800 s of no request: idle (600) beats fairness (1800)

    def test_idle_600_with_a_waiter_releases_at_the_second(self):
        g.lease_grant('A', 'x', 0)
        g._queue.append({'key': 'B', 'client': 'x', 'first': 599})
        self.assertIsNone(g.lease_release(599))
        self.assertEqual(g.lease_release(600), 'idle')
        g.lease_update(600)
        self.assertEqual(g._lease['key'], 'B')

    def test_fairness_handover_puts_the_demoted_holder_last_in_line(self):
        g.lease_grant('A', 'x', 0)
        g.lease_end(10, 'working', 'A')
        g._queue.append({'key': 'B', 'client': 'x', 'first': 600})  # fresh waiter (idle would grab at 600 first)
        g._lease['last_req'] = 1800  # holder kept requesting (working): no idle; only fairness applies
        g.lease_update(1801)
        self.assertEqual(g._lease['key'], 'B')
        self.assertEqual([q['key'] for q in g._queue], ['A'])

    def test_max_hold_4h_cuts_even_without_a_waiter(self):
        g.lease_grant('A', 'x', 0)
        g.lease_end(10, 'working', 'A')
        self.assertEqual(g.lease_release(4 * 3600 - 1), None)
        self.assertEqual(g.lease_release(4 * 3600), 'max')
        g.lease_update(4 * 3600)
        self.assertIsNone(g._lease['key'])

    def test_free_lease_with_queue_hands_to_the_head(self):
        g._queue.append({'key': 'A', 'client': 'x', 'first': 20})
        g.lease_update(21)
        self.assertEqual(g._lease['key'], 'A')


class Identity(unittest.TestCase):
    def test_header_key_is_stable_and_wins(self):
        k = g.session_key({'x-session-affinity': 'pid-123'}, b'{"model":"m"}', '192.168.0.21')
        self.assertEqual(k, 'pid-123')
        self.assertEqual(k, g.session_key({'x-session-affinity': 'pid-123'}, b'{"model":"other","messages":[]}',
                                          '10.0.0.9'))

    def test_fallback_key_across_turns_compaction_and_sessions(self):
        body = lambda first: json.dumps({'model': 'qwen3.8-flash-next-iq1_m',
                                         'messages': [{'role': 'system', 'content': 's'},
                                                      {'role': 'user', 'content': first}]})
        self.assertEqual(g.session_key({}, body('start'), '192.168.0.21'),
                          g.session_key({}, body('start'), '192.168.0.21'))  # stable across turns
        self.assertNotEqual(g.session_key({}, body('start'), '192.168.0.21'),
                          g.session_key({}, body('The conversation history before this point was compacted into '
                                                'the following summary: <summary>'), '192.168.0.21'))  # compaction
        self.assertNotEqual(g.session_key({}, body('start'), '192.168.0.21'),
                          g.session_key({}, body('start'), '192.168.0.16'))  # different clients differ
        q = json.dumps({'model': 'qwen3.8-flash-next-iq3_xxs',
                        'messages': [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'start'}]})
        self.assertEqual(g.session_key({}, body('start'), '192.168.0.21'), g.session_key({}, q, '192.168.0.21'))

    def test_model_family_tags_strip(self):
        for full, fam in (('qwen3.8-flash-next-iq3_xxs', 'qwen3.8-flash-next'),
                          ('qwen3.8-flash-next-coder-iq1_m', 'qwen3.8-flash-next-coder'),
                          ('gemma-4-26b-a4b', 'gemma-4-26b'),
                          ('qwen3.8-27b-uncensored', 'qwen3.8-27b-uncensored'),
                          ('swift-1.5-iq2_xs', 'swift-1.5')):
            self.assertEqual(g.model_family(full), fam)

    def test_finish_state_reads_reply_tails(self):
        self.assertEqual(g.finish_state(b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}'), 'done')
        self.assertEqual(g.finish_state(b'data: {"choices":[{"finish_reason":"tool_calls","delta":{"tool_calls":'
                        b'[{"function":{"name":"bash"}}]}}]}'), 'working')
        self.assertEqual(g.finish_state(b'data: {"choices":[{"finish_reason":"tool_calls","delta":{"tool_calls":'
                        b'[{"function":{"name":"ask"}}]}}]}'), 'waiting')
        self.assertEqual(g.finish_state(b''), 'working')
        self.assertEqual(g.finish_state(b'data: {"choices":[{"finish_reason":"length","delta":{"content":"x"}}]}'),
                         'done')


if __name__ == '__main__':
    unittest.main()
