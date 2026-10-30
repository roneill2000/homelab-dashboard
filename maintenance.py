"""Alert snooze (maintenance mode). The dashboard keeps one end time; the alert senders Ryan's homelab controls
(homelab-alerts and the health watchdog on Proxmox, the Unraid ntfy agent) read GET /api/maintenance and hold back
new alerts until it passes. Fail open: a sender that cannot read it alerts as usual. Nothing here sends anything."""
import json
from pathlib import Path
import time
import urllib.parse

import qwen_proxy

STATE = Path('/var/lib/homelab-dashboard/maintenance.json')
CHOICES = (30, 120, 240)  # minutes; 0 ends the snooze


def status(now=None, path=None):
    now = time.time() if now is None else now
    try:
        d = json.loads((path or STATE).read_text())
    except (OSError, ValueError):
        d = {}
    until = d.get('until') if isinstance(d, dict) else None
    active = isinstance(until, (int, float)) and not isinstance(until, bool) and until > now
    return dict(active=active, until=until if active else None, started_at=d.get('started_at') if active else None, now=now)


def set_minutes(minutes, now=None, path=None):
    now = time.time() if now is None else now
    path = path or STATE
    if minutes not in CHOICES and minutes != 0:
        raise ValueError('Choose 30, 120 or 240 minutes, or 0 to resume alerts.')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({} if minutes == 0 else dict(until=now + minutes * 60, started_at=now)))
    tmp.replace(path)
    return status(now, path)


def answer(handler, code, data):
    raw = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def handle(handler, post=False):
    path = urllib.parse.urlparse(handler.path).path
    if path == '/api/maintenance' and not post:
        answer(handler, 200, status()); return True
    if path == '/api/action/maintenance' and post:
        if not qwen_proxy.same_origin(handler, 'X-Dashboard-Action'):
            answer(handler, 403, dict(ok=False, error='Use the homepage to run this action.')); return True
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            body = json.loads(handler.rfile.read(length)) if 0 < length <= 256 else {}
            minutes = body.get('minutes') if isinstance(body, dict) else None
            if not isinstance(minutes, int) or isinstance(minutes, bool):
                raise ValueError('Choose 30, 120 or 240 minutes, or 0 to resume alerts.')
            answer(handler, 200, dict(ok=True, **set_minutes(minutes)))
        except ValueError as exc:
            answer(handler, 400, dict(ok=False, error=str(exc)))
        except OSError:
            answer(handler, 503, dict(ok=False, error='Could not save the snooze on the dashboard.'))
        return True
    return False
