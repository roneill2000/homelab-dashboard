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
    """game-watch.ps1 on the PC stops llama-server (:8080) while a game runs; a refused connection there means a game."""
    try:
        socket.create_connection((PC, 8080), timeout=3).close()
        return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def llama_busy():
    try:
        config = json.loads(ai_monitor.CONFIG.read_text())
        return any(m['state'] in ('loaded', 'loading')
                   for m in ai_monitor.models(ai_monitor.fetch(config['url'] + '/models', config['token'])))
    except Exception:
        return False


def ensure(engine):
    """Give the GPU to `engine` ('llama' or 'strata'); serialised so concurrent requests cannot interleave a switch."""
    with _switch_lock:
        if engine == 'llama':
            if strata_loaded():
                log('llama request: unloading Strata')
                ai_monitor.strata_post('/unload')
                if not ai_monitor.wait_strata(False, timeout=120):
                    raise RuntimeError('Strata did not unload')
        else:
            if llama_busy():
                log('Strata request: unloading llama-server models')
                ai_monitor.unload_llama()
            if not strata_loaded():
                if game_running():
                    raise GamePaused()
                log('Strata request: loading Strata')
                ai_monitor.strata_post('/load')
                if not ai_monitor.wait_strata(True):
                    raise RuntimeError('Strata did not load')


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0 responses end when the connection closes, so streamed (SSE) bodies need no re-chunking.
    protocol_version = 'HTTP/1.0'

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
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self.fail(413, 'Request too large')
        body = self.rfile.read(length) if length else None
        if needs_gpu(self.command, self.path):
            try:
                ensure(engine)
            except GamePaused:
                return self.fail(503, 'A game is running on the gaming PC, so its GPU is paused. Try again after the game.')
            except Exception as exc:
                log(f'switch failed: {exc}')
                return self.fail(503, 'The gaming PC could not switch its GPU to this engine. Try again shortly.')
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
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (ConnectionError, OSError) as exc:
            if not started:
                try:
                    self.fail(502, 'The gaming PC did not answer.')
                except OSError:
                    pass
            log(f'{self.command} {self.path} ({engine}): {exc.__class__.__name__}')
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = forward


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, upstream):
        self.engine, self.upstream = engine, upstream
        super().__init__(address, Handler)


def main():
    servers = [Server(('0.0.0.0', port), engine, (PC, port)) for port, engine in PORTS.items()]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    log('gpu broker on ' + ', '.join(f':{p} -> {PC}:{p} ({e})' for p, e in PORTS.items()))
    servers[0].serve_forever()


if __name__ == '__main__':
    sys.exit(main())
