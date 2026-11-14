"""GPU broker: one engine on the gaming PC's 16 GB card at a time, switched automatically.

Listens on :8080 (llama-server router), :8088 (Strata, general model) and :8089 (Strata, Qwen3.8 Coder for coding agents)
on this host and forwards to the same ports on the PC. Before forwarding a generation request it makes sure the right
engine holds the GPU: every other engine is unloaded first (llama-server's models, the other Strata server), then a
Strata engine is loaded if it isn't. Status reads (/models, /health, /slots, ...)
pass straight through and never switch. Responses stream (SSE) as they arrive. Clients keep their own auth headers.

:8000 is one URL for every model (http://192.168.0.2:8000/v1): GET /v1/models lists all engines' models and a
generation request goes to the engine its `model` names, with the same switching. The broker adds llama-server's token
there, so clients configure one connection with no key.
"""
import http.client
import hashlib
import http.server
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.request

import ai_history
import ai_monitor
import pc_net

PC = '192.168.0.12'
PORTS = {8080: 'llama', 8088: 'strata', 8089: 'coder'}
# The two Strata servers (one model each; the coder one starts unloaded with --lazy). Only one is ever loaded.
STRATA = {'strata': 8088, 'coder': 8089}
# One URL for every local model (2 Nov 2026): http://192.168.0.2:8000/v1. The request's `model` picks the engine,
# GET /v1/models lists all of them, and the broker adds llama-server's token itself, so clients need no key.
UNIFIED_PORT = 8000
_names = {'at': 0.0, 'strata': '', 'coder': ''}
_names_lock = threading.Lock()
_llama_ids = {'at': 0.0, 'ids': []}
# Generation requests still streaming, per engine. A request for another engine waits (up to DRAIN_TIMEOUT) for these to
# finish before its swap, so a swap never cuts a reply mid-stream (Sonnet audit, 2 Nov 2026: ping-pong between clients).
_inflight = {'llama': 0, 'strata': 0, 'coder': 0}
_inflight_cv = threading.Condition()
DRAIN_TIMEOUT = 600
# Requests that generate (and so need their engine on the GPU). Anything else is forwarded as-is.
GENERATE = ('/v1/chat/completions', '/v1/completions', '/v1/messages', '/v1/embeddings', '/v1/responses',
            '/completion', '/completions', '/chat/completions', '/embedding', '/embeddings', '/infill')
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
       'upgrade', 'host', 'content-length'}
MAX_BODY = 64 * 1024 * 1024
_switch_lock = threading.Lock()
# Browsers attach Origin to cross-site requests; the broker's clients (Hermes, Open WebUI, the Qwen broker) are
# servers and send none. Host must name this machine, which stops DNS rebinding (a foreign name resolving here).
HOSTS = {'192.168.0.2', '127.0.0.1', 'localhost'}
# Strata's own chat page, served through Caddy (strata.lan -> here, so opening it also frees the GPU of llama models).
# Its page may call back to itself, so its own origin is the one browser origin accepted.
PAGES = {'strata.lan'}
# Only these machines may use the broker (7 Nov 2026 audit: any phone/TV/IoT device on the main Wi-Fi could otherwise
# drive the GPU with a plain script, which sends no Origin). Dashboard itself, Proxmox (alerts, benchmarks), Unraid
# (Ask box / nightly), Open WebUI, Caddy (strata.lan), Hermes.
CLIENTS = {'127.0.0.1', '192.168.0.2', '192.168.0.3', '192.168.0.4', '192.168.0.16', '192.168.0.20', '192.168.0.21'}


def client_allowed(address):
    return (address or '').removeprefix('::ffff:') in CLIENTS


# ---- GPU session queue (SPEC-session-queue, 14 Nov 2026; Phase 1 report session-queue-phase1-20261114) ----
# One lease for the whole GPU: the holder session's requests (same session key) go straight through and keep
# their prompt cache; a request from another session waits its turn instead of swapping the engine away. Off
# until Ryan sets Environment=QUEUE_ENABLED=1 on homelab-gpu-broker.service (systemctl edit) and restarts it;
# with the flag off nothing below changes behaviour (the new history columns stay None).
QUEUE_ENABLED = os.environ.get('QUEUE_ENABLED', '0').strip().lower() in ('1', 'true', 'on', 'yes')
LEASE_IDLE_S = 600       # no request from the holder for this long: idle, lease may pass (Phase 1 p99.9 was 316 s)
ASK_GRACE_S = 600        # holder keeps the lease this long after an ask card, then the queue may take it
DONE_GRACE_S = 600       # holder keeps the lease this long after a finished reply (71% of Ryan's replies came within)
FAIR_HOLD_S = 1800       # Ryan's fairness: >30 min held with someone waiting -> handover (the holder re-queues)
LEASE_MAX_S = 4 * 3600   # a lease with no progress (session died mid-turn) is cut after 4 h, queue or not
QUEUE_HOLD_S = 120       # a non-streaming request waits at most this long, then 503 + Retry-After
QUEUE_KEEPALIVE_S = 15   # SSE comment every 15 s while queued: undici (pi family) kills a silent stream at 300 s
# Pass-through clients (Ryan): never queue, never take or move the lease. They are served at once (evicting the
# holder's cache as today); the holder keeps its lease and its queue place. Decided per client in ONE constant.
PASS_THROUGH = {'192.168.0.16',   # Open WebUI: short interactive chats (a queued wait would die at its 600 s aiohttp
                '192.168.0.4',    #   total cap anyway; Ryan won't use it while things are queued)
                '192.168.0.3'}    #   Proxmox (homelab-why, alerts, benchmarks): same rule, nothing found against it
# Queued sessions wait FIFO by first request. Status reads, /queue/* and Strata's /load never queue. No phone ping
# for long waits (Ryan).


def client_ip(address):
    return (address or '').removeprefix('::ffff:')


def is_pass_through(address):
    return client_ip(address) in PASS_THROUGH


def model_family(model):
    """Model id without the quant tag: the cache is per conversation, not per quant, and switching between two quants
    of the same model must not look like a new session ('qwen3.8-flash-next-iq3_xxs' -> 'qwen3.8-flash-next')."""
    m = (model or '').strip().lower()
    return re.sub(r'-(i?[qa]\d+[a-z_]*|a\d+b)$', '', m) or m


def msg_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
    return ''


def session_key(headers, body, ip):
    """Stable id for the lease. First choice: the pi-family header x-session-affinity (Pi sends it once models.json
    sets compat.sendSessionAffinityHeaders; survives compaction). Fallback: hash of client IP + model family +
    system message + first user message. Phase 1 measured that fallback: stable across turns, NOT stable across
    compaction (the summary becomes the new first message, so the session re-queues - its cache is gone then
    anyway), and only the first user message separates two Pocket sessions - prefer the header whenever present."""
    aff = (headers.get('x-session-affinity') or '').strip()
    if aff and len(aff) <= 200:
        return aff
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    system = first_user = ''
    msgs = data.get('messages')
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get('role') == 'system' and not system:
                system = msg_text(m.get('content'))[:4000]
            elif isinstance(m, dict) and m.get('role') == 'user' and not first_user:
                first_user = msg_text(m.get('content'))[:4000]
    raw = '\x1f'.join((ip, model_family(data.get('model')), system, first_user))
    return 'fb:' + hashlib.sha256(raw.encode('utf-8', 'replace')).hexdigest()[:20]


# Lease state: the holder session plus its queue of waiters, one lock for both. A holder's state is kept from what
# its last response was: working (tool calls came back = the turn continues), waiting (an ask card opened for
# a person), done (the reply ended with no tool calls). Pass-through requests never read or write this.
_lease = {'key': None, 'client': None, 'since': 0.0, 'last_req': 0.0, 'release_at': float('inf'), 'state': None}
_queue = []  # FIFO sessions: [{'key': str, 'client': str, 'first': float}]
_lease_cv = threading.Condition()


def lease_release(now):
    """Why the holder may lose the lease now, or None. idle: no request for LEASE_IDLE_S. grace: a done/ask
    signal older than DONE/ASK_GRACE_S (release_at). max: LEASE_MAX_S of continuous holding. fairness: Ryan's
    hand-over - held past FAIR_HOLD_S while someone waits. idle/grace only matter with a waiting queue: with no
    waiters the lease simply stays and costs nothing (the next request takes it either way)."""
    if not _lease['key']:
        return None
    if now - _lease['since'] >= LEASE_MAX_S:
        return 'max'
    if now >= _lease['release_at'] and _queue:
        return 'grace'
    if now - _lease['last_req'] >= LEASE_IDLE_S and _queue:
        return 'idle'
    if now - _lease['since'] >= FAIR_HOLD_S and _queue:
        return 'fairness'
    return None


def lease_update(now, locked=False):
    """Under the lock: recheck the lease and hand it to the queue head when it expired; a fairness hand-over also
    puts the demoted holder at the back of the queue (it pays one re-read later). Wakes every parked waiter.
    locked=True: the caller already holds _lease_cv (lease_wait, queue_release); bare callers (replay, tests) take
    the lock themselves. No private Condition API: the caller states its ownership instead."""
    if locked:
        return _lease_update(now)
    with _lease_cv:
        return _lease_update(now)


def _lease_update(now):
    reason = lease_release(now)
    if reason is None:
        if not _lease['key'] and _queue:  # free lease + waiters (release endpoint, or released earlier): hand over
            reason = 'free'
        else:
            return
    old, old_client = _lease['key'], _lease['client']
    if reason == 'fairness':
        _queue.append({'key': old, 'client': old_client, 'first': now})
    _lease.update(key=None, client=None, since=0.0, last_req=0.0, release_at=float('inf'), state=None)
    if _queue:
        head = _queue.pop(0)
        _lease.update(key=head['key'], client=head['client'], since=now, last_req=now, release_at=float('inf'),
                      state='working')
    log(f"queue: lease {(old or 'none')[:24]} ({old_client}) -> {_lease['key'][:24] if _lease['key'] else 'free'} "
        f"({reason})")
    _lease_cv.notify_all()


def lease_grant(key, client, now):
    """A session with no holder running takes the lease and goes straight through. Lock-safe from anywhere."""
    with _lease_cv:
        if _lease['key']:  # raced: someone else took it a moment earlier
            return
        _lease.update(key=key, client=client, since=now, last_req=now, release_at=float('inf'), state='working')
        log(f'queue: lease to {client} ({key[:24]})')
        _lease_cv.notify_all()


def lease_end(now, state, key):
    """What a holder's finished response was: update release_at for done/ask (only while someone waits), else keep
    working. key must still be the holder - a pass-through or a hand-over may have changed that during the stream."""
    with _lease_cv:
        if _lease['key'] != key:
            return  # no longer the holder (hand-over or pass-through evicted it during the stream): nothing to note
        if state == 'waiting':
            _lease['state'] = 'waiting'
            _lease['release_at'] = now + ASK_GRACE_S
        elif state == 'done':
            _lease['state'] = 'done'
            _lease['release_at'] = now + DONE_GRACE_S
        else:
            _lease['state'] = 'working'
            _lease['release_at'] = float('inf')
        _lease_cv.notify_all()


def lease_snapshot():
    """GET /queue/status (never queued): who holds the GPU, who waits. Same small JSON the dashboard row shows."""
    with _lease_cv:
        now = time.time()
        holder = None
        if _lease['key']:
            holder = dict(session=_lease['key'], client=_lease['client'], state=_lease['state'],
                          hold_s=round(now - _lease['since'], 1), idle_s=round(now - _lease['last_req'], 1))
        return dict(enabled=bool(QUEUE_ENABLED), holder=holder,
                    queue=[dict(position=n + 1, session=q['key'], client=q['client'], age_s=round(now - q['first'], 1))
                           for n, q in enumerate(_queue)])


def finish_state(tail):
    """From the last 256 KB of a streamed reply: 'waiting' if an ask tool call is visible, 'done' if it ended with
    no tool calls (finish_reason stop/length), else working. A heuristic by design: the broker cannot see the client
    side (approval cards, ask answers), those show up later as idle or the ask-card release endpoint."""
    text = (tail or b'').decode('utf-8', 'replace')
    tool = '"tool_calls"' in text
    if tool and re.search(r'"(?:name|function)"\s*:\s*"(?:streamable-http__)?ask"', text):
        return 'waiting'
    if not tool and re.search(r'"finish_reason"\s*:\s*"(?:stop|length)"', text):
        return 'done'
    return 'working'


class GamePaused(RuntimeError):
    pass
_log_lock = threading.Lock()


def log(message):
    with _log_lock:
        print(time.strftime('%Y-%m-%d %H:%M:%S'), message, flush=True)


def needs_gpu(method, path):
    return method == 'POST' and path.split('?', 1)[0].rstrip('/') in GENERATE


def strata_loaded(engine='strata'):
    try:
        return ai_monitor.strata(STRATA[engine]).get('loaded') is True
    except Exception:
        return False  # a Strata server that isn't running holds no GPU


def game_running():
    return ai_monitor.game_running()


def llama_busy():
    try:
        config = json.loads(ai_monitor.CONFIG.read_text())
        return any(m['state'] in ('loaded', 'loading')
                   for m in ai_monitor.models(ai_monitor.fetch(config['url'] + '/models', config['token'])))
    except Exception:
        return False


# Broker wake-on-request (12 Nov 2026): a sleeping gaming PC is woken by the first generation request itself, so
# Ryan can leave it asleep overnight and still ask a model. Only generate/load requests wake; status reads never do.
WAKE_TIMEOUT = 90  # the whole budget: PC coming up plus the requested engine answering its health URL
WAKE_RESEND = 15   # re-send the magic packet at most this often while waiting (the NIC can miss one packet)
_wake_lock = threading.Lock()          # one wake sequence at a time; concurrent requests share it
_cancel = {'at': 0.0}                 # last sleep-countdown cancel (Part A rule: at most once per 60 s)
_sleep_requested_at = 0.0             # Part B hook: set when the broker itself asks the PC to sleep
# 'PC confirmed awake' cache: a generation request otherwise opens a fresh port-22 TCP probe per request, which shows
# up in the PC's OpenSSH log as one 'Connection closed by 192.168.0.3' per model call. A confirmed stamp is trusted for
# this long; a successful pc_awake() probe sets it, so does any upstream engine reply (a model answered -> the PC is up,
# no probe needed). A failed upstream connection clears it: that is the only cheap sign the PC may have slept since.
# The probe stays on port 22 only; the engine ports (8080/8088/8089) close while a game runs, so they cannot tell awake
# from asleep and must not become the cache's proof.
AWAKE_CACHE = 15.0
_awake = {'at': 0.0}


def confirm_awake():
    _awake['at'] = time.time()


def awake_cached():
    return time.time() - _awake['at'] < AWAKE_CACHE


def forget_awake():
    _awake['at'] = 0.0


class WakeTimeout(RuntimeError):
    pass


def engine_ready(engine):
    """True when the requested engine answers its cheap status URL (:8080/:8088/:8089 /health, the ones ai_monitor
    already uses; they do not count as model activity)."""
    try:
        if engine == 'llama':
            req = urllib.request.Request(f'http://{PC}:8080/health', headers={'Authorization': 'Bearer ' + llama_token()})
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        return isinstance(ai_monitor.strata(STRATA[engine]), dict)
    except Exception:
        return False


def ensure_awake(engine, timeout=WAKE_TIMEOUT, sleep=time.sleep):
    """Wake the gaming PC when a generation request finds it asleep. Fast path: awake -> None (~1 ms LAN TCP).
    Otherwise one wake sequence at a time under _wake_lock: magic packet, then poll pc_awake every 1 s (re-sending
    the packet every WAKE_RESEND s) until the PC answers, then wait within the same deadline for engine_ready.
    Returns the seconds spent waking; raises WakeTimeout on the deadline. A stamp set within AWAKE_CACHE s skips the
    probe entirely (returns None like an awake PC would); it is never trusted past that, so a PC that slept between
    requests is still woken on the next one."""
    if awake_cached():
        return None
    if pc_net.pc_awake(timeout=1):
        confirm_awake()
        return None
    deadline = time.time() + timeout
    with _wake_lock:
        if awake_cached() or pc_net.pc_awake(timeout=1):  # woken by a concurrent request while this one waited on the lock
            confirm_awake()
            return None
        t0 = time.time()
        pc_net.wake_pc()
        log('wake: sent')
        sent_at = t0
        while not pc_net.pc_awake(timeout=1):
            if time.time() >= deadline:
                raise WakeTimeout('the gaming PC did not come up')
            if time.time() - sent_at >= WAKE_RESEND:
                pc_net.wake_pc()
                log('wake: sent')
                sent_at = time.time()
            sleep(1)
        confirm_awake()  # the poll that ended the loop was a successful probe
        log(f'wake: PC up after {time.time() - t0:.0f} s')
        while not engine_ready(engine):
            if time.time() >= deadline:
                raise WakeTimeout('the engine did not answer its health URL')
            sleep(1)
        log(f'wake: engine ready after {time.time() - t0:.0f} s')
        return time.time() - t0


def maybe_cancel_sleep():
    """A generation request cancels a sleep countdown it is about to run into (Part B's, or the dashboard's Sleep).
    At most once per 60 s, never delaying the request: the cancel forced-command SSH runs in a thread. Cheap when no
    countdown is running. (Part B will only call this path when _sleep_requested_at is recent; for dashboard-initiated
    sleeps, cancelling once a minute while requests arrive is the accepted simplification.)"""
    if time.time() - _cancel['at'] < 60:
        return
    _cancel['at'] = time.time()
    threading.Thread(target=lambda: pc_net.sleep_pc(command='cancel'), daemon=True).start()


def drain(engine, timeout=DRAIN_TIMEOUT):
    """Wait until no other engine has a reply streaming; True when drained, False when the timeout passed first."""
    deadline = time.time() + timeout
    with _inflight_cv:
        while any(n for e, n in _inflight.items() if e != engine):
            left = deadline - time.time()
            if left <= 0:
                return False
            _inflight_cv.wait(min(left, 1.0))
    return True


def release(engine):
    with _inflight_cv:
        _inflight[engine] = max(0, _inflight[engine] - 1)
        _inflight_cv.notify_all()


def ensure(engine, hold=False):
    """Give the GPU to `engine` ('llama', 'strata' or 'coder'); serialised so concurrent requests cannot interleave a
    switch. Waits for other engines' streaming replies to finish, unloads every other engine, then loads a Strata engine
    if needed. With hold=True the caller is counted as streaming on `engine` (call release() when done), counted under
    the switch lock so no other swap can slip in between. Returns what it did, '+'-joined, or None."""
    did = []
    with _switch_lock:
        if not drain(engine):
            log(f'{engine} request: other engine still streaming after {DRAIN_TIMEOUT} s, switching anyway')
        with ai_monitor.gpu_switch():
            did = _switch(engine)
        if hold:
            with _inflight_cv:
                _inflight[engine] += 1
    return '+'.join(did) or None


def _switch(engine):
    """Unload every other engine, then load a Strata engine if needed (callers hold the switch locks)."""
    did = []
    for other, port in STRATA.items():
        if other != engine and strata_loaded(other):
            log(f'{engine} request: unloading {other}')
            did.append('unload-' + other)
            ai_monitor.strata_post('/unload', port=port)
            if not ai_monitor.wait_strata(False, timeout=120, port=port):
                raise RuntimeError(f'{other} did not unload')
    if engine in STRATA:
        if llama_busy():
            log(f'{engine} request: unloading llama-server models')
            did.append('unload-llama')
            ai_monitor.unload_llama()
        if not strata_loaded(engine):
            if game_running():
                raise GamePaused()
            log(f'{engine} request: loading it')
            did.append('load-' + engine)
            ai_monitor.strata_post('/load', port=STRATA[engine])
            if not ai_monitor.wait_strata(True, port=STRATA[engine]):
                raise RuntimeError(f'{engine} did not load')
    return did


def strata_names(max_age=60.0):
    """{'strata': model id, 'coder': model id} as each Strata server reports it ('' when it isn't running)."""
    with _names_lock:
        if time.time() - _names['at'] > max_age:
            for engine, port in STRATA.items():
                try:
                    _names[engine] = ai_monitor.strata(port).get('model') or ''
                except Exception:
                    pass  # keep the last known name: a restarting server is still the same model
            _names['at'] = time.time()
        return {e: _names[e] for e in STRATA}


def route(model, names=None):
    """The engine for a request's `model` on the unified port: a Strata server's own model id, else a Flash-Next name
    (coder or not), else llama-server, whose router knows its own models and answers unknown ones itself."""
    m = (model or '').strip().lower()
    names = strata_names() if names is None else names
    for engine in ('coder', 'strata'):
        if names.get(engine) and m == names[engine].lower():
            return engine
    if 'flash-next' in m or m.startswith('strata'):
        return 'coder' if 'coder' in m else 'strata'
    return 'llama'


LLAMA_EFFORT = {'off': 'none', 'none': 'none', 'minimal': 'low', 'low': 'low',
                'medium': 'medium', 'high': 'xhigh', 'xhigh': 'xhigh', 'max': 'xhigh'}


# Ryan, 11 Nov 2026: the Qwen 27B models (27B, 27B Long, Cold Fusion, Cold Fusion Fast) never run without reasoning.
# Without it they repeated one tool call 80-96 times in a row; every turn with thinking on was fine. So a request that
# asks for no thinking (reasoning_effort none/off, or enable_thinking false) gets medium instead. Gemma is not touched:
# its template has no levels and it is used for chat.
ALWAYS_THINK_PREFIX = 'qwen3.8-27b'


def llama_body(body):
    """Map reasoning_effort to the levels the 27B / Cold Fusion templates accept (low, medium, xhigh, or none)
    and mirror it into chat_template_kwargs so Cold Fusion never 500s on 'high'/'minimal'/'max'/'off'.
    For the Qwen 27B family, 'no thinking' in any form becomes medium (ALWAYS_THINK_PREFIX)."""
    if not body:
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    always = str(data.get('model') or '').startswith(ALWAYS_THINK_PREFIX)
    kwargs = data.get('chat_template_kwargs')
    kwargs = dict(kwargs) if isinstance(kwargs, dict) else {}
    raw = data.get('reasoning_effort')
    effort = LLAMA_EFFORT.get(raw.strip().lower()) if isinstance(raw, str) else None
    if always:
        if effort == 'none' or kwargs.get('enable_thinking') is False or kwargs.get('reasoning_effort') in ('none', 'off'):
            effort = 'medium'
            kwargs.pop('enable_thinking', None)
            kwargs['reasoning_effort'] = 'medium'
        if effort is None:
            if 'chat_template_kwargs' in data and kwargs != data.get('chat_template_kwargs'):
                data['chat_template_kwargs'] = kwargs
                return json.dumps(data).encode()
            return body
    elif not effort:
        return body
    data['reasoning_effort'] = effort
    if effort == 'none':
        kwargs.setdefault('enable_thinking', False)
    else:
        kwargs.setdefault('reasoning_effort', effort)
    data['chat_template_kwargs'] = kwargs
    return json.dumps(data).encode()


# Loop guard (11 Nov 2026). Three agent sessions (9 and 11 Nov) repeated one tool call with the same result 55-80
# times in a row. Replaying every recorded pi / Pi Pocket session: normal work never repeats a step (same calls, same
# arguments, same results) more than twice. So the 3rd identical step gets a note on its tool result, and the 6th
# is refused before any GPU swap, which ends the client's turn. Every client goes through here, so one guard covers
# pi, Pi Pocket, Paseo and Open WebUI. The note sits on the last message only: the cached prompt prefix is untouched.
LOOP_NUDGE_AT = 3
LOOP_STOP_AT = 6
LOOP_NOTE = ('\n\n[Homelab broker: you have now made exactly this tool call and got exactly this result {n} times in '
             'a row. Repeating it will not change the answer. Do not call it again: use what you already have, '
             'try a different approach, or tell Ryan what is blocking you.]')


def _tool_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(p.get('text') or '' for p in content if isinstance(p, dict))
    return ''


def _tool_args(raw):
    if isinstance(raw, str):
        try:
            return json.dumps(json.loads(raw), sort_keys=True)
        except ValueError:
            return raw
    return json.dumps(raw, sort_keys=True)


def loop_run(messages):
    """How many identical tool steps end this conversation. A step is one assistant message with tool calls plus
    the tool results after it; two steps are identical when the calls (name + arguments) and the results match.
    0 unless the last message is a tool result (a new message from Ryan ends any run)."""
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict) \
            or messages[-1].get('role') != 'tool':
        return 0
    steps, i = [], len(messages) - 1
    while i >= 0:
        results = []
        while i >= 0 and isinstance(messages[i], dict) and messages[i].get('role') == 'tool':
            results.insert(0, _tool_text(messages[i].get('content')))
            i -= 1
        m = messages[i] if i >= 0 else None
        calls = m.get('tool_calls') if isinstance(m, dict) and m.get('role') == 'assistant' else None
        if not results or not isinstance(calls, list) or not calls:
            break
        sig = tuple(sorted(((c.get('function') or {}).get('name'), _tool_args((c.get('function') or {}).get('arguments')))
                           for c in calls if isinstance(c, dict)))
        steps.append((sig, tuple(results)))
        if steps[-1] != steps[0]:
            steps.pop()
            break
        i -= 1
    return len(steps)


def loop_guard(body):
    """(body, run): the request with a note on its last tool result once a loop starts, and how long the run is.
    Never raises: a body it cannot read goes through unchanged."""
    try:
        data = json.loads(body)
        messages = data.get('messages') if isinstance(data, dict) else None
        run = loop_run(messages)
        if run < LOOP_NUDGE_AT or run >= LOOP_STOP_AT:
            return body, run
        last, note = messages[-1], LOOP_NOTE.format(n=run)
        if isinstance(last.get('content'), list):
            last['content'] = list(last['content']) + [{'type': 'text', 'text': note.strip()}]
        else:
            last['content'] = _tool_text(last.get('content')) + note
        return json.dumps(data).encode(), run
    except Exception:
        return body, 0


def llama_ids(max_age=60.0):
    """llama-server's model ids (cached); [] when it can't be asked, so a request then goes through and llama answers."""
    if time.time() - _llama_ids['at'] > max_age:
        try:
            _llama_ids['ids'] = [m['id'] for m in ai_monitor.fetch(f'http://{PC}:8080/v1/models', llama_token())
                                 .get('data', []) if isinstance(m, dict) and m.get('id')]
            _llama_ids['at'] = time.time()
        except Exception:
            return _llama_ids['ids']
    return _llama_ids['ids']


def llama_token():
    return json.loads(ai_monitor.CONFIG.read_text())['token']


def all_models():
    """OpenAI /v1/models for every engine: llama-server's own list plus the two Strata models."""
    data = []
    try:
        data = [m for m in ai_monitor.fetch(f'http://{PC}:8080/v1/models', llama_token()).get('data', [])
                if isinstance(m, dict) and m.get('id')]
        # llama-server's entries pass through whole: their `status` (loaded/unloaded) is what the approver judges read
        data = [dict(m, owned_by=m.get('owned_by') or 'llama-server') for m in data]
    except Exception as exc:
        log(f'models: llama-server list unavailable ({exc.__class__.__name__})')
    for engine, name in strata_names().items():
        if name and all(m['id'] != name for m in data):
            data.append({'id': name, 'object': 'model', 'owned_by': engine, 'created': 0})
    return {'object': 'list', 'data': data}


def remember(row):
    """One history row per generation request (Local AI page). Never lets a history problem touch the request."""
    try:
        ai_history.record(row)
    except Exception as exc:
        log(f'history not recorded: {exc.__class__.__name__}')


def refuse(method, path, headers):
    """(code, message) for a request the broker must not forward, else None. A web page on any LAN device could
    otherwise send a simple cross-site POST here and force a GPU swap (code review, 30 Oct 2026)."""
    host = headers.get('Host', '').rsplit(':', 1)[0].strip('[]').lower()
    if host not in HOSTS | PAGES:
        return 421, 'Unknown host name'
    origin = headers.get('Origin')
    if origin is not None and not (host in PAGES and origin.lower() in ('https://' + host, 'http://' + host)):
        return 403, 'Browser requests from other sites are not accepted'
    length = headers.get('Content-Length')
    if length is not None and not (length.isascii() and length.isdigit()):
        return 400, 'Bad Content-Length'
    if needs_gpu(method, path) and headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
        return 415, 'Generation requests must be JSON'
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0 responses end when the connection closes, so streamed (SSE) bodies need no re-chunking.
    protocol_version = 'HTTP/1.0'
    timeout = 120  # a client that stops sending its request is dropped instead of holding a thread

    def log_message(self, *args):
        pass

    def fail(self, code, message, retry_after=None):
        raw = json.dumps({'error': {'message': message, 'type': 'gpu_broker'}}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        if retry_after is not None:
            self.send_header('Retry-After', str(retry_after))
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def reply_json(self, payload, code=200):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def forward(self):
        engine, (host, port) = self.server.engine, self.server.upstream
        if not client_allowed(self.client_address[0]):
            return self.fail(403, 'This machine may not use the GPU broker')
        problem = refuse(self.command, self.path, self.headers)
        if problem:
            return self.fail(*problem)
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self.fail(413, 'Request too large')
        body = self.rfile.read(length) if length else None
        generate = needs_gpu(self.command, self.path)
        # Queue status and lease release (never GPU work, never queued themselves). Same host/origin rules as
        # everything else here (refuse()); release additionally needs the dashboard action header.
        qpath = self.path.split('?', 1)[0].rstrip('/')
        if self.command == 'GET' and qpath == '/queue/status':
            return self.reply_json(lease_snapshot())
        if self.command == 'POST' and qpath == '/queue/release':
            return self.queue_release(body)
        extra = {}
        if engine == 'auto':
            path = self.path.split('?', 1)[0].rstrip('/')
            if self.command == 'GET' and path in ('/v1/models', '/models'):
                return self.reply_json(all_models())
            if self.command == 'GET' and path in ('', '/health', '/v1'):
                return self.reply_json({'status': 'ok', 'service': 'gpu_broker', 'models': '/v1/models'})
            if not generate:
                return self.fail(404, 'This URL only lists models (/v1/models) and generates; use /v1/chat/completions')
            try:
                model = json.loads(body or b'{}').get('model')
            except (ValueError, AttributeError):
                return self.fail(400, 'Request body is not a JSON object')
            if not isinstance(model, str) or not model.strip():
                return self.fail(400, 'Name a model (GET /v1/models lists them)')
            engine = route(model)
            if engine == 'llama':
                known = llama_ids()
                if known and model not in known:  # refuse before any swap: an unknown name must not unload Strata
                    return self.fail(404, f'Unknown model {model!r}; GET /v1/models lists the available ones')
            host, port = self.server.upstream[0], self.server.ports[engine]
            if engine == 'llama':
                extra['Authorization'] = 'Bearer ' + llama_token()
        if generate and body:
            body, run = loop_guard(body)
            if run >= LOOP_STOP_AT:
                model = ai_history.model_of(body)
                log(f'loop guard: {model} repeated one tool step {run} times; turn refused')
                remember(dict(ts=time.time(), engine=engine, model=model, ok=0, status=409, swap='loop', wall_s=0.0))
                return self.fail(409, f'Loop guard: the same tool call returned the same result {run} times in a row, '
                                      'so this turn was stopped. Send a new message to go on.')
            if run >= LOOP_NUDGE_AT:
                log(f'loop guard: {ai_history.model_of(body)} repeated one tool step {run} times; note added')
        if generate and engine == 'llama':
            body = llama_body(body)
        # Session queue: park behind the lease holder instead of swapping the engine away. Pass-through clients
        # (PASS_THROUGH) and status reads take no part; the holder's own requests ride straight through (this
        # covers side requests too: compaction summaries and retries carry the same session key).
        lease_key = None
        waited = None
        if generate and QUEUE_ENABLED and not is_pass_through(self.client_address[0]):
            lease_key = session_key(self.headers, body, client_ip(self.client_address[0]))
            try:
                streaming = bool(json.loads(body or b'{}').get('stream'))
            except Exception:
                streaming = False
            granted, waited = self.lease_wait(lease_key, streaming)
            if not granted:
                if getattr(self, '_sse_committed', False):
                    log(f'queue: {lease_key[:24]} ({client_ip(self.client_address[0])}) queued stream went away')
                    return
                remember(dict(ts=time.time(), engine=engine, model=ai_history.model_of(body) if generate and body else None,
                            ok=0, status=503, swap='queued', queued_s=waited, wall_s=waited,
                            client=client_ip(self.client_address[0])))
                return self.fail(503, f'The GPU is held by another session; this non-streaming request waited its '
                                      f'{QUEUE_HOLD_S} s queue hold and may try again', max(10, int(QUEUE_HOLD_S)))
        # A granted but already-committed stream must stay non-silent: undici drops a stream quiet for 300 s, and
        # waking the PC plus the engine swap can exceed that. Comments keep flowing until the first real chunk goes.
        sse_hold = threading.Event() if getattr(self, '_sse_committed', False) else None
        if sse_hold:
            def sse_keep():
                while not sse_hold.wait(QUEUE_KEEPALIVE_S):
                    try:
                        self.wfile.write(b': waiting, engine starting\n\n')
                        self.wfile.flush()
                    except OSError:
                        return  # client hung up; the drain loop's own write will end the request
            threading.Thread(target=sse_keep, daemon=True).start()
        # Strata's chat page has its own Load button; it must free the GPU of llama models first, like a request.
        load = engine in STRATA and self.command == 'POST' and self.path.split('?', 1)[0].rstrip('/') == '/load'
        t0, held = time.time(), False
        row = dict(ts=t0, engine=engine, model=ai_history.model_of(body) if generate and body else None, ok=0,
                   status=None, client=client_ip(self.client_address[0]),
                   queued_s=waited if generate and lease_key else None)
        if generate or load:
            try:
                woke = ensure_awake(engine)
            except WakeTimeout as exc:
                log(f'wake: failed after {time.time() - t0:.0f} s ({exc})')
                if generate:
                    remember(dict(row, status=503, swap='wake-failed', wall_s=time.time() - t0))
                return self.fail(503, f'The gaming PC did not wake up within {WAKE_TIMEOUT} s. Try again, or wake it '
                                      'from the dashboard.')
            if woke is None:
                maybe_cancel_sleep()  # the PC is up; a sleep countdown must not cut this request's work
            try:
                row['swap'] = ensure(engine, hold=generate)
                held = generate
            except GamePaused:
                if generate:
                    remember(dict(row, status=503, swap='game', wall_s=time.time() - t0))
                return self.fail(503, 'A game is running on the gaming PC, so its GPU is paused. Try again after the game.')
            except Exception as exc:
                log(f'switch failed: {exc}')
                if generate:
                    remember(dict(row, status=503, swap='failed', wall_s=time.time() - t0))
                return self.fail(503, 'The gaming PC could not switch its GPU to this engine. Try again shortly.')
            if woke is not None:
                row['swap'] = f'wake+{row["swap"]}' if row['swap'] else 'wake'
                row['swap_s'] = time.time() - t0
            else:
                row['swap_s'] = time.time() - t0 if row['swap'] else None
        t1, tail, first = time.time(), b'', None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        if extra:  # the unified port's own credentials replace whatever key the client sent
            headers = {k: v for k, v in headers.items() if k.lower() not in ('authorization', 'x-api-key')}
            headers.update(extra)
        conn = http.client.HTTPConnection(host, port, timeout=ai_monitor.SWITCH_TIMEOUT)
        started = False
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            upstream = conn.getresponse()
            started = True
            confirm_awake()  # any upstream answer (any engine, any status) proves the PC is up for the next 15 s
            if not getattr(self, '_sse_committed', False):
                self.send_response(upstream.status, upstream.reason)
                for k, v in upstream.getheaders():
                    if k.lower() not in HOP or (k.lower() == 'content-length' and not upstream.chunked):
                        self.send_header(k, v)
                self.end_headers()
            row['status'] = upstream.status
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                if generate:
                    first = first or time.time()
                    tail = (tail + chunk)[-ai_history.TAIL:]
                if sse_hold:
                    sse_hold.set()  # real output started: stop the comments
                self.wfile.write(chunk)
                self.wfile.flush()
            row['ok'] = int(200 <= upstream.status < 300)
        except (ConnectionError, OSError) as exc:
            if not started:
                forget_awake()  # the awake stamp was the only reason not to probe: distrust it, wake on the next request
                try:
                    self.fail(502, 'The gaming PC did not answer.')
                except OSError:
                    pass
            log(f'{self.command} {self.path} ({engine}): {exc.__class__.__name__}')
        finally:
            conn.close()
            if sse_hold:
                sse_hold.set()  # no chunk ever came (or the stream ended): the comment thread must not outlive it
            if held:
                release(engine)
            if generate:
                if lease_key and row.get('ok'):
                    # done/waiting signal for the lease from the reply's own tail (finish_reason / ask tool call);
                    # lease_end ignores it unless this session is still the holder.
                    lease_end(time.time(), finish_state(tail), lease_key)
                remember(dict(row, ttfb_s=first - t1 if first else None, wall_s=time.time() - t0, **(ai_history.timings(tail) if row['ok'] else {})))

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = forward

    def queue_release(self, body):
        """POST /queue/release: drop the current lease. Needs X-Dashboard-Action: 1 plus the host/origin rules
        refuse() already enforces (same guard as the dashboard's other actions). With {"session": key} it releases
        only that holder; with an empty body it releases a holder already waiting (ask open) or done (turn ended),
        never one mid-turn: the rh-queue drop-in calls it at run.ended, when the broker has already marked the
        holder done from its reply tail."""
        if self.headers.get('X-Dashboard-Action', '') != '1':
            return self.fail(401, 'X-Dashboard-Action: 1 required')
        try:
            want = (json.loads(body) if body else {}).get('session')
        except (ValueError, AttributeError):
            return self.fail(400, 'body must be JSON ({"session": key}) or empty')
        if not QUEUE_ENABLED:
            return self.reply_json(dict(released=False, reason='queue disabled'))
        with _lease_cv:
            if not _lease['key']:
                return self.reply_json(dict(released=False, reason='no holder'))
            if want is not None and want != _lease['key']:
                return self.reply_json(dict(released=False, reason='not the holder'))
            if want is None and _lease['state'] == 'working':
                return self.fail(409, 'holder is mid-turn (working): send its session id to force, or let the '
                                      'done/idle rules release it')
            old = _lease['key']
            _lease.update(key=None, client=None, since=0.0, last_req=0.0, release_at=float('inf'), state=None)
            lease_update(time.time(), locked=True)  # hand the freed lease to the queue head at once
            _lease_cv.notify_all()
            new = _lease['key']
        log(f'queue: lease released by action: {old[:24]} -> {new[:24] if new else "free"}')
        return self.reply_json(dict(released=True, was=old, now_holder=new))

    def lease_wait(self, key, streaming):
        """Park until this session may run: (True, waited_s) or (False, None) for a non-streaming caller past
        QUEUE_HOLD_S (its queue entry stays, so a retry keeps its place in line). A queued streaming caller commits
        200 + SSE headers at once, then sends `: queued, position N` comments every QUEUE_KEEPALIVE_S until
        granted (measured: undici drops a stream silent for 300 s; 15 s comments keep pi-family clients alive).
        The holder (and its side requests: same session key) returns immediately. The waiter's own 15 s tick
        rechecks time-based lease expiry, so a clock-only release needs no push from the holder."""
        t0 = time.time()
        with _lease_cv:
            while True:
                lease_update(time.time(), locked=True)
                if _lease['key'] == key:
                    _lease['last_req'] = time.time()
                    return True, max(0.0, time.time() - t0)
                if not _lease['key']:
                    lease_grant(key, client_ip(self.client_address[0]), time.time())
                    _lease_cv.notify_all()
                    return True, max(0.0, time.time() - t0)
                if next((q for q in _queue if q['key'] == key), None) is None:
                    _queue.append({'key': key, 'client': client_ip(self.client_address[0]), 'first': time.time()})
                if streaming and not getattr(self, '_sse_committed', False):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self._sse_committed = True
                if not streaming and time.time() - t0 >= QUEUE_HOLD_S:
                    return False, time.time() - t0  # the caller records the wait; the queue entry stays
                _lease_cv.wait(QUEUE_KEEPALIVE_S)
                if streaming:
                    pos = 1 + next((i for i, q in enumerate(_queue) if q['key'] == key), 0)
                    try:
                        self.wfile.write((': queued, position %d\n\n' % pos).encode())
                        self.wfile.flush()
                    except OSError:
                        return False, None  # client hung up on its queued stream


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, upstream):
        self.engine, self.upstream = engine, upstream
        self.ports = {'llama': 8080, **STRATA}  # the unified port's upstream per engine (tests point these at a fake PC)
        super().__init__(address, Handler)


def main():
    ai_history.initialize()
    servers = [Server(('0.0.0.0', port), engine, (PC, port)) for port, engine in PORTS.items()]
    servers.append(Server(('0.0.0.0', UNIFIED_PORT), 'auto', (PC, 0)))
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    log('gpu broker on ' + ', '.join(f':{p} -> {PC}:{p} ({e})' for p, e in PORTS.items())
        + f', :{UNIFIED_PORT} (any model, routed by name)')
    servers[0].serve_forever()


if __name__ == '__main__':
    sys.exit(main())
