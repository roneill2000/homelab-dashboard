"""Broker wake-on-request, Part A (SPEC-broker-wake-on-request-20261112): generation requests wake a sleeping
gaming PC themselves. All network helpers are monkeypatched: no packets, no SSH, no PC, no real waiting."""
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

import gpu_broker as g


class Pure(unittest.TestCase):
    def setUp(self):
        # The 'PC confirmed awake' stamp is process state; every test starts unserved so probes are not skipped.
        g._awake['at'] = 0.0

    def test_awake_pc_returns_fast_without_waking(self):
        with patch.object(g.pc_net, 'pc_awake', return_value=True) as awake, \
             patch.object(g.pc_net, 'wake_pc') as wake, \
             patch.object(g, 'engine_ready') as ready:
            self.assertIsNone(g.ensure_awake('strata'))
        awake.assert_called_once()
        wake.assert_not_called()
        ready.assert_not_called()

    def test_asleep_pc_gets_one_wake_then_health_is_awaited(self):
        states = {'n': 0}

        def pc_awake(timeout=1.5):
            states['n'] += 1
            return states['n'] >= 4  # entry, in-lock and first poll are False; up from the second poll

        ready = {'n': 0}

        def engine_ready(engine):
            ready['n'] += 1
            return ready['n'] >= 3

        with patch.object(g.pc_net, 'pc_awake', pc_awake), \
             patch.object(g.pc_net, 'wake_pc') as wake, \
             patch.object(g, 'engine_ready', engine_ready):
            spent = g.ensure_awake('strata', timeout=30, sleep=lambda s: None)
        self.assertGreaterEqual(spent, 0.0)
        wake.assert_called_once()
        self.assertEqual(ready['n'], 3)  # the engine's health URL was awaited, not skipped

    def test_the_packet_is_re_sent_every_15_s_while_the_pc_stays_down(self):
        clock = {'t': 1_000_000.0}

        def fake_time():
            return clock['t']

        def pc_awake(timeout=1.5):
            clock['t'] += 1.0  # each poll advances the fake clock one second
            return False

        with patch.object(g.time, 'time', fake_time), \
             patch.object(g.pc_net, 'pc_awake', pc_awake), \
             patch.object(g.pc_net, 'wake_pc') as wake, \
             patch.object(g, 'engine_ready', return_value=True):
            with self.assertRaises(g.WakeTimeout):
                g.ensure_awake('llama', timeout=90, sleep=lambda s: None)
        # first send plus re-sends at +15 s (15, 30, 45, 60, 75 s) before the 90 s deadline
        self.assertEqual(wake.call_count, 6)

    def test_a_confirmed_stamp_skips_the_port_22_probe_for_15_s(self):
        # One probe per 15 s is what keeps the PC's OpenSSH log quiet while a model is in use; the probe stays on port
        # 22 (engine ports close during games), it is only skipped, never replaced.
        probes = {'n': 0}

        def pc_awake(timeout=1.5):
            probes['n'] += 1
            return True

        with patch.object(g.pc_net, 'pc_awake', pc_awake), patch.object(g.pc_net, 'wake_pc') as wake, \
             patch.object(g, 'engine_ready', side_effect=AssertionError('a cached PC waited on a health URL')):
            self.assertIsNone(g.ensure_awake('strata'))   # probe confirms, then stamps 'awake'
            self.assertIsNone(g.ensure_awake('strata'))   # inside the stamp: no probe, no wake, no health URL
            self.assertIsNone(g.ensure_awake('strata'))
        self.assertEqual(probes['n'], 1)
        wake.assert_not_called()
        self.assertGreater(g._awake['at'], 0.0)

    def test_the_probe_comes_back_once_the_stamp_expires(self):
        g._awake['at'] = time.time() - (g.AWAKE_CACHE + 1)  # a stamp older than 15 s buys nothing
        with patch.object(g.pc_net, 'pc_awake', return_value=True) as awake, patch.object(g.pc_net, 'wake_pc') as wake:
            self.assertIsNone(g.ensure_awake('strata'))
        awake.assert_called_once()
        wake.assert_not_called()

    def test_a_stamp_is_never_trusted_past_15_s_even_if_requests_keep_arriving(self):
        clock = {'t': 1_000_000.0}

        def fake_time():
            return clock['t']

        probes = {'n': 0}

        def pc_awake(timeout=1.5):
            probes['n'] += 1
            return True

        with patch.object(g.time, 'time', fake_time), patch.object(g.pc_net, 'pc_awake', pc_awake):
            for _ in range(20):            # requests every 1 s; the stamp must not be refreshed from itself
                g.ensure_awake('strata')
                clock['t'] += 1.0
        self.assertEqual(probes['n'], 2)   # t=0 and t=16 (the stamp at t=0 covers up to t=15)

    def test_never_wakes_raises_waketimeout(self):
        with patch.object(g.pc_net, 'pc_awake', return_value=False), \
             patch.object(g.pc_net, 'wake_pc') as wake:
            with self.assertRaises(g.WakeTimeout):
                g.ensure_awake('llama', timeout=0.05, sleep=lambda s: None)
        wake.assert_called_once()

    def test_engine_that_never_answers_its_health_url_also_times_out(self):
        with patch.object(g.pc_net, 'pc_awake', side_effect=[False, False, True]), \
             patch.object(g.pc_net, 'wake_pc'), \
             patch.object(g, 'engine_ready', return_value=False):
            with self.assertRaises(g.WakeTimeout):
                g.ensure_awake('strata', timeout=0.05, sleep=lambda s: None)

    def test_concurrent_requests_share_exactly_one_wake_sequence(self):
        up = threading.Event()

        def pc_awake(timeout=1.5):
            return up.is_set()

        wakes = {'n': 0}

        def wake():
            wakes['n'] += 1
            up.set()

        results = []

        def worker():
            results.append(g.ensure_awake('strata', sleep=lambda s: None))

        with patch.object(g.pc_net, 'pc_awake', pc_awake), patch.object(g.pc_net, 'wake_pc', wake), \
             patch.object(g, 'engine_ready', return_value=True):
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(len(results), 3)
        self.assertEqual(wakes['n'], 1)
        self.assertEqual([r is not None for r in results].count(True), 1)

    def test_cancel_countdown_thread_starts_at_most_once_per_60_s(self):
        g._cancel['at'] = 0.0
        started = []

        class FakeThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                started.append(self._target)

        with patch.object(g.threading, 'Thread', FakeThread), patch.object(g.pc_net, 'sleep_pc') as sleep_pc:
            g.maybe_cancel_sleep()
            g.maybe_cancel_sleep()
            g.maybe_cancel_sleep()
            self.assertEqual(len(started), 1)
            started[0]()
        sleep_pc.assert_called_once_with(command='cancel')

    def test_kvllama_engine_ready_probes_8092_health_with_bearer_token(self):
        seen = []
        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def fake_open(req, timeout=3):
            seen.append((req.full_url, req.get_header('Authorization')))
            return Resp()
        with patch.object(g, 'llama_token', return_value='tok'), patch.object(g.urllib.request, 'urlopen', side_effect=fake_open):
            self.assertTrue(g.engine_ready('kvllama'))
        self.assertEqual(seen, [(f'http://{g.PC}:8092/health', 'Bearer tok')])


class ThroughHTTP(unittest.TestCase):
    def setUp(self):
        g._awake['at'] = 0.0  # unserved: this class exercises the stamp through the HTTP path
        self.rows = []
        rows = patch.object(g.ai_history, 'record', side_effect=lambda row: self.rows.append(row))
        rows.start()
        self.addCleanup(rows.stop)

    def handler_for(self, engine, method, path, body):
        server = g.Server.__new__(g.Server)
        server.engine, server.upstream = engine, ('127.0.0.1', 1)  # dead upstream: only the pre-flight is probed
        server.ports = {'llama': 8080, 'strata': 8088, 'coder': 8089}
        h = g.Handler.__new__(g.Handler)
        h.server = server
        h.client_address = ('192.168.0.2', 0)
        h.command, h.path = method, path
        h.headers = {'Host': '192.168.0.2:8080', 'Content-Type': 'application/json',
                     'Content-Length': str(len(body))}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.request_version = 'HTTP/1.1'
        h.requestline = f'{method} {path} HTTP/1.1'
        h._headers_buffer = []
        return h

    def body_of(self, h):
        raw = h.wfile.getvalue()
        return json.loads(raw[raw.find(b'{'):])['error']['message']

    def test_wake_timeout_is_503_with_a_wake_failed_row(self):
        h = self.handler_for('llama', 'POST', '/v1/chat/completions', b'{"model": "m"}')
        with patch.object(g, 'ensure_awake', side_effect=g.WakeTimeout('the gaming PC did not come up')):
            h.forward()
        self.assertIn('did not wake up within 90 s', self.body_of(h))
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0]['swap'], 'wake-failed')
        self.assertEqual(self.rows[0]['status'], 503)

    def test_a_woken_request_records_wake_plus_the_swap(self):
        h = self.handler_for('strata', 'POST', '/v1/chat/completions', b'{"model": "m"}')
        with patch.object(g, 'ensure_awake', return_value=12.0), \
             patch.object(g, 'ensure', return_value='load-strata'):
            h.forward()  # upstream is dead on purpose: the swap decision must still reach history
        self.assertEqual(self.rows[0]['swap'], 'wake+load-strata')
        self.assertIsNotNone(self.rows[0]['swap_s'])  # swap_s spans wake + swap (ensure_awake's seconds are real in production)

    def test_status_reads_never_reach_the_wake_code(self):
        h = self.handler_for('llama', 'GET', '/v1/models', b'')
        with patch.object(g.pc_net, 'pc_awake', side_effect=AssertionError('a status read tried to wake the PC')), \
             patch.object(g.pc_net, 'wake_pc', side_effect=AssertionError('a status read woke the PC')):
            h.forward()
        self.assertIn(b'The gaming PC did not answer', h.wfile.getvalue())  # dead upstream, but wake code never ran
        self.assertEqual(self.rows, [])  # a GET records no generation history


if __name__ == '__main__':
    unittest.main()
