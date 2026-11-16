"""Dashboard-only proxy. Broker credentials never enter browser responses."""
import json
import re
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

CONFIG=Path('/etc/homelab-dashboard/qwen.json')
HOSTS={'192.168.0.2','192.168.0.2:80','127.0.0.1','localhost','home.lan','homepage.lan'}
# A question plus this browser tab's chat history must stay bounded on the way to the broker (spec item 11): the
# page caps the history it sends at 10,000 characters, this proxy keeps a hard 32 KiB cap on the whole body.
BODY_LIMIT=32768
HISTORY_MAX=20
HISTORY_Q_MAX=3000
HISTORY_A_MAX=6000


def same_origin(handler,header):
    # A custom header requires CORS preflight; no cross-origin CORS is allowed.
    host=handler.headers.get('Host','');origin=handler.headers.get('Origin')
    return handler.headers.get(header)=='1' and host in HOSTS and (origin is None or origin in ('http://'+host,'https://'+host))


def handle(handler,post=False):
    if urllib.parse.urlparse(handler.path).path!='/api/qwen':return False
    def reply(code,data):
        raw=json.dumps(data).encode();handler.send_response(code)
        handler.send_header('Content-Type','application/json');handler.send_header('Cache-Control','no-store')
        handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
    body=None
    if post:
        if not same_origin(handler,'X-Qwen-Request'):
            reply(403,dict(status='failed',error='Use the homepage to submit a question.'));return True
        try:
            length=int(handler.headers.get('Content-Length','0'))
            if not 0<length<=BODY_LIMIT:raise ValueError()
            handler.connection.settimeout(5)
            data=json.loads(handler.rfile.read(length))
            prompt=data.get('prompt') if isinstance(data,dict) else None
            if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>3000:raise ValueError()
            target=data.get('target','a310')
            if not isinstance(target,str) or not re.match(r'^(a310|a310-12b|auto|strata|pc|pc:[a-z0-9][a-z0-9.\-]{0,63})$',target):raise ValueError()
            homelab=data.get('homelab',False)
            if not isinstance(homelab,bool):raise ValueError()
            # Chat history is passed through unchanged (the broker scrubs it); only its shape is checked here, and
            # only question and answer text travels, never any model reasoning.
            history=data.get('history',[])
            if not isinstance(history,list) or len(history)>HISTORY_MAX:raise ValueError()
            turns=[]
            for turn in history:
                if not isinstance(turn,dict):raise ValueError()
                q,a=turn.get('q'),turn.get('a')
                if not isinstance(q,str) or not isinstance(a,str) or len(q)>HISTORY_Q_MAX or len(a)>HISTORY_A_MAX:raise ValueError()
                turns.append({'q':q,'a':a})
            body=json.dumps({'prompt':prompt,'target':target,'homelab':homelab,'history':turns}).encode()
            if len(body)>BODY_LIMIT:raise ValueError()
        except (ValueError,OSError):
            reply(400,dict(status='failed',error='Enter a question of 1–3,000 characters.'));return True
    try:
        config=json.loads(CONFIG.read_text())
        request=urllib.request.Request('http://192.168.0.4:11438/job',data=body,
            headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(request,timeout=5)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            raw=response.read(131073);code=response.status
        if len(raw)>131072:raise ValueError()
        reply(code,json.loads(raw))
    except Exception:
        reply(503,dict(status='failed',model='Nemotron Bolt E4B · A310',error='Qwen is unavailable. Try again shortly.'))
    return True
