"""Create a dedicated Moe Memos PAT and save it locally without printing it."""
import json,os,pathlib,subprocess
remote=r'''
import urllib.request,json
base='http://127.0.0.1:5230/api/v1'
with urllib.request.urlopen(base+'/auth/me') as r: user=json.load(r)['user']
req=urllib.request.Request(base+'/'+user['name']+'/personalAccessTokens',data=json.dumps({'description':'Moe Memos Android','expiresInDays':0}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req) as r: data=json.load(r)
# Verify the real token against the authenticated backend, without the auto-login proxy.
req=urllib.request.Request('http://127.0.0.1:5231/api/v1/auth/me',headers={'Authorization':'Bearer '+data['token']})
with urllib.request.urlopen(req) as r: assert json.load(r)['user']['name']==user['name']
print(json.dumps(data))
'''
result=subprocess.check_output(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','113','--','python3','-'],input=remote,text=True)
data=json.loads(result)
path=pathlib.Path.home()/'Downloads/MOE_MEMOS_ANDROID.txt'
with open(path,'x',opener=lambda p,f:os.open(p,f,0o600)) as f:
 f.write('Host: http://192.168.0.18:5230\nAccess token: '+data['token']+'\n\nDedicated token: Moe Memos Android\nExpires: never (revoke in Memos settings when no longer needed)\n')
print('Dedicated Android token created and verified. Saved privately:',path)
