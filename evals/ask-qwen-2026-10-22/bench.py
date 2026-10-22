import json, sys, time, functools
sys.path.insert(0, '/mnt/disk1/.homelab-nightly')
import runner as r, qwen_service as q
qs = json.load(open('/tmp/bench_qs.json'))
CONFIGS = {'4K': (4096, 2750, 1152), '6K': (6144, 4550, 1400)}
orig_cmd, orig_body = r.container_command, q.body_for
out = []
for name, (ctx, budget, maxtok) in CONFIGS.items():
    q.INPUT_BUDGET = budget
    r.container_command = lambda context_size=2048, _c=ctx: orig_cmd(context_size=_c)
    q.body_for = lambda p, e, _m=maxtok: dict(orig_body(p, e), max_tokens=_m)
    for key, prompt in qs:
        b = q.Broker()
        b.submit.__func__  # noqa
        # bypass the 3,000/1,000 char check so long questions are tested on both
        import uuid, fcntl, threading
        lock = open(r.ROOT / 'run.lock', 'a')
        for _ in range(120):
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError: time.sleep(5)
        b.job = dict(status='queued', id=uuid.uuid4().hex, prompt=q.scrub(prompt.strip()), model='Qwen3.5-4B', updated_at=int(time.time()), started_at=int(time.time()))
        t0 = time.monotonic(); b.work(lock); s = b.state()
        row = dict(config=name, q=key, chars=len(prompt), status=s.get('status'), error=s.get('error'),
                   input_tokens=s.get('input_tokens'), sources=s.get('context_sources'), trimmed=s.get('context_omissions'),
                   perf=s.get('performance'), total_s=s.get('total_seconds'), answer=s.get('answer'))
        out.append(row)
        print(json.dumps({k: row[k] for k in ('config', 'q', 'status', 'input_tokens', 'total_s')}), flush=True)
        json.dump(out, open('/tmp/bench_out.json', 'w'))
