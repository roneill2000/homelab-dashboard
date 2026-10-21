#!/usr/bin/env python3
"""Read-only network byte counters for Homepage. GET / returns JSON for one interface; no input is used."""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IFACE = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 61209


def counters():
    for line in open('/proc/net/dev'):
        name, _, rest = line.partition(':')
        if name.strip() == IFACE:
            f = rest.split()
            speed = None
            try:
                speed = int(open(f'/sys/class/net/{IFACE}/speed').read())
            except (OSError, ValueError):
                pass
            return dict(iface=IFACE, rx_bytes=int(f[0]), tx_bytes=int(f[8]), speed_mbps=speed if speed and speed > 0 else None, ts=time.time())
    return dict(iface=IFACE, error='interface not found', ts=time.time())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(counters()).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
