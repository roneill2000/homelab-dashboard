"""Install the authorized five-minute mover guard. Reserve is a separate stopped-array step."""
from pathlib import Path
import json,subprocess
REMOTE=r'''
import pathlib,datetime,json,subprocess,shutil,os
p=json.loads(PAYLOAD)
root=pathlib.Path('/boot/config/plugins/dynamix')
backup=pathlib.Path('/boot/config/homelab-backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,exist_ok=True)
for target in [root/'homelab-cache-guard.py',root/'homelab-cache-guard.cron',pathlib.Path('/boot/config/pools/cache.cfg')]:
 if target.exists():shutil.copy2(target,backup/target.name)
compile(p['source'],'cache_guard.py','exec')
(root/'homelab-cache-guard.py').write_text(p['source'])
line='*/5 * * * * /usr/bin/python3 /boot/config/plugins/dynamix/homelab-cache-guard.py >/dev/null 2>&1'
(root/'homelab-cache-guard.cron').write_text('# Start stock mover above 70% cache usage\n'+line+'\n')
subprocess.run(['/bin/bash','/usr/local/sbin/update_cron'],check=True)
cron=subprocess.check_output(['crontab','-c','/etc/cron.d','-l'],text=True)
assert line in cron
subprocess.run(['/usr/bin/python3',str(root/'homelab-cache-guard.py')],check=True)
print(json.dumps({'backup':str(backup),'schedule':line,'reserve_changed':False}))
'''
if __name__=='__main__':
 script=REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps({'source':Path(__file__).with_name('cache_guard.py').read_text()}))+')')
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],input=script,text=True,check=True)
