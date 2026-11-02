import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import ai_history as h

T = {'prompt_n': 2857, 'cache_n': 0, 'prompt_per_second': 209.9, 'predicted_n': 165, 'predicted_per_second': 52.2,
     'draft_n': 132, 'draft_n_accepted': 97}


class AiHistoryTests(unittest.TestCase):
    def test_timings_from_json_body_and_sse_stream(self):
        body = json.dumps({'choices': [{'message': {'content': 'hi'}}], 'timings': T}).encode()
        self.assertEqual(h.timings(body)['prompt_tps'], 209.9)
        sse = b''.join(b'data: ' + json.dumps(c).encode() + b'\n\n' for c in
                       [{'choices': [{'delta': {'content': 'a'}}]}, {'choices': [{'delta': {}}], 'timings': T}, {'choices': [], 'usage': {'prompt_tokens': 1}}])
        t = h.timings(sse + b'data: [DONE]\n\n')
        self.assertEqual((t['gen_n'], t['gen_tps'], t['draft_ok']), (165, 52.2, 97))

    def test_usage_fallback_truncated_and_garbage(self):
        self.assertEqual(h.timings(b'data: {"usage": {"prompt_tokens": 12, "completion_tokens": 3}}\n'), {'prompt_n': 12, 'gen_n': 3})
        self.assertEqual(h.timings(b'ens": 3}}\n\ndata: [DONE]'), {})
        self.assertEqual(h.timings(b''), {})
        self.assertEqual(h.timings(b'{"timings": {"prompt_per_second": -1, "predicted_n": true}}'), dict(prompt_n=None, cache_n=None, prompt_tps=None, gen_n=None, gen_tps=None, draft_n=None, draft_ok=None))

    def test_model_name(self):
        self.assertEqual(h.model_of(b'{"model": "qwen3.8-27b", "messages": []}'), 'qwen3.8-27b')
        self.assertIsNone(h.model_of(b'not json')); self.assertIsNone(h.model_of(b'{"model": 5}'))

    def test_summary_medians_swaps_and_short_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            connect = lambda: sqlite3.connect(Path(folder) / 'm.db')
            h.initialize(connect)
            now = 1_800_000_000
            rows = [dict(ts=now - 60, engine='strata', model='swift', ok=1, status=200, swap='unload-llama+load-strata', swap_s=40.0, ttfb_s=14.0, wall_s=20,
                         prompt_n=2857, prompt_tps=210.0, gen_n=165, gen_tps=60.0, draft_n=100, draft_ok=80),
                    dict(ts=now - 30, engine='strata', model='swift', ok=1, status=200, ttfb_s=1.0, wall_s=3, prompt_n=40, prompt_tps=30.0, gen_n=50, gen_tps=90.0, draft_n=50, draft_ok=40),
                    dict(ts=now - 20, engine='llama', model='qwen3.8-27b', ok=0, status=503, swap='game', wall_s=0.1),
                    dict(ts=now - 10 * 86400, engine='llama', model='old', ok=1, status=200, gen_tps=50.0)]
            for r in rows:
                h.record(r, connect, now)
            s = h.snapshot(connect, now)
            st = s['engines']['strata']
            self.assertEqual((st['requests'], st['failed'], st['swaps'], st['swap_s']), (2, 0, 1, 40.0))
            self.assertEqual(st['gen_tps'], 75.0); self.assertEqual(st['prompt_tps'], 210.0)  # the 40-token prompt is left out
            self.assertEqual(st['draft_pct'], 80.0)
            self.assertEqual(s['engines']['llama']['failed'], 1)  # the 10-day-old row is outside the 7-day summary
            self.assertEqual(s['recent'][0]['model'], 'qwen3.8-27b')
            later = now + h.RETENTION + 86400
            h.record(dict(ts=later, engine='llama', ok=1), connect, later)  # retention prunes the old rows
            self.assertEqual(connect().execute('SELECT COUNT(*) FROM ai_requests').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
