#!/usr/bin/env python3
"""Replay the GPU session-queue lease rules over real request history.

Reads a copy of the dashboard metrics database (ai_requests rows), replays every
generation request in arrival order through the same lease state machine the
broker will run (QUEUE_ENABLED=1): lease per session, FIFO queue, idle 600 s,
done/waiting grace 600 s, fairness demote at 1800 s, max hold 4 h, pass-through
clients never take or move the lease. Reports engine swaps and re-read tokens
before and after, plus queue wait percentiles, so Ryan can watch the effect on
real weeks of traffic before the flag is switched on.

Usage:  ssh proxmox "pct exec 104 -- cat /var/lib/homelab-dashboard/metrics.sqlite3" > /tmp/m.sqlite3
        python3 scripts/replay_queue.py /tmp/m.sqlite3 [--since 7d]
Rows written after the queue deploy carry `client` (real pass-through marks);
older rows are attributed by heuristic (see CAVEATS printed at the end).
"""
import argparse
import datetime
import math
import sqlite3
import sys

GRACE_DONE = GRACE_WAITING = GRACE_ASK = 600
IDLE, FAIR, MAXHOLD = 600, 1800, 4 * 3600
PASS_THROUGH_IPS = {'192.168.0.16', '192.168.0.4', '192.168.0.3'}  # same set gpu_broker.PASS_THROUGH uses
SESSION_GAP = 7200  # heuristic for header-less rows: same family, >2 h apart = new session (--gap to change)
QUANT_SUFFIX = ('-iq1_m', '-iq3_xxs', '-iq2_xs', '-a4b', '-q3_k_xl', '-mtp', '-fast')


def family(model):
    m = (model or 'x').lower()
    for suf in QUANT_SUFFIX:
        if m.endswith(suf):
            m = m[:-len(suf)]
    return m


def is_pass_through(row, attributed_by):
    # Rows written after the queue deploy carry the client IP, so the replay can
    # decide pass-through exactly as gpu_broker.PASS_THROUGH does. For header-less old rows the Ask-box models (Unraid) are the
    # only pass-through traffic that can be recognised at all.
    if row['pt'] is not None:
        return row['pt']
    return row['model'] in ('gemma-4-26b-a4b', 'swift-1.5-iq2_xs', 'swift-1.5-iq3_xxs')


def load(db, since_s):
    rows = []
    cols = {r[1] for r in db.execute('PRAGMA table_info(ai_requests)')}
    client_sel = 'client' if 'client' in cols else 'NULL'
    cut = db.execute('select max(ts) from ai_requests').fetchone()[0] - since_s
    sql = ("select ts, engine, model, ok, prompt_n, cache_n, %s from ai_requests "
           "where ts >= ? and engine in (%s) order by ts") % (client_sel,
           ",".join("'%s'" % e for e in ('coder', 'strata', 'llama')))
    for (ts, engine, model, ok, prompt_n, cache_n, client) in db.execute(sql, (cut,)):
        if not ok or not prompt_n:
            continue
        rows.append(dict(ts=ts, engine=engine, model=model, family=family(model),
                         prompt_n=prompt_n, cache_n=cache_n or 0,
                         pt=(client in PASS_THROUGH_IPS) if client else None, client=client))
    return rows


def sessions(rows):
    """Attribute sessions: header rows use their real key tag; old rows fall back to
    (model family) split by >2 h gaps - the phase-1 heuristic."""
    last = {}
    sid = {}
    nxt = 0
    for r in rows:
        if r['client']:  # tagged rows: the broker recorded the session key itself
            key = ('k', r['client'], r['client'])
        else:
            key = ('f', r['family'])
            prev = last.get(key)
            if prev is not None and r['ts'] - prev > SESSION_GAP:
                sid[key] = None  # break the session
        if sid.get(key) is None:
            nxt += 1
            sid[key] = 's%d' % nxt
        r['session'] = sid[key]
        last[key] = r['ts']
    return len(set(r['session'] for r in rows))


# NOTE: kept deliberately close to gpu_broker.lease_* so the rules stay one-to-one;
# implemented as a class to share state instead of module globals.


class Sim:
    def __init__(self, rows):
        self.lease = dict(key=None, since=0.0, last_req=0.0, release_at=math.inf, client=None)
        self.queue = []
        self.pending = {}      # session -> list of waiting requests (arrival order)
        self.arrived_at = {}   # session -> ts it entered the queue
        self.warm = {}         # engine -> session id of the last served request
        self.ctx = {}          # session -> max prompt_n seen
        self.engine = None     # engine currently loaded (swap when different)
        self.real_swaps = self.sim_swaps = 0
        self.real_pay = self.sim_pay = 0
        self.waits = []
        self.rows = rows

    def tick(self, now):
        L = self.lease
        if not L['key']:
            if self.queue and self.pending.get(self.queue[0]):
                self.grant(self.queue.pop(0), now)
            return
        reason = None
        if now - L['since'] >= MAXHOLD:
            reason = 'max'
        elif now - L['since'] >= FAIR and self.queue:
            reason = 'fairness'
        elif now - L['last_req'] >= IDLE and self.queue:
            reason = 'idle'
        elif now >= L['release_at'] and self.queue:
            reason = 'grace'
        if reason:
            old = L['key']
            if reason == 'fairness':
                self.queue.append(old)  # demoted holder goes to the back
            L.update(key=None)
            if self.queue:
                head = self.queue.pop(0)
                self.grant(head, now)
                # the demoted holder must re-read when it returns: mark its cache gone
                self.warm = {e: None for e, s in self.warm.items() if s == old}

    def grant(self, sess, now):
        self.lease.update(key=sess, since=now, last_req=now, release_at=math.inf)
        reqs = self.pending.pop(sess, None)
        if reqs:
            for r in reqs:
                self.waits.append(r['ts'] if False else now - r['arrival'])
            self.serve_all(sess, reqs)

    def serve(self, r):
        if self.engine != r['engine']:
            self.engine = r['engine']
            self.sim_swaps += 1
        warm = self.warm.get(r['engine']) == r['session']
        c = self.ctx.get(r['session'], 0)
        sim_cache = c if warm else 0
        if r['cache_n'] < r['prompt_n'] / 2 and r['prompt_n'] >= 500:
            self.real_pay += r['prompt_n'] - r['cache_n']
        if sim_cache < r['prompt_n'] / 2:
            self.sim_pay += r['prompt_n'] - sim_cache
        self.warm[r['engine']] = r['session']
        self.ctx[r['session']] = max(c, r['prompt_n'])

    def serve_all(self, sess, reqs):
        for r in reqs:
            self.serve(r)

def run(rows, hours=168):
    sim = Sim(rows)
    prev = None
    for r in rows:
        if prev is not None and r['ts'] - prev > 0:
            sim.tick(r['ts'])
        prev = r['ts']
        if is_pass_through(r, None):
            sim.serve(r)
            continue
        L = sim.lease
        if L['key'] is None:
            sim.grant(r['session'], r['ts'])
            sim.serve(r)
            sim.lease['release_at'] = math.inf  # working; idle/grace decides below
        elif L['key'] == r['session']:
            sim.tick(r['ts'])
            L['last_req'] = r['ts']
            sim.serve(r)
        else:
            if r['session'] not in sim.queue and r['session'] not in sim.pending:
                sim.queue.append(r['session'])
            r['arrival'] = r['ts']
            sim.pending.setdefault(r['session'], []).append(r)
    # count real swaps over the same row order
    eng = None
    for r in rows:
        if eng != r['engine']:
            eng = r['engine']
            sim.real_swaps += 1
    return sim


def pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('db')
    ap.add_argument('--since', default='7d')
    ap.add_argument('--gap', type=float, default=None, help='session gap seconds for header-less rows')
    args = ap.parse_args()
    if args.gap:
        global SESSION_GAP
        SESSION_GAP = args.gap
    m = __import__('re').match(r'(\d+)d', args.since)
    since = int(m.group(1)) * 86400 if m else 7 * 86400
    db = sqlite3.connect(args.db)
    rows = load(db, since)
    nsess = sessions(rows)
    sim = run(rows)
    print(f'rows replayed: {len(rows)}  sessions attributed: {nsess}  '
          f'sessions served from queue: {len(sim.waits)}')
    print(f'real: swaps {sim.real_swaps}  re-read tokens {sim.real_pay/1e6:.1f} M')
    print(f'sim : swaps {sim.sim_swaps} ({max(0, sim.real_swaps-sim.sim_swaps)} saved)  '
          f're-read tokens {sim.sim_pay/1e6:.1f} M  saved '
          f'{(sim.real_pay-sim.sim_pay)/1e6:.1f} M '
          f'({100*(sim.real_pay-sim.sim_pay)/max(1,sim.real_pay):.0f}%)')
    ws = [w for w in sim.waits]
    if ws:
        print(f'queued requests: {len(ws)}  wait median {pct(ws, 0.5):.0f} s  '
              f'p90 {pct(ws, 0.9):.0f} s  max {max(ws):.0f} s')
    print('CAVEATS: pre-queue rows carry no session key (family+gap heuristic; Open WebUI '
          'and pi sessions merge the same way phase 1 merged them); pass-through only '
          'recognised for Ask-box models; waits count time to grant, the live cap is '
          'QUEUE_HOLD_S 120 s with a 503 and a kept queue place.')


if __name__ == '__main__':
    main()
