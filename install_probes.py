"""Install a bounded read-only probe in existing Glances; back up and verify."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parent
SCRIPT = r'''
import pathlib,json,datetime,subprocess,time,urllib.request,shutil
p=json.loads(PAYLOAD)
config=pathlib.Path(p['config']); probe=config.parent/'homelab-host-probe.py'
backup=config.parent/('homelab-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
old=config.read_text() if config.exists() else ''
if config.exists(): shutil.copy2(config,backup/config.name)
if probe.exists(): shutil.copy2(probe,backup/probe.name)
section='\n[amp_homelab]\nenable=true\nrefresh=10\none_line=true\ncommand='+p['command']+'\n'
if '[amp_homelab]' in old:
 if section.strip() not in old: raise RuntimeError('Existing probe configuration differs; inspect before updating')
 updated=old
else: updated=old+section
try:
 probe.write_text(p['source']);probe.chmod(0o644)
 config.write_text(updated)
 subprocess.run(p['restart'],check=True,stdout=subprocess.DEVNULL)
 for attempt in range(30):
  try:
   amps=json.load(urllib.request.urlopen('http://127.0.0.1:61208/api/4/amps',timeout=2))
   data=json.loads(next(a['result'] for a in amps if a.get('name','').lower()=='homelab'))
   assert data['disks'] and time.time()-data['sampled_at']<45
   print(json.dumps({'host':p['kind'],'backup':str(backup),'probe':'healthy','recovery':data.get('recovery') is not None}));break
  except Exception:
   if attempt==29: raise
   time.sleep(1)
except Exception:
 if (backup/config.name).exists(): shutil.copy2(backup/config.name,config)
 else: config.unlink(missing_ok=True)
 if (backup/probe.name).exists(): shutil.copy2(backup/probe.name,probe)
 else: probe.unlink(missing_ok=True)
 subprocess.run(p['restart'],check=True,stdout=subprocess.DEVNULL)
 raise RuntimeError('Probe install failed; original Glances configuration restored') from None
'''

if __name__ == '__main__':
    for kind,host,config,command,restart in [
      ('pve','192.168.0.3','/etc/glances/glances.conf','/usr/bin/python3 /etc/glances/homelab-host-probe.py --kind pve',['systemctl','restart','glances']),
      ('unraid','192.168.0.4','/mnt/user/appdata/glances/glances.conf','/venv/bin/python3 /etc/glances/homelab-host-probe.py --root /rootfs --kind unraid',['docker','restart','Glances'])]:
        data=dict(kind=kind,config=config,command=command,restart=restart,source=(ROOT/'host_probe.py').read_text())
        script=SCRIPT.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(data))+')')
        subprocess.run(['ssh','-o','BatchMode=yes','root@'+host,'python3','-'],input=script,text=True,check=True)
