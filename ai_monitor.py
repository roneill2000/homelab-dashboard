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


def desktop():
    global _token_samples, _last_speed, _last_active_at, _last_active_model
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
    
    if loaded_model:
        try:
            base_url = config['url'].replace('/v1', '')
            slots_data = fetch(f"{base_url}/slots?model={loaded_model}", config['token'])
            if isinstance(slots_data, list):
                active_slot = next((s for s in slots_data if s.get('is_processing')), None)
                now = time.time()
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
        active_model=loaded_model if is_active else None
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {k: pool.submit(fn) for k, fn in [('webui', webui), ('desktop', desktop)]}
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
