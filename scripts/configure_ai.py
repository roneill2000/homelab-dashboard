"""Reuse Open WebUI's desktop model credential entirely on the home hosts."""
import subprocess
SCRIPT=r'''
import subprocess,json
read=r"""
import pathlib,json,urllib.request
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  cmd=(p/'cmdline').read_bytes()
  if b'open_webui' not in cmd and b'uvicorn' not in cmd:continue
  env=dict(x.decode().split('=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
  key=env.get('OPENAI_API_KEY') or env.get('OPENAI_API_KEYS','').split(';')[0]
  if not key:continue
  url=env.get('OPENAI_API_BASE_URL','')
  if url!='http://192.168.0.12:8080/v1':continue
  request=urllib.request.Request(url+'/models',headers={'Authorization':'Bearer '+key})
  with urllib.request.urlopen(request,timeout=5) as response:assert isinstance(json.load(response).get('data'),list)
  print(json.dumps({'url':url,'token':key}));break
 except (OSError,ValueError):continue
else:raise RuntimeError('Verified desktop model connection not found')
"""
config=subprocess.check_output(['pct','exec','112','--','python3','-c',read],text=True)
assert json.loads(config)['token']
write=r"""
import json,os,pathlib,sys
value=json.load(sys.stdin)
p=pathlib.Path('/etc/homelab-dashboard');p.mkdir(mode=0o700,exist_ok=True)
stage=p/'ai.json.new';fd=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as out:json.dump(value,out)
stage.chmod(0o600);os.replace(stage,p/'ai.json')
"""
subprocess.run(['pct','exec','104','--','python3','-c',write],input=config,text=True,check=True)
print('Desktop model credential configured privately in LXC104; no credential copied to workstation.')
'''
if __name__=='__main__':subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','python3','-'],input=SCRIPT,text=True,check=True)
