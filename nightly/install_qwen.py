"""Install the local prompt broker; credentials stay in root-only remote files."""
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parent
REMOTE=r'''
import datetime,fcntl,json,os,pathlib,secrets,shutil,signal,subprocess,time,urllib.request
p=json.loads(PAYLOAD)
root=pathlib.Path('/mnt/disk1/.homelab-nightly')
if not root.is_dir():raise RuntimeError('Install nightly model first')
# Exclude new questions/nightly startup throughout replacement.
deploy_lock=open(root/'run.lock','a')
try:fcntl.flock(deploy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise RuntimeError('Model is in use; retry installation afterward')
tokenfile=root/'qwen-token'
token=tokenfile.read_text().strip() if tokenfile.exists() else secrets.token_urlsafe(48)
# Refuse replacement while an existing question owns the GPU.
try:
 req=urllib.request.Request('http://192.168.0.4:11438/job',headers={'Authorization':'Bearer '+token})
 with urllib.request.urlopen(req,timeout=3) as response:job=json.load(response)
except Exception:job={}
if job.get('status') in ('queued','loading','generating'):raise RuntimeError('An on-demand question is active; retry deployment afterward')
backup=root/'backups'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup.mkdir(mode=0o700,parents=True)
# Stop only the idle broker process whose complete command matches our file.
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:args=(proc/'cmdline').read_bytes().split(b'\0')
 except OSError:continue
 if args[:2]==[b'/usr/bin/python3',str(root/'qwen_service.py').encode()] and b'--ensure' not in args:
  os.kill(int(proc.name),signal.SIGTERM)
for name,source in p.items():
 compile(source,name,'exec')
 target=root/name
 if target.exists():shutil.copy2(target,backup/name)
 stage=target.with_suffix('.py.new');stage.write_text(source);stage.chmod(0o700);os.replace(stage,target)
if not tokenfile.exists():
 fd=os.open(tokenfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(token)
os.chmod(tokenfile,0o600)
cron=pathlib.Path('/boot/config/plugins/dynamix/homelab-qwen.cron')
if cron.exists():shutil.copy2(cron,backup/cron.name)
cron.write_text('# Local Qwen prompt broker; GPU starts only on request\n* * * * * test ! -f /mnt/disk1/.homelab-nightly/qwen_service.py || /usr/bin/python3 /mnt/disk1/.homelab-nightly/qwen_service.py --ensure\n')
subprocess.run(['/bin/bash','/usr/local/sbin/update_cron'],check=True,stdout=subprocess.DEVNULL)
for attempt in range(10):
 subprocess.run(['/usr/bin/python3',str(root/'qwen_service.py'),'--ensure'],check=True)
 time.sleep(1)
 try:
  req=urllib.request.Request('http://192.168.0.4:11438/job',headers={'Authorization':'Bearer '+token})
  with urllib.request.urlopen(req,timeout=2) as response:assert json.load(response)['status']=='idle'
  break
 except Exception:
  if attempt==9:raise RuntimeError('Broker failed to start')
# Returned only to installer memory, never printed to terminal or saved locally.
print(json.dumps({'token':token,'backup':str(backup)}))
'''
CLIENT=r'''
import json,pathlib,os
p=json.loads(PAYLOAD)
folder=pathlib.Path('/etc/homelab-dashboard');folder.mkdir(mode=0o700,exist_ok=True)
path=folder/'qwen.json';stage=folder/'qwen.json.new'
fd=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump({'token':p['token']},f)
os.chmod(stage,0o600);os.replace(stage,path)
print('Dashboard Qwen credential installed (private)')
'''
if __name__=='__main__':
 payload={name:(ROOT/name).read_text() for name in ('qwen_service.py','runner.py','log_snapshot.py','gpu_probe.py')}
 script=REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(payload))+')')
 result=subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],input=script,text=True,capture_output=True)
 if result.returncode:raise RuntimeError('Broker installation failed: '+result.stderr[-1500:])
 value=json.loads(result.stdout)
 script=CLIENT.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(value))+')')
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','104','--','python3','-'],input=script,text=True,check=True)
 print(json.dumps({'broker':'192.168.0.4:11438','backup':value['backup'],'status':'idle'}))
