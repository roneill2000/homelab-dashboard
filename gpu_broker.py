"""GPU broker: one engine on the gaming PC's 16 GB card at a time, switched automatically.

Listens on :8080 (llama-server router) and :8088 (Strata) on this host and forwards to the same ports on the PC.
Before forwarding a generation request it makes sure the right engine holds the GPU: a llama-server request unloads
Strata; a Strata request unloads llama-server's models and loads Strata. Status reads (/models, /health, /slots, ...)
pass straight through and never switch. Responses stream (SSE) as they arrive. Clients keep their own auth headers.
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
PORTS = {8080: 'llama', 8088: 'strata'}
# Requests that generate (and so need their engine on the GPU). Anything else is forwarded as-is.
GENERATE = ('/v1/chat/completions', '/v1/completions', '/v1/messages', '/v1/embeddings', '/v1/responses',
            '/completion', '/completions', '/chat/completions', '/embedding', '/embeddings', '/infill')
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
       'upgrade', 'host', 'content-length'}
MAX_BODY = 64 * 1024 * 1024
_switch_lock = threading.Lock()
# Browsers attach Origin to cross-site requests; the broker's clients (OpenCode, Open WebUI, the Qwen broker) are
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


def strata_loaded():
    try:
        return ai_monitor.strata().get('loaded') is True
    except Exception:
        return False


def game_running():
    return ai_monitor.game_running()


def llama_busy():
    try:
        config = json.loads(ai_monitor.CONFIG.read_text())
        return any(m['state'] in ('loaded', 'loading')
                   for m in ai_monitor.models(ai_monitor.fetch(config['url'] + '/models', config['token'])))
    except Exception:
        return False


def ensure(engine):
    """Give the GPU to `engine` ('llama' or 'strata'); serialised so concurrent requests cannot interleave a switch.
    Returns what it had to do ('unload-strata', 'load-strata', 'swap-to-strata') or None."""
    did = []
    with _switch_lock, ai_monitor.gpu_switch():
        if engine == 'llama':
            if strata_loaded():
                log('llama request: unloading Strata')
                did.append('unload-strata')
                ai_monitor.strata_post('/unload')
                if not ai_monitor.wait_strata(False, timeout=120):
                    raise RuntimeError('Strata did not unload')
        else:
            if llama_busy():
                log('Strata request: unloading llama-server models')
                did.append('unload-llama')
                ai_monitor.unload_llama()
            if not strata_loaded():
                if game_running():
                    raise GamePaused()
                log('Strata request: loading Strata')
                did.append('load-strata')
                ai_monitor.strata_post('/load')
                if not ai_monitor.wait_strata(True):
                    raise RuntimeError('Strata did not load')
    return '+'.join(did) or None


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
        # Strata's chat page has its own Load button; it must free the GPU of llama models first, like a request.
        load = engine == 'strata' and self.command == 'POST' and self.path.split('?', 1)[0].rstrip('/') == '/load'
        t0 = time.time()
        row = dict(ts=t0, engine=engine, model=ai_history.model_of(body) if generate and body else None, ok=0, status=None)
        if generate or load:
            try:
                row['swap'] = ensure(engine)
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
            if generate:
                remember(dict(row, ttfb_s=first - t1 if first else None, wall_s=time.time() - t0, **(ai_history.timings(tail) if row['ok'] else {})))

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = forward


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, upstream):
        self.engine, self.upstream = engine, upstream
        super().__init__(address, Handler)


def main():
    ai_history.initialize()
    servers = [Server(('0.0.0.0', port), engine, (PC, port)) for port, engine in PORTS.items()]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    log('gpu broker on ' + ', '.join(f':{p} -> {PC}:{p} ({e})' for p, e in PORTS.items()))
    servers[0].serve_forever()


if __name__ == '__main__':
    sys.exit(main())
