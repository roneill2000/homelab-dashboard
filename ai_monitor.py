"""Read-only local AI service and llama.cpp router inventory; no model loading."""
import concurrent.futures
import copy
import json
from pathlib import Path
import threading
import time
import urllib.request

CONFIG = Path('/etc/homelab-dashboard/ai.json')
_lock = threading.Lock()
_latest = {}
_last_poll = 0

# Rolling token-rate samples for active inference: [(timestamp, decoded_token_count, task_id), ...]
_token_samples = []
_last_speed = None
_last_active_at = 0
_last_active_model = None
_loop_started = False
# Every GET /slots counts as activity for llama.cpp's --sleep-idle-seconds, so polling it every 1.2 s kept the gaming
# PC's model loaded forever (27 Oct 2026). Poll it fast only while someone is watching (the Local AI page pings
# request_live()), otherwise only every WATCHDOG_INTERVAL, which is longer than the 300 s idle timer.
WATCHDOG_INTERVAL = 360
STUCK_SECONDS = 300
_live_until = 0
_last_slot_check = 0
_stuck = None        # {'key': (model, task id), 'decoded': n, 'since': t} while a slot is processing
_watchdog = {}       # last watchdog action, reported in the snapshot
# Gaming PC GPU readings (pc/gpu_metrics.py on the PC, NVML, cached 2 s there). Read only while someone is watching.
PC_GPU_URL = 'http://192.168.0.12:9835/gpu'
# Strata (Swift 1.5 / Qwen3.8-Flash-Next) on the gaming PC. /health is cheap and does not count as model activity.
STRATA_URL = 'http://192.168.0.12:8088/health'
_pc_gpu = {'latest': None, 'history': [], 'error': None, 'last_try': 0}
GPU_KEYS = ('power_w', 'power_limit_w', 'temperature_c', 'utilization_pct', 'memory_util_pct', 'vram_used_mib',
            'vram_total_mib', 'fan_pct', 'clock_mhz')


def request_live(seconds=30):
    global _live_until
    with _lock:
        _live_until = max(_live_until, time.time() + seconds)


def pc_gpu_poll(now):
    # At most every 2 s, only while watching. Keeps ~5 minutes of samples for the panel's chart.
    if now - _pc_gpu['last_try'] < 2:
        return
    _pc_gpu['last_try'] = now
    try:
        raw = fetch(PC_GPU_URL)
        if not isinstance(raw, dict) or raw.get('error'):
            raise ValueError('GPU reading failed')
        sample = {k: raw.get(k) for k in GPU_KEYS if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool)}
        sample['name'] = raw.get('name') if isinstance(raw.get('name'), str) else None
        sample['observed_at'] = int(now)
        _pc_gpu['latest'] = sample
        _pc_gpu['error'] = None
        _pc_gpu['history'] = (_pc_gpu['history'] + [{'ts': int(now), **{k: sample.get(k) for k in ('power_w', 'temperature_c', 'utilization_pct', 'vram_used_mib')}}])[-150:]
    except Exception:
        _pc_gpu['error'] = 'GPU metrics unavailable'


def live_snapshot():
    # Small payload for the Local AI page's 2 s ping: GPU readings, chart history and the generation speed.
    with _lock:
        d = copy.deepcopy(_latest.get('desktop', {}))
    return {'gpu': {'latest': _pc_gpu['latest'], 'history': list(_pc_gpu['history']), 'error': _pc_gpu['error']},
            'active_speed': d.get('active_speed'), 'is_active': d.get('is_active'), 'is_recent': d.get('is_recent'),
            'active_model': d.get('active_model'), 'online': d.get('online')}


def unload(base_url, token, model):
    body = json.dumps({'model': model}).encode('utf-8')
    req = urllib.request.Request(base_url + '/models/unload', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status


def fetch(url, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=4) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError('AI response exceeds limit')
    return json.loads(raw)


def positive(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 1000000000 else None


def models(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('Invalid model inventory')
    out = []
    seen = set()
    for entry in payload['data'][:64]:
        if not isinstance(entry, dict):
            continue
        name = entry.get('id')
        if not isinstance(name, str) or not name or len(name) > 200 or name in seen:
            continue
        seen.add(name)
        status = entry.get('status') or {}
        status = status if isinstance(status, dict) else {}
        state = status.get('value')
        state = state if state in ('loaded', 'unloaded', 'loading', 'unloading', 'sleeping', 'failed') else 'unknown'
        args = status.get('args') or []
        configured = None
        if isinstance(args, list):
            for i, arg in enumerate(args[:-1]):
                if arg in ('--ctx-size', '-c'):
                    try:
                        configured = positive(int(args[i + 1]))
                    except (ValueError, TypeError):
                        pass
        meta = entry.get('meta') or {}
        meta = meta if isinstance(meta, dict) else {}
        context = positive(meta.get('n_ctx')) if state == 'loaded' else None
        out.append(dict(id=name, state=state, context_tokens=context or configured,
                        context_source='runtime' if context else 'configured' if configured else 'unknown'))
    if payload['data'] and not out:
        raise ValueError('No valid model records')
    return out


def webui():
    result = fetch('http://192.168.0.16:3000/api/config')
    if result.get('name') != 'Open WebUI' or result.get('status') is not True:
        raise ValueError('Unexpected WebUI response')
    return dict(version=str(result.get('version', ''))[:40])


def strata():
    result = fetch(STRATA_URL)
    if result.get('service') != 'strata':
        raise ValueError('Unexpected Strata response')
    return dict(model=str(result.get('model') or '')[:80], loaded=result.get('loaded') is True,
                context_tokens=positive(result.get('max_context')), images=result.get('images') is True)


def desktop():
    global _token_samples, _last_speed, _last_active_at, _last_active_model, _last_slot_check, _stuck, _watchdog
    config = json.loads(CONFIG.read_text())
    if config.get('url') != 'http://192.168.0.12:8080/v1':
        raise ValueError('Unexpected model endpoint')
    
    # 1. Fetch model list
    model_list = models(fetch(config['url'] + '/models', config['token']))
    
    # 2. Check for active generation on the loaded model
    loaded_model = next((m['id'] for m in model_list if m['state'] == 'loaded'), None)
    active_speed = None
    is_active = False
    is_recent = False
    
    now = time.time()
    with _lock:
        watching = now < _live_until
    if watching:
        pc_gpu_poll(now)
    due = now - _last_slot_check >= WATCHDOG_INTERVAL
    if loaded_model and (watching or due):
        try:
            _last_slot_check = now
            base_url = config['url'].replace('/v1', '')
            slots_data = fetch(f"{base_url}/slots?model={loaded_model}&autoload=false", config['token'])
            if isinstance(slots_data, list):
                active_slot = next((s for s in slots_data if s.get('is_processing')), None)
                # Watchdog: a slot that stays "processing" with no new tokens for STUCK_SECONDS is stuck; unload the
                # model so the GPU stops working (the router reloads it on the next request).
                if active_slot:
                    tok = active_slot.get('next_token')
                    dec = tok[0].get('n_decoded', 0) if isinstance(tok, list) and tok else (tok or {}).get('n_decoded', 0) if isinstance(tok, dict) else 0
                    key = (loaded_model, active_slot.get('id_task'))
                    if _stuck and _stuck['key'] == key and _stuck['decoded'] == dec:
                        if now - _stuck['since'] >= STUCK_SECONDS:
                            try:
                                unload(base_url, config['token'], loaded_model)
                                _watchdog = dict(action='unloaded', model=loaded_model, at=int(now), stuck_seconds=int(now - _stuck['since']))
                            except Exception:
                                _watchdog = dict(action='unload_failed', model=loaded_model, at=int(now))
                            _stuck = None
                    else:
                        _stuck = dict(key=key, decoded=dec, since=now)
                else:
                    _stuck = None
                if active_slot:
                    is_active = True
                    is_recent = False
                    decoded = 0
                    tokens = active_slot.get('next_token')
                    if isinstance(tokens, list) and tokens:
                        decoded = tokens[0].get('n_decoded', 0)
                    task_id = active_slot.get('id_task')
                    
                    # Reset buffer if new task or counter rolled backwards
                    if _token_samples and (_token_samples[-1][2] != task_id or decoded < _token_samples[-1][1]):
                        _token_samples = []
                    
                    _token_samples.append((now, decoded, task_id))
                    # Retain samples strictly within the last 5.0 seconds
                    _token_samples = [s for s in _token_samples if now - s[0] <= 5.0]
                    
                    if len(_token_samples) >= 2:
                        dt = _token_samples[-1][0] - _token_samples[0][0]
                        dn = _token_samples[-1][1] - _token_samples[0][1]
                        if dt >= 0.1 and dn > 0:
                            active_speed = round(dn / dt, 1)
                        elif dn == 0 and _token_samples[-1][1] > 0 and dt > 0:
                            active_speed = round(_token_samples[-1][1] / max(0.5, dt), 1)
                    
                    if active_speed:
                        _last_speed = active_speed
                        _last_active_at = now
                        _last_active_model = loaded_model
                else:
                    _token_samples = []
                    # 5-second decay hold after generation completes
                    if _last_speed and (now - _last_active_at <= 5.0) and _last_active_model == loaded_model:
                        is_active = True
                        is_recent = True
                        active_speed = _last_speed
                    else:
                        is_active = False
                        is_recent = False
                        active_speed = None
        except Exception:
            _token_samples = []
    else:
        _token_samples = []
        
    return dict(
        models=model_list,
        active_speed=active_speed,
        is_active=is_active,
        is_recent=is_recent,
        active_model=loaded_model if is_active else None,
        live=watching,
        watchdog=dict(_watchdog) if _watchdog else None
    )


def collect(now=None, min_interval=1.0):
    global _last_poll
    now = now or time.time()
    with _lock:
        if now - _last_poll < min_interval:
            return
        _last_poll = now
        prior = copy.deepcopy(_latest)
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(fn) for k, fn in [('webui', webui), ('desktop', desktop), ('strata', strata)]}
        for key, future in futures.items():
            old = prior.get(key, {})
            try:
                value = future.result()
                result[key] = dict(value, online=True, last_attempt_at=int(now), last_success_at=int(now), error=None)
            except Exception:
                result[key] = dict(old, online=False, last_attempt_at=int(now), error='Source unavailable')
    with _lock:
        _latest.clear()
        _latest.update(result)


def start():
    global _loop_started
    with _lock:
        if _loop_started:
            return
        _loop_started = True
    def loop():
        while True:
            try:
                collect(min_interval=1.0)
            except Exception:
                pass
            time.sleep(1.2)
    t = threading.Thread(target=loop, name='ai-speed-collector', daemon=True)
    t.start()


def snapshot():
    with _lock:
        return copy.deepcopy(_latest)
