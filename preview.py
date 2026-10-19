"""Local preview; all control actions are disabled."""
import os
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import urllib.parse
import urllib.request

BASE = Path(__file__).parent
(BASE / '.preview').mkdir(exist_ok=True)
os.environ['HOMELAB_METRICS_DB'] = str(BASE / '.preview/metrics.sqlite3')
import storage_monitor
storage_monitor.start()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == '/api/storage':
                window = urllib.parse.parse_qs(path.query).get('window', ['3600'])[0]
                body = json.dumps(storage_monitor.snapshot(int(window))).encode()
                content_type = 'application/json'
            elif path.path in ('/api/telemetry', '/static/react.js', '/static/react-dom.js', '/icon.svg', '/manifest.json'):
                with urllib.request.urlopen('http://192.168.0.2' + path.path, timeout=10) as response:
                    body = response.read()
                    content_type = response.headers.get('Content-Type')
            elif path.path == '/':
                body = (BASE / 'index.html').read_bytes()
                content_type = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(502)
    def do_POST(self):
        self.send_error(405, 'Actions disabled in preview')
    def log_message(self, *_):
        pass

ThreadingHTTPServer(('127.0.0.1', 8799), Handler).serve_forever()
