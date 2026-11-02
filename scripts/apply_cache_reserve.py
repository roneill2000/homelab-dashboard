"""Stage/verify 100 GiB reserve. Applying requires an already-stopped Unraid array."""
import subprocess,sys
REMOTE=r'''
from pathlib import Path
import configparser,json,subprocess,datetime,shutil
p=Path('/var/local/emhttp/disks.ini');c=configparser.ConfigParser(interpolation=None);c.read(p)
cache=c['"cache"'];idx=cache['idx'].strip('"');current=int(cache['sharefloor'].strip('"'))
print(json.dumps({'pool':'cache','current_KiB':current,'desired_KiB':104857600,'requires_array_stopped':True}))
if APPLY:
 var=Path('/var/local/emhttp/var.ini').read_text()
 if 'mdState="STOPPED"' not in var:raise RuntimeError('Array must already be stopped; no changes made')
 config=Path('/boot/config/pools/cache.cfg')
 backup=Path('/boot/config/homelab-backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,exist_ok=True)
 shutil.copy2(config,backup/config.name)
 result=subprocess.run(['/usr/local/sbin/emcmd',f'changeDisk=Apply&diskShareFloor.{idx}=104857600'],capture_output=True,text=True)
 if result.returncode:raise RuntimeError('Unraid rejected reserve change')
 if 'diskShareFloor="104857600"' not in config.read_text():raise RuntimeError('Reserve persistence verification failed')
 print(json.dumps({'applied':True,'backup':str(backup),'reserve_GiB':100}))
'''
if __name__=='__main__':subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],input=REMOTE.replace('APPLY','True' if '--apply' in sys.argv else 'False'),text=True,check=True)
