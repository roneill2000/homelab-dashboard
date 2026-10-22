import json, sys, time, urllib.request
sys.path.insert(0, '/mnt/disk1/.homelab-nightly')
import runner as r, qwen_service as q, qwen_context
key = open('/tmp/.k27').read().strip()
qs = json.load(open('/tmp/bench_qs.json'))
out = []
for k, prompt in qs:
    t0 = time.monotonic()
    try:
        storage = r.fetch('http://192.168.0.2/api/storage', timeout=20)
        try: telemetry = r.fetch('http://192.168.0.2/api/telemetry', timeout=15)
        except Exception: telemetry = {}
        snapshot = {}; services = {}
        if 'logs' in qwen_context.topics(prompt):
            try: snapshot = r.fetch('http://192.168.0.3:8999/api/nightly-snapshot', timeout=30)
            except Exception: pass
            services = r.measured_services()
        fleet = {}
        if set(qwen_context.topics(prompt)) & {'mounts', 'units', 'containers', 'updates'}:
            try: fleet = r.fetch('http://192.168.0.2/api/fleet', timeout=60)
            except Exception: pass
        now = time.time()
        ev = qwen_context.build(prompt, storage, telemetry, snapshot, now, q.context(storage, now), services, fleet)
        body = q.body_for(prompt, ev); body.update(model='qwen3.8-27b', max_tokens=6000)
        req = urllib.request.Request('http://192.168.0.12:8080/v1/chat/completions', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        t1 = time.monotonic()
        res = json.load(urllib.request.urlopen(req, timeout=900))
        c = res['choices'][0]; u = res.get('usage', {}); tm = res.get('timings', {})
        row = dict(config='27B', q=k, status='complete' if c.get('finish_reason') == 'stop' else 'cut:' + str(c.get('finish_reason')),
                   input_tokens=u.get('prompt_tokens'), perf=dict(tokens_per_second=tm.get('predicted_per_second'), output_tokens=u.get('completion_tokens')),
                   total_s=round(time.monotonic() - t0, 1), answer=c['message'].get('content', '').strip())
    except Exception as e:
        row = dict(config='27B', q=k, status='failed', error=str(e)[:200], total_s=round(time.monotonic() - t0, 1))
    out.append(row); json.dump(out, open('/tmp/bench27_out.json', 'w'))
    print(json.dumps({x: row.get(x) for x in ('q', 'status', 'input_tokens', 'total_s')}), flush=True)
