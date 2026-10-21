"""Single-user LAN auto-login for pinned Memos 0.31.0.

The LAN endpoint intentionally grants the owner's access without a login prompt.
The original Memos service remains authenticated on loopback port 5231.
"""
import datetime
import http.client
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request
from urllib.parse import urlsplit

CONFIG = json.loads(Path('/etc/memos-auto-login.json').read_text())
UPSTREAM = 'http://127.0.0.1:5231'
LOCK = threading.Lock()
JAR = http.cookiejar.CookieJar()
CLIENT = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))
SESSION = {}

def auth(path, body):
    req = urllib.request.Request(UPSTREAM+'/api/v1/auth/'+path,
        data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    with CLIENT.open(req,timeout=15) as response:
        return json.load(response)

def token():
    with LOCK:
        if SESSION.get('deadline',0) > time.time()+120:
            return SESSION.copy()
        try:
            result=auth('refresh',{})
            expires=result['expiresAt']
        except Exception:
            result=auth('signin',{'passwordCredentials':CONFIG})
            expires=result['accessTokenExpiresAt']
        deadline=datetime.datetime.fromisoformat(expires.replace('Z','+00:00')).timestamp()
        SESSION.update(accessToken=result['accessToken'], expiresAt=expires, deadline=deadline)
        return SESSION.copy()

def varint(n):
    out=bytearray()
    while n>127:
        out.append((n&127)|128);n>>=7
    out.append(n)
    return bytes(out)

def blob(field, data):
    return varint((field<<3)|2)+varint(len(data))+data

def refresh_proto(session):
    # AuthService.RefreshTokenResponse: string access_token=1, Timestamp expires_at=2.
    timestamp=b'\x08'+varint(int(session['deadline']))
    return blob(1,session['accessToken'].encode())+blob(2,timestamp)

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self, fmt, *args):
        pass
    def reply(self,status,body=b'',mime='application/json'):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        if self.command!='HEAD': self.wfile.write(body)
    def handle_request(self):
        self.response_started=False
        # Keep browser access same-origin; this is deliberately an open LAN app.
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.reply(403,b'{"error":"Cross-origin access denied"}')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' and self.command not in ('GET','HEAD'):
            return self.reply(403,b'{"error":"Cross-site mutation denied"}')
        if not self.path.startswith('/') or self.path.startswith('//'):
            return self.reply(400,b'{}')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>64*1024*1024: return self.reply(413,b'{}')
            if self.headers.get('Transfer-Encoding'): return self.reply(400,b'{}')
            body=self.rfile.read(length) if length else None
            session=token()
            path=urlsplit(self.path).path
            if path in ('/memos.api.v1.AuthService/RefreshToken','/api/v1/auth/refresh'):
                if path.startswith('/memos.'):
                    return self.reply(200,refresh_proto(session),'application/proto')
                return self.reply(200,json.dumps({k:session[k] for k in ('accessToken','expiresAt')}).encode())
            if path in ('/memos.api.v1.AuthService/SignOut','/api/v1/auth/signout'):
                return self.reply(200,b'' if path.startswith('/memos.') else b'{}','application/proto' if path.startswith('/memos.') else 'application/json')
            if self.command=='GET' and path in ('/auth','/auth/signin'):
                self.send_response(302);self.send_header('Location','/');self.send_header('Content-Length','0');self.end_headers();return
            headers={k:v for k,v in self.headers.items() if k.lower() not in ('authorization','cookie','host','connection','transfer-encoding','accept-encoding','origin')}
            headers.update(Host='192.168.0.18:5230',Authorization='Bearer '+session['accessToken'])
            conn=http.client.HTTPConnection('127.0.0.1',5231,timeout=60)
            try:
                conn.request(self.command,self.path,body=body,headers=headers)
                response=conn.getresponse()
                self.send_response(response.status)
                for k,v in response.getheaders():
                    if k.lower() not in ('connection','transfer-encoding','set-cookie','content-length'):
                        self.send_header(k,v)
                # Close-delimited streaming supports both attachments and streamed responses.
                self.send_header('Connection','close');self.end_headers();self.close_connection=True
                self.response_started=True
                if self.command!='HEAD':
                    while chunk:=response.read(65536): self.wfile.write(chunk)
            finally: conn.close()
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            self.close_connection=True
            # Once headers are sent, close the interrupted response. A second
            # HTTP response here would corrupt the first response's body.
            if not self.response_started:
                self.reply(502,b'{"error":"Memos temporarily unavailable"}')
    do_GET=handle_request
    do_HEAD=handle_request
    do_POST=handle_request
    do_PATCH=handle_request
    do_PUT=handle_request
    do_DELETE=handle_request
    do_OPTIONS=handle_request

if __name__=='__main__':
    ThreadingHTTPServer(('0.0.0.0',5230),Handler).serve_forever()
