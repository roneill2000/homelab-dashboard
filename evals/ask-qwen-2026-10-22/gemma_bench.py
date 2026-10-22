import json, subprocess, sys, time, uuid, fcntl
sys.path.insert(0, '/mnt/disk1/.homelab-nightly')
import runner as r, qwen_service as q, gpu_probe
GEMMA = 'gemma-4-E4B-it-Q4_K_M'
r.MODEL = GEMMA; r.MODEL_FILE = 'test/gemma-4-E4B-it-Q4_K_M.gguf'
orig = r.container_command
OT = sys.argv[1] if len(sys.argv) > 1 else 'per_layer_token_embd=CPU'
def cmd(context_size=2048, reasoning_budget=768):
    c = orig(context_size=6144, reasoning_budget=256)
    c[c.index('--alias') + 1] = GEMMA
    c[c.index('--model') + 1] = '/models/' + r.MODEL_FILE
    return c + (['--override-tensor', OT] if OT != 'none' else [])
r.container_command = cmd
qs = json.load(open('/tmp/bench_qs.json'))
# 1) load + VRAM + speed
lock = open(r.ROOT / 'run.lock', 'a')
while True:
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
    except BlockingIOError: time.sleep(5)
r.stop_model(); t0 = time.monotonic()
subprocess.run(cmd(), check=True, stdout=subprocess.DEVNULL, timeout=60)
ok = False
while time.monotonic() - t0 < 600:
    try:
        if any(x.get('id') == GEMMA for x in r.fetch(r.API + '/v1/models', timeout=3).get('data', [])): ok = True; break
    except Exception: pass
    if subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', r.NAME], capture_output=True, text=True).stdout.strip() != 'true': break
    time.sleep(2)
logs = subprocess.run(['docker', 'logs', r.NAME], capture_output=True, text=True).stderr
print(json.dumps(dict(stage='load', ok=ok, load_s=round(time.monotonic() - t0), gpu=gpu_probe.snapshot().get('latest'),
                      log=[l.strip()[:150] for l in logs.splitlines() if any(k in l for k in ('buffer size', 'per_layer', 'offload', 'error', 'failed', 'OUT_OF'))][:12])), flush=True)
if ok:
    body = dict(model=GEMMA, max_tokens=300, temperature=0.2, seed=1, messages=[dict(role='user', content='Explain in about 250 words how a home NAS parity disk protects data.')])
    t = r.fetch(r.API + '/v1/chat/completions', body, timeout=600)['timings']
    print(json.dumps(dict(stage='speed', gen_tps=round(t['predicted_per_second'], 1), prompt_tps=round(t['prompt_per_second'], 1), gpu=gpu_probe.snapshot().get('latest'))), flush=True)
r.stop_model(); lock.close()
if not ok: sys.exit(1)
# 2) the 10 questions through the real Ask Qwen pipeline (A310 path, 6K, 256 thinking)
out = []
for key, prompt in qs:
    lock = open(r.ROOT / 'run.lock', 'a')
    while True:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError: time.sleep(5)
    b = q.Broker(); b.job = dict(status='queued', id=uuid.uuid4().hex, prompt=q.scrub(prompt.strip()), target='a310', model='gemma', updated_at=int(time.time()), started_at=int(time.time()))
    b.work(lock); s = b.state()
    row = dict(config='gemma-e4b', q=key, status=s.get('status'), error=s.get('error'), input_tokens=s.get('input_tokens'),
               perf=s.get('performance'), total_s=s.get('total_seconds'), load_s=s.get('load_seconds'), answer=s.get('answer'))
    out.append(row); json.dump(out, open('/tmp/gemma_out.json', 'w'))
    print(json.dumps({k: row[k] for k in ('q', 'status', 'total_s', 'load_s')}), flush=True)
