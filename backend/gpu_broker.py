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
import http.server
import json
import socket
import socketserver
import sys
import threading
import time

import ai_history
import ai_monitor

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

    def fail(self, code, message):
        raw = json.dumps({'error': {'message': message, 'type': 'gpu_broker'}}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
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
        problem = refuse(self.command, self.path, self.headers)
        if problem:
            return self.fail(*problem)
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self.fail(413, 'Request too large')
        body = self.rfile.read(length) if length else None
        generate = needs_gpu(self.command, self.path)
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
        # Strata's chat page has its own Load button; it must free the GPU of llama models first, like a request.
        load = engine in STRATA and self.command == 'POST' and self.path.split('?', 1)[0].rstrip('/') == '/load'
        t0, held = time.time(), False
        row = dict(ts=t0, engine=engine, model=ai_history.model_of(body) if generate and body else None, ok=0, status=None)
        if generate or load:
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
                self.wfile.write(chunk)
                self.wfile.flush()
            row['ok'] = int(200 <= upstream.status < 300)
        except (ConnectionError, OSError) as exc:
            if not started:
                try:
                    self.fail(502, 'The gaming PC did not answer.')
                except OSError:
                    pass
            log(f'{self.command} {self.path} ({engine}): {exc.__class__.__name__}')
        finally:
            conn.close()
            if held:
                release(engine)
            if generate:
                remember(dict(row, ttfb_s=first - t1 if first else None, wall_s=time.time() - t0, **(ai_history.timings(tail) if row['ok'] else {})))

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = forward


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
