"""Dashboard-only bridge to Proxmox `homelab-update` over a restricted SSH key."""
import json
import subprocess
import threading
import time
import urllib.parse

import qwen_proxy

KEY='/etc/homelab-dashboard/update_ed25519'
KNOWN='/etc/homelab-dashboard/update_known_hosts'
TARGET='root@192.168.0.3'
CACHE={'at':0,'body':None};LOCK=threading.Lock()


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


def handle(handler,post=False):
    if urllib.parse.urlparse(handler.path).path!='/api/updates':return False
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
