#!/usr/bin/env python3
"""Uptime Kuma -> ntfy relay on the Orange Pi (30 Oct 2026).

Kuma's own ntfy integration can't set a tap target or replace a notification, so Kuma sends its webhook here instead
and this relay posts each event to ntfy once, like the other senders (alerts/SENDERS.md):

- Click: homelab://view/<page> (the Android app opens on that page),
- X-Sequence-ID per monitor (the recovery replaces the down notification),
- Ask AI and Snooze 2 h buttons on down alerts.

It runs on the Orange Pi, next to Kuma, so "Proxmox is down" still gets out when Proxmox (and the dashboard) are down.
Snooze: the dashboard's snooze end time is cached here, so it holds through a Proxmox reboot; a down that arrives while
snoozed is held and sent when the snooze ends if no recovery came first (a held down's recovery is dropped too).
Listens only on the Docker bridge (Kuma's container reaches the host at 172.17.0.1). The topic is read from
/etc/kuma-ntfy-relay/topic (0600) and never logged.
"""
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOPIC_FILE = Path('/etc/kuma-ntfy-relay/topic')
LISTEN = ('172.17.0.1', 8998)
MAINTENANCE = 'http://192.168.0.2/api/maintenance'
WHY = 'http://192.168.0.3:8997/why'
SNOOZE = ('http, Snooze 2 h, http://192.168.0.2/api/action/maintenance, method=POST, headers.X-Dashboard-Action=1, '
          'headers.Content-Type=application/json, body=\'{"minutes":120}\', clear=true')
# Dashboard page per monitor name (first match), else Hosts.
PAGES = ((r'share|smb', 'storage'), (r'duckdns|dns', 'network'))

lock = threading.Lock()
snooze_until = 0.0  # last known end of the dashboard snooze (kept when the dashboard is unreachable)
held = {}           # sequence id -> ntfy request held during a snooze


def page_for(name):
    return next((page for pattern, page in PAGES if re.search(pattern, name or '', re.I)), 'hosts')


def header_text(text):
    """ntfy reads raw header bytes as UTF-8; anything outside ASCII goes as an RFC 2047 encoded word."""
    try:
        text.encode('ascii')
        return text
    except UnicodeEncodeError:
        import base64
        return '=?UTF-8?B?' + base64.b64encode(text.encode()).decode() + '?='


def build(payload):
    """(sequence id, down?, headers, body) for a Kuma webhook payload, or None for events that aren't up/down."""
    beat, mon = payload.get('heartbeat') or {}, payload.get('monitor') or {}
    status = beat.get('status')
    if status not in (0, 1) or not mon.get('name'):
        return None
    name, down = str(mon['name'])[:100], status == 0
    msg = str(beat.get('msg') or payload.get('msg') or '')[:1500]
    sid = 'kuma-' + re.sub(r'[^A-Za-z0-9_-]', '-', str(mon.get('id', name)))[:40]
    headers = {'Title': header_text(('' if down else 'RECOVERED: ') + name + (' is down' if down else '') + ' [Uptime Kuma]'),
               'Priority': '5' if down else '3', 'Tags': 'red_circle' if down else 'green_circle',
               'Click': 'homelab://view/' + page_for(name), 'X-Sequence-ID': sid}
    if down:
        why = WHY + '?' + urllib.parse.urlencode({'title': f'{name} is down (Uptime Kuma)', 'detail': msg[:600]})
        headers['Actions'] = f'http, Ask AI, {why}, method=POST, clear=true; ' + SNOOZE
    body = msg or (f'{name} is not responding.' if down else f'{name} is back up.')
    return sid, down, headers, body


def publish(headers, body, opener=urllib.request.urlopen):
    req = urllib.request.Request('https://ntfy.sh/' + TOPIC_FILE.read_text().strip(), data=body.encode(),
                                 method='POST', headers=headers)
    for attempt in range(3):
        try:
            opener(req, timeout=15).read()
            return True
        except Exception:
            time.sleep(2 * (attempt + 1))
    return False


def refresh_snooze(fetch=urllib.request.urlopen):
    global snooze_until
    try:
        d = json.loads(fetch(MAINTENANCE, timeout=3).read())
        with lock:
            snooze_until = d['until'] if d.get('active') is True and isinstance(d.get('until'), (int, float)) else 0.0
    except Exception:
        pass  # dashboard unreachable (e.g. Proxmox rebooting): keep the last known snooze


def handle(payload, now=None, send=publish):
    """Deliver or hold one Kuma event. Returns 'sent', 'held', 'dropped' (recovery of a held down) or 'ignored'."""
    now = time.time() if now is None else now
    built = build(payload)
    if not built:
        return 'ignored'
    sid, down, headers, body = built
    with lock:
        snoozed = now < snooze_until
        if down and snoozed:
            held[sid] = (headers, body)
            return 'held'
        if not down and held.pop(sid, None):
            return 'dropped'  # it was never shown, so its recovery isn't either
    return 'sent' if send(headers, body) else 'failed'


def flush_held(now=None, send=publish):
    """Send downs held by a snooze that has ended (no recovery arrived meanwhile)."""
    now = time.time() if now is None else now
    with lock:
        if now < snooze_until or not held:
            return 0
        due, _ = list(held.values()), held.clear()
    for headers, body in due:
        send(headers, body)
    return len(due)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= 65536:
                raise ValueError()
            result = handle(json.loads(self.rfile.read(length)))
            code = 200
        except Exception:
            result, code = 'bad request', 400
        raw = json.dumps({'result': result}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        pass


def loop():
    while True:
        refresh_snooze()
        flush_held()
        time.sleep(60)


def main():
    refresh_snooze()
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()


if __name__ == '__main__':
    sys.exit(main())
