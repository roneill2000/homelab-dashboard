import json, subprocess, sys, time, urllib.error
sys.path.insert(0, '/mnt/disk1/.homelab-nightly')
import runner as r
NAME = 'homelab-bench-llm'; API = 'http://127.0.0.1:11439'
AOT = 'homelab/llama-sycl-a310:b11804'
SYCL = 'ghcr.io/ggml-org/llama.cpp@sha256:2d162b8160ea2e023c51fd7abff0b3d39ad273c2350bbd39902e422b8132c7e9'
M = {'q4km': '/models/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf', 'iq4xs': '/models/test/Qwen3.5-4B-IQ4_XS.gguf',
     'iq3xxs': '/models/test/Qwen3.5-4B-UD-IQ3_XXS.gguf'}
DRAFT = '/models/test/Qwen3.5-0.8B-Q4_K_M.gguf'
def cmd(backend, model, draft=False, ctx=6144):
    c = r.container_command(context_size=ctx)
    if draft: c.remove('--rm')
    c[c.index('--name') + 1] = NAME; c[c.index('-p') + 1] = '127.0.0.1:11439:8000'
    c[c.index('--model') + 1] = M[model]
    if backend != 'vulkan':
        i = c.index('--device'); c[i:i + 2] = ['--device', '/dev/dri/card1', '--device', '/dev/dri/renderD129', '-e', 'ONEAPI_DEVICE_SELECTOR=level_zero:0']
        img = [x for x in c if x.startswith('ghcr.io/ggml-org/llama.cpp@')][0]; c[c.index(img)] = AOT if backend == 'aot' else SYCL
        j = c.index('Vulkan0'); del c[j - 1:j + 1]; c[c.index('--memory') + 1] = '7g'
    if draft == 'draft':
        c += ['--spec-type', 'draft-simple', '--spec-draft-model', DRAFT, '--spec-draft-ngl', 'all', '--spec-draft-n-max', '8']
        if backend == 'vulkan': c += ['--spec-draft-device', 'Vulkan0']
    elif draft:
        c += ['--spec-type', draft]
    return c
def gone():
    for _ in range(60):
        if subprocess.run(['docker', 'inspect', NAME], capture_output=True).returncode: return

        subprocess.run(['docker', 'rm', '-f', NAME], capture_output=True); time.sleep(1)
def run(prompt, n):
    body = dict(model=r.MODEL, max_tokens=n, temperature=0.2, seed=1, chat_template_kwargs={'enable_thinking': False},
                messages=[dict(role='user', content=prompt)])
    try: res = r.fetch(API + '/v1/chat/completions', body, timeout=900)
    except urllib.error.HTTPError as e: return dict(error=e.code, body=e.read().decode(errors='replace')[:200])
    except Exception as e:
        time.sleep(2)
        log = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True).stderr
        return dict(error=type(e).__name__, log=log[-900:])
    t = res['timings']
    out = dict(prompt_n=t.get('prompt_n'), prompt_tps=round(t.get('prompt_per_second', 0), 1), gen_n=t.get('predicted_n'), gen_tps=round(t.get('predicted_per_second', 0), 1))
    if t.get('draft_n'): out['draft_accept'] = f"{t.get('draft_n_accepted')}/{t.get('draft_n')}"
    return out
SHORT = 'Explain in about 250 words how a home NAS parity disk protects data.'
LONG = ('2026-10-22T09:13:51 GL-BE9300 dnsmasq-dhcp[1]: DHCPACK(br-lan) 192.168.0.80 c4:f7:c1:3d:1a:86 Bedroom-2\n' * 35) + '\nList each distinct device in the log above with its IP and say how often it renews. Use a short table.'
CASES = [('aot', 'q4km', 'draft'), ('aot', 'q4km', 'draft-mtp'), ('aot', 'q4km', 'ngram-mod'), ('aot', 'q4km', False)]
lock = open(r.ROOT / 'run.lock', 'a')
if not r.acquire_run_lock(lock, scheduled=True, timeout=600): sys.exit('A310 busy')
try:
    r.stop_model()
    for backend, model, draft in CASES:
        gone(); t0 = time.monotonic(); label = f'{backend}/{model}' + (f'+{draft}' if draft else '')
        p = subprocess.run(cmd(backend, model, draft), capture_output=True, text=True, timeout=120)
        if p.returncode: print(json.dumps(dict(case=label, error='start: ' + p.stderr[-300:])), flush=True); continue
        ok = False
        while time.monotonic() - t0 < 900:
            try:
                if r.fetch(API + '/v1/models', timeout=3).get('data'): ok = True; break
            except Exception: pass
            if subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', NAME], capture_output=True, text=True).stdout.strip() != 'true': break
            time.sleep(2)
        load = round(time.monotonic() - t0)
        if not ok:
            time.sleep(1)
            log = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True).stderr
            print(json.dumps(dict(case=label, load_s=load, error='did not load', log=log[-600:])), flush=True); continue
        print(json.dumps(dict(case=label, load_s=load, short=run(SHORT, 300), long=run(LONG, 300))), flush=True)
finally:
    gone(); lock.close()
