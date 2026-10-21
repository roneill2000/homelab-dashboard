"""Run inside LXC113: exercise the user-requested no-login endpoint."""
import json, subprocess, time, urllib.request, urllib.error
BASE='http://127.0.0.1:5230/api/v1'
def call(path,data=None,method=None,headers=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json',**(headers or {})})
 with urllib.request.urlopen(req,timeout=15) as r:
  b=r.read();return json.loads(b) if b else {}
assert call('/auth/me')['user']['username']=='roneill'
# Clean up only the exact note left by the initial API-mask test.
for note in call('/memos').get('memos',[]):
 if note['content']=='Temporary no-login persistence verification': call('/'+note['name'],method='DELETE')
note=call('/memos',{'content':'Temporary no-login persistence verification','visibility':'PRIVATE'})
try:
 subprocess.run(['systemctl','restart','memos','memos-lan'],check=True)
 time.sleep(2)
 assert call('/'+note['name'])['content']==note['content']
 call('/'+note['name']+'?updateMask=content',method='PATCH',data={'name':note['name'],'content':'Temporary no-login edit verification'})
 assert call('/'+note['name'])['content']=='Temporary no-login edit verification'
finally:
 call('/'+note['name'],method='DELETE')
print('PASS: no-login create/read/edit/delete; notes survive service restart')
try: call('/memos',{'content':'Must be rejected'},headers={'Origin':'https://external.example'})
except urllib.error.HTTPError as e: assert e.code==403;print('PASS: cross-origin mutation rejected')
else: raise AssertionError('Cross-origin write accepted')
