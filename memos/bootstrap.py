"""One-time owner bootstrap. Password saved locally, sent only over SSH stdin."""
import json, os, pathlib, secrets, subprocess
path = pathlib.Path.home() / 'Downloads/MEMOS_CREDENTIALS.txt'
password = secrets.token_urlsafe(24)
with open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as f:
    f.write(f'URL: http://192.168.0.18:5230\nUsername: roneill\nPassword: {password}\n')
remote = r'''
import json, pathlib, urllib.request, urllib.error, subprocess
password = PASSWORD
base='http://127.0.0.1:5230/api/v1'
def call(path, body=None, token=None, method=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    request=urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    with urllib.request.urlopen(request,timeout=15) as response:
        data=response.read()
        return json.loads(data) if data else {}
assert call('/instance/profile')['needsSetup'] is True
user=call('/users',{'username':'roneill','password':password,'displayName':'Ryan','role':'ADMIN'})
assert user['role']=='ADMIN'
session=call('/auth/signin',{'passwordCredentials':{'username':'roneill','password':password}})
assert session.get('accessToken'), list(session)
setting=pathlib.Path('/etc/secrets/memos-instance-setting-general.json')
setting.write_text(json.dumps({'key':'GENERAL','generalSetting':{'disallowUserRegistration':True,'disallowPasswordAuth':False}}))
subprocess.run(['chown','root:memos',str(setting)],check=True)
setting.chmod(0o640)
unit=pathlib.Path('/etc/systemd/system/memos.service')
unit.write_text(unit.read_text().replace('--addr 127.0.0.1','--addr 0.0.0.0'))
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','memos'],check=True)
print('Owner created; registration closed; service bound to LAN.')
'''.replace('PASSWORD', repr(password))
subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','113','--','python3','-'],input=remote,text=True,check=True)
print('Credentials saved privately:',path)
