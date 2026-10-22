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


def fleet():
    # Read-only container health from Proxmox; a probe takes ~8 s, so cache for a minute.
    with FLEET_LOCK:
        if FLEET['body'] is not None and time.time()-FLEET['at']<60:return FLEET['body']
        body=remote('health',timeout=90);FLEET.update(at=time.time(),body=body);return body


def events():
    # "What changed" timeline from Proxmox; collecting takes ~15 s, so cache for five minutes.
    with EVENTS_LOCK:
        if EVENTS['body'] is not None and time.time()-EVENTS['at']<300:return EVENTS['body']
        body=remote('events',timeout=90);EVENTS.update(at=time.time(),body=body);return body


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
    path=urllib.parse.urlparse(handler.path).path
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
