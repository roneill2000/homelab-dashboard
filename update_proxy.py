"""Dashboard-only bridge to Proxmox `homelab-update` over a restricted SSH key."""
import json
import subprocess
import threading
import time
import socket
import urllib.parse

import qwen_proxy

KEY='/etc/homelab-dashboard/update_ed25519'
KNOWN='/etc/homelab-dashboard/update_known_hosts'
TARGET='root@192.168.0.3'
CACHE={'at':0,'body':None};LOCK=threading.Lock()
FLEET={'at':0,'body':None};FLEET_LOCK=threading.Lock()
EVENTS={'at':0,'body':None};EVENTS_LOCK=threading.Lock()


def remote(command,timeout=15):
    args=['ssh','-i',KEY,'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+KNOWN,
          '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',TARGET,command]
    r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError('ssh exited %d'%r.returncode)
    return json.loads(r.stdout)


def status():
    with LOCK:
        if CACHE['body'] is not None and time.time()-CACHE['at']<2:return CACHE['body']
        body=remote('status');CACHE.update(at=time.time(),body=body);return body


def _refresh_fleet():
    try:
        body=remote('health',timeout=90)
        with FLEET_LOCK:FLEET.update(at=time.time(),body=body)
    finally:
        with FLEET_LOCK:FLEET['refreshing']=False


def _fleet_loop():
    # Keep host and backup status fresh whether or not anyone is looking, so opening the app or site shows a current
    # answer at once instead of an old copy or the ~8 s probe. Every 50 s (under the 60 s cache age), 12 s while a cloud
    # backup uploads so its progress bar moves.
    while True:
        with FLEET_LOCK:
            busy=FLEET.get('refreshing')
            if not busy:FLEET['refreshing']=True
        if not busy:
            try:_refresh_fleet()
            except Exception:pass
        try:events(wait=False)  # keeps the timeline warm too (refreshes itself in the background every 5 min)
        except Exception:pass
        with FLEET_LOCK:body=FLEET['body']
        running=isinstance(body,dict) and (body.get('cloud_backup') or {}).get('running')
        time.sleep(12 if running else 50)


_LOOP={'started':False};_LOOP_LOCK=threading.Lock()


def start_fleet_loop():
    import os
    if not os.path.exists(KEY):return False  # only on the dashboard host (tests and previews never probe Proxmox)
    with _LOOP_LOCK:
        if _LOOP['started']:return False
        _LOOP['started']=True
    threading.Thread(target=_fleet_loop,name='fleet-refresh',daemon=True).start()
    return True


def fleet():
    # Read-only container health and backup state from Proxmox. A probe takes ~8 s, so once there is a result it is
    # answered at once and refreshed in the background when over a minute old; only the very first call waits.
    # checked_at lets the page ask again shortly after it was handed an old copy.
    with FLEET_LOCK:
        body,at=FLEET['body'],FLEET['at']
        # While a cloud backup runs, refresh every 15 s so its progress bar moves; otherwise once a minute.
        ttl=15 if isinstance(body,dict) and (body.get('cloud_backup') or {}).get('running') else 60
        stale=body is None or time.time()-at>=ttl
        start=stale and not FLEET.get('refreshing')
        if start:FLEET['refreshing']=True
    if body is None:
        if start:_refresh_fleet()
        else:
            for _ in range(900):
                time.sleep(0.1)
                with FLEET_LOCK:
                    if FLEET['body'] is not None:break
        with FLEET_LOCK:body,at=FLEET['body'],FLEET['at']
        if body is None:raise RuntimeError('fleet probe failed')
    elif start:threading.Thread(target=_refresh_fleet,daemon=True).start()
    return dict(body,checked_at=int(at)) if isinstance(body,dict) else body


def _refresh_events():
    try:
        body=remote('events',timeout=90)
        with EVENTS_LOCK:EVENTS.update(at=time.time(),body=body)
    finally:
        with EVENTS_LOCK:EVENTS['refreshing']=False


def events(wait=True):
    # "What changed" timeline from Proxmox. Collecting takes ~15 s, so the last answer is served at once and refreshed in
    # the background when over five minutes old (the fleet loop also keeps it warm); only the very first call waits.
    with EVENTS_LOCK:
        body,at=EVENTS['body'],EVENTS['at']
        start=(body is None or time.time()-at>=300) and not EVENTS.get('refreshing')
        if start:EVENTS['refreshing']=True
    if start and (body is None and wait):
        _refresh_events()
        with EVENTS_LOCK:body=EVENTS['body']
    elif start:threading.Thread(target=_refresh_events,daemon=True).start()
    if body is None and wait:raise RuntimeError('events unavailable')
    return body


PC_MAC='34:5a:60:01:a1:9a'  # gaming PC, Realtek 5GbE (Wake on Magic Packet + Shutdown WoL enabled)


def sleep_pc(force=False):
    """Ask the gaming PC to sleep via its forced-command key (60 s warning on screen; refuses if the GPU is busy unless forced)."""
    args=['ssh','-i','/etc/homelab-dashboard/pc_sleep_ed25519','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
          '-o','UserKnownHostsFile=/etc/homelab-dashboard/pc_known_hosts','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=6',
          'roneill@192.168.0.12','force' if force else 'sleep']
    r=subprocess.run(args,capture_output=True,text=True,timeout=25)
    return json.loads(r.stdout.strip().splitlines()[-1])


def wake_pc():
    """Send a Wake-on-LAN magic packet for the gaming PC to the LAN broadcast addresses."""
    payload=b'\xff'*6+bytes.fromhex(PC_MAC.replace(':',''))*16
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
        for target in ('192.168.0.255','255.255.255.255'):
            for port in (9,7):sock.sendto(payload,(target,port))


def handle(handler,post=False):
    start_fleet_loop()
    path=urllib.parse.urlparse(handler.path).path
    import download_proxy
    if download_proxy.handle(handler,post):return True
    if path=='/api/action/sleep-pc' and post:
        def answer(code,data):
            raw=json.dumps(data).encode();handler.send_response(code)
            handler.send_header('Content-Type','application/json');handler.send_header('Content-Length',str(len(raw)))
            handler.end_headers();handler.wfile.write(raw)
        if not qwen_proxy.same_origin(handler,'X-Dashboard-Action'):
            answer(403,dict(ok=False,error='Use the homepage to run this action.'));return True
        try:
            length=int(handler.headers.get('Content-Length','0'))
            body=json.loads(handler.rfile.read(length)) if 0<length<=256 else {}
            result=sleep_pc(force=bool(isinstance(body,dict) and body.get('force') is True))
            answer(200 if result.get('ok') else 409,result)
        except Exception:
            answer(503,dict(ok=False,error='Could not reach the gaming PC.'))
        return True
    if path=='/api/action/wake-pc' and post:
        def answer(code,data):
            raw=json.dumps(data).encode();handler.send_response(code)
            handler.send_header('Content-Type','application/json');handler.send_header('Content-Length',str(len(raw)))
            handler.end_headers();handler.wfile.write(raw)
        if not qwen_proxy.same_origin(handler,'X-Dashboard-Action'):
            answer(403,dict(ok=False,error='Use the homepage to run this action.'));return True
        try:wake_pc();answer(200,dict(ok=True))
        except OSError:answer(503,dict(ok=False,error='Could not send the wake signal.'))
        return True
    if path=='/api/events' and not post:
        try:raw=json.dumps(events()).encode();code=200
        except Exception:raw=json.dumps(dict(error='Timeline unavailable.')).encode();code=503
        handler.send_response(code);handler.send_header('Content-Type','application/json')
        handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(raw)))
        handler.end_headers();handler.wfile.write(raw);return True
    if path=='/api/fleet' and not post:
        raw=b''
        try:raw=json.dumps(fleet()).encode();code=200
        except Exception:raw=json.dumps(dict(error='Fleet health unavailable.')).encode();code=503
        handler.send_response(code);handler.send_header('Content-Type','application/json')
        handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(raw)))
        handler.end_headers();handler.wfile.write(raw);return True
    if path!='/api/updates':return False
    def reply(code,data):
        raw=json.dumps(data).encode();handler.send_response(code)
        handler.send_header('Content-Type','application/json');handler.send_header('Cache-Control','no-store')
        handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
    if not post:
        try:reply(200,status())
        except Exception:reply(503,dict(error='Update status unavailable.'))
        return True
    if not qwen_proxy.same_origin(handler,'X-Dashboard-Action'):
        reply(403,dict(ok=False,error='Use the homepage to run this action.'));return True
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if not 0<length<=256:raise ValueError()
        handler.connection.settimeout(5)
        action=json.loads(handler.rfile.read(length)).get('action')
        if action not in ('check','start'):raise ValueError()
    except (ValueError,OSError,AttributeError):
        reply(400,dict(ok=False,error='Invalid request.'));return True
    try:
        result=remote(action)
        with LOCK:CACHE['at']=0
        reply(200 if result.get('ok') else 409,result)
    except Exception:
        reply(503,dict(ok=False,error='Could not reach Proxmox.'))
    return True
