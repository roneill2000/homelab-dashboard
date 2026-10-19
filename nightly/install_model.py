"""Install the pinned public model and runtime on Unraid; never sends logs out."""
import ast
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import hashlib,json,pathlib,subprocess,urllib.request
p=json.loads(PAYLOAD)
root=pathlib.Path('/mnt/disk1/.homelab-nightly-models')
root.mkdir(mode=0o700,exist_ok=True)
target=root/p['MODEL_FILE'];target.parent.mkdir(mode=0o700,exist_ok=True)
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
 return h.hexdigest()
if not target.exists() or digest(target)!=p['MODEL_SHA256']:
 url='https://huggingface.co/'+p['MODEL_REPO']+'/resolve/'+p['MODEL_REVISION']+'/'+target.name
 stage=target.with_suffix('.partial')
 with urllib.request.urlopen(url,timeout=120) as source,stage.open('wb') as out:
  while True:
   block=source.read(4*1024*1024)
   if not block:break
   out.write(block)
 if digest(stage)!=p['MODEL_SHA256']:raise ValueError('Model checksum mismatch')
 stage.replace(target)
subprocess.run(['docker','pull',p['IMAGE']],check=True)
print(json.dumps({'model':p['MODEL'],'bytes':target.stat().st_size,'sha256_verified':True}))
'''

if __name__ == '__main__':
    tree = ast.parse((Path(__file__).parent/'runner.py').read_text())
    names = {'MODEL','MODEL_REPO','MODEL_REVISION','MODEL_FILE','MODEL_SHA256','IMAGE'}
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id in names}
    script = REMOTE.replace('json.loads(PAYLOAD)', 'json.loads('+repr(json.dumps(values))+')')
    subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],
                   input=script,text=True,check=True)
