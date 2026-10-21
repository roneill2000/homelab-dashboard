"""Verify private login, note persistence and closed registration without logging credentials."""
import pathlib, subprocess
password=(pathlib.Path.home()/'Downloads/MEMOS_CREDENTIALS.txt').read_text().split('Password: ',1)[1].strip()
script=r'''
import json,urllib.request,urllib.error,subprocess,time
base='http://127.0.0.1:5231/api/v1'
def call(path,body=None,token=None,method=None):
 headers={'Content-Type':'application/json'}
 if token: headers['Authorization']='Bearer '+token
 req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
 with urllib.request.urlopen(req,timeout=15) as r:
  data=r.read()
  return json.loads(data) if data else {}
p=call('/instance/profile')
assert p['needsSetup'] is False and p['accessMode']=='INSTANCE_ACCESS_MODE_PRIVATE'
token=call('/auth/signin',{'passwordCredentials':{'username':'roneill','password':PASSWORD}})['accessToken']
note=call('/memos',{'content':'Memos installation persistence check. Temporary test note.','visibility':'PRIVATE'},token)
name=note['name']
subprocess.run(['systemctl','restart','memos'],check=True)
time.sleep(2)
assert call('/'+name,token=token)['content']==note['content']
for path,body in [('/memos',None),('/'+name,None),('/users',{'username':'installation-test-denied','password':'invalid-unused-account-password'})]:
 try: call(path,body)
 except urllib.error.HTTPError as e: assert e.code in (401,403),e.code
 else: raise AssertionError('Anonymous request unexpectedly permitted: '+path)
call('/'+name,token=token,method='DELETE')
print(json.dumps({'version':p['version'],'private':True,'registration_closed':True,'login':True,'note_persisted_after_restart':True,'test_note_deleted':True}))
'''.replace('PASSWORD',repr(password))
subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','113','--','python3','-'],input=script,text=True,check=True)
