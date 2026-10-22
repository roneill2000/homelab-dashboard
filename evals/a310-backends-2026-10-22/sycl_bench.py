import json, subprocess, sys, time
sys.path.insert(0, '/mnt/disk1/.homelab-nightly')
import runner as r
NAME = 'homelab-bench-llm'; API = 'http://127.0.0.1:11439'
SYCL = 'ghcr.io/ggml-org/llama.cpp@sha256:2d162b8160ea2e023c51fd7abff0b3d39ad273c2350bbd39902e422b8132c7e9'
def cmd(backend, ctx):
    c = r.container_command(context_size=ctx)
    c[c.index('--name') + 1] = NAME
    c[c.index('-p') + 1] = '127.0.0.1:11439:8000'
    if backend == 'sycl':
        i = c.index('--device'); c[i:i + 2] = ['--device', '/dev/dri/card1', '--device', '/dev/dri/renderD129', '-e', 'ONEAPI_DEVICE_SELECTOR=level_zero:0']
        img = [x for x in c if x.startswith('ghcr.io/ggml-org/llama.cpp@')][0]; c[c.index(img)] = SYCL
        j = c.index('Vulkan0'); del c[j - 1:j + 1]
        c[c.index('--memory') + 1] = '7g'
    return c
def gone():
    for _ in range(60):
        if subprocess.run(['docker', 'inspect', NAME], capture_output=True).returncode: return
        subprocess.run(['docker', 'rm', '-f', NAME], capture_output=True); time.sleep(1)
def run(prompt, n):
    body = dict(model=r.MODEL, max_tokens=n, temperature=0.2, seed=1, chat_template_kwargs={'enable_thinking': False},
                messages=[dict(role='user', content=prompt)])
    import urllib.error
    try: res = r.fetch(API + '/v1/chat/completions', body, timeout=900)
    except urllib.error.HTTPError as e: return dict(error=e.code, body=e.read().decode(errors='replace')[:300])
    t = res['timings']
    return dict(prompt_n=t.get('prompt_n'), prompt_tps=round(t.get('prompt_per_second', 0), 1), gen_n=t.get('predicted_n'), gen_tps=round(t.get('predicted_per_second', 0), 1))
lock = open(r.ROOT / 'run.lock', 'a')
if not r.acquire_run_lock(lock, scheduled=True, timeout=300): sys.exit('A310 busy')
try:
    r.stop_model()
    for backend in ('sycl', 'vulkan'):
        gone(); t0 = time.monotonic()
        p = subprocess.run(cmd(backend, 4096), capture_output=True, text=True, timeout=120)
        if p.returncode: print(backend, 'start failed', p.stderr[-400:]); continue
        ok = False
        while time.monotonic() - t0 < 1200:
            try:
                if any(x.get('id') == r.MODEL for x in r.fetch(API + '/v1/models', timeout=3).get('data', [])): ok = True; break
            except Exception: pass
            if subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', NAME], capture_output=True, text=True).stdout.strip() != 'true': break
            time.sleep(3)
        load = round(time.monotonic() - t0)
        if not ok:
            print(backend, 'did not load', load, 's'); print(subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True).stderr[-1500:]); continue
        logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True).stderr
        dev = [l.strip()[:140] for l in logs.splitlines() if any(k in l for k in ('SYCL0', 'Vulkan0', 'Arc', 'offloaded'))][:4]
        print(backend, 'loaded in', load, 's', dev, flush=True)
        short = run('Explain in about 250 words how a home NAS parity disk protects data.', 300)
        long_text = ('2026-10-22T09:13:51 GL-BE9300 dnsmasq-dhcp[1]: DHCPACK(br-lan) 192.168.0.80 c4:f7:c1:3d:1a:86 Bedroom-2\n' * 60)
        print(backend, 'short', short, flush=True)
        long = run(long_text + '\nSummarize the log above in two sentences.', 120)
        warm = run('Explain in about 250 words how a home NAS parity disk protects data.', 300)
        print(json.dumps(dict(backend=backend, load_s=load, device=dev, short=short, long=long, short_again=warm)), flush=True)
finally:
    gone(); lock.close()
