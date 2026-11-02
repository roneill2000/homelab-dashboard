"""Local AI request history: what each generation request through the GPU broker cost (model swap, wait for the first
output, prompt reading and writing speed). gpu_broker.py writes one row per request; the dashboard reads a summary
for the Local AI page. Speeds come from the engines' own `timings`; nothing is estimated. Kept 90 days."""
import json
import os
import sqlite3
import statistics
import time

DB = os.environ.get('HOMELAB_METRICS_DB', '/var/lib/homelab-dashboard/metrics.sqlite3')
RETENTION = 90 * 86400
TAIL = 256 * 1024  # bytes of each response kept to find the final timings
COLS = ('ts', 'engine', 'model', 'ok', 'status', 'swap', 'swap_s', 'ttfb_s', 'wall_s',
        'prompt_n', 'cache_n', 'prompt_tps', 'gen_n', 'gen_tps', 'draft_n', 'draft_ok')
MIN_PROMPT = 500  # prompt speed is only meaningful for prompts at least this long


def connect(path=None):
    db = sqlite3.connect(path or DB, timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    return db


def initialize(connect=connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS ai_requests (ts REAL, engine TEXT, model TEXT, ok INTEGER, status INTEGER, '
                   'swap TEXT, swap_s REAL, ttfb_s REAL, wall_s REAL, prompt_n INTEGER, cache_n INTEGER, prompt_tps REAL, '
                   'gen_n INTEGER, gen_tps REAL, draft_n INTEGER, draft_ok INTEGER)')
        db.execute('CREATE INDEX IF NOT EXISTS ai_requests_ts ON ai_requests(ts)')


def number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None


def model_of(body):
    try:
        m = json.loads(body).get('model')
        return m[:80] if isinstance(m, str) else None
    except Exception:
        return None


def timings(tail):
    """Speeds from the end of a response: a plain JSON body, or the last SSE `data:` chunk that carries `timings`
    (llama-server and Strata both send them), falling back to OpenAI-style `usage` token counts."""
    text = (tail or b'').decode('utf-8', 'replace')
    found = []
    if text.strip().startswith('{'):
        found.append(text.strip())
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith('data:') and line[5:].strip().startswith('{'):
            found.append(line[5:].strip())
    usage = None
    for raw in found:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        t = obj.get('timings')
        if isinstance(t, dict):
            return dict(prompt_n=number(t.get('prompt_n')), cache_n=number(t.get('cache_n')), prompt_tps=number(t.get('prompt_per_second')),
                        gen_n=number(t.get('predicted_n')), gen_tps=number(t.get('predicted_per_second')),
                        draft_n=number(t.get('draft_n')), draft_ok=number(t.get('draft_n_accepted')))
        if usage is None and isinstance(obj.get('usage'), dict):
            usage = obj['usage']
    if usage:
        return dict(prompt_n=number(usage.get('prompt_tokens')), gen_n=number(usage.get('completion_tokens')))
    return {}


def record(row, connect=connect, now=None):
    now = now or time.time()
    with connect() as db:
        db.execute(f'INSERT INTO ai_requests ({",".join(COLS)}) VALUES ({",".join("?" for _ in COLS)})', [row.get(c) for c in COLS])
        db.execute('DELETE FROM ai_requests WHERE ts<?', (now - RETENTION,))


def median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def snapshot(connect=connect, now=None, recent=20):
    now = now or time.time()
    with connect() as db:
        rows = [dict(zip(COLS, r)) for r in db.execute(f'SELECT {",".join(COLS)} FROM ai_requests WHERE ts>=? ORDER BY ts', (now - 14 * 86400,))]
        first = db.execute('SELECT MIN(ts) FROM ai_requests').fetchone()[0]
    week = [r for r in rows if r['ts'] >= now - 7 * 86400]
    engines = {}
    for e in sorted({r['engine'] for r in week}):
        rs = [r for r in week if r['engine'] == e]
        ok = [r for r in rs if r['ok']]
        swaps = [r['swap_s'] for r in rs if r['swap']]
        drafts = [r for r in ok if r['draft_n']]
        engines[e] = dict(requests=len(rs), failed=len(rs) - len(ok), gen_tps=median(r['gen_tps'] for r in ok),
                          prompt_tps=median(r['prompt_tps'] for r in ok if (r['prompt_n'] or 0) >= MIN_PROMPT),
                          ttfb_s=median(r['ttfb_s'] for r in ok), swaps=len(swaps), swap_s=median(swaps),
                          draft_pct=round(100 * sum(r['draft_ok'] or 0 for r in drafts) / sum(r['draft_n'] for r in drafts), 1) if drafts else None)
    days = {}
    for r in rows:
        if not r['ok']:
            continue
        d = days.setdefault((time.strftime('%Y-%m-%d', time.localtime(r['ts'])), r['engine']), [])
        d.append(r)
    daily = [dict(date=d, engine=e, requests=len(rs), gen_tps=median(r['gen_tps'] for r in rs),
                  prompt_tps=median(r['prompt_tps'] for r in rs if (r['prompt_n'] or 0) >= MIN_PROMPT)) for (d, e), rs in sorted(days.items())]
    return dict(recent=list(reversed(rows[-recent:])), engines=engines, daily=daily, history_since=first, min_prompt=MIN_PROMPT)
