"""Install the authorized five-minute mover guard. Reserve is a separate stopped-array step."""
from pathlib import Path
import json,subprocess

# The array check was added on Unraid by hand on 11 Nov 2026 (backup *.bak-20261111-fsstate): with the array stopped
# the guard hung, so the cron line must test fsState first. Keep it byte-identical to the live line.
GUARD_LINE = """*/5 * * * * grep -qs '^fsState="Started"' /var/local/emhttp/var.ini && /usr/bin/python3 /boot/config/plugins/dynamix/homelab-cache-guard.py >/dev/null 2>&1"""
CRON_COMMENT = '# Start stock mover above 85%, deferred while a download is writing (only while the array is started)'

REMOTE=r'''
import pathlib,datetime,json,subprocess,shutil,os
p=json.loads(PAYLOAD)
root=pathlib.Path('/boot/config/plugins/dynamix')
backup=pathlib.Path('/boot/config/homelab-backups')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,exist_ok=True)
for target in [root/'homelab-cache-guard.py',root/'homelab-cache-guard.cron',pathlib.Path('/boot/config/pools/cache.cfg')]:
 if target.exists():shutil.copy2(target,backup/target.name)
compile(p['source'],'cache_guard.py','exec')
(root/'homelab-cache-guard.py').write_text(p['source'])
line=p['cron_line'];comment=p['cron_comment']
(root/'homelab-cache-guard.cron').write_text(comment+'\n'+line+'\n')
subprocess.run(['/bin/bash','/usr/local/sbin/update_cron'],check=True)
cron=subprocess.check_output(['crontab','-c','/etc/cron.d','-l'],text=True)
assert line in cron, 'cron line not installed'
assert comment in cron, 'cron comment not installed'
assert "grep -qs '^fsState=\"Started\"' /var/local/emhttp/var.ini &&" in cron, 'fsState guard missing'
subprocess.run(['/usr/bin/python3',str(root/'homelab-cache-guard.py')],check=True)
print(json.dumps({'backup':str(backup),'schedule':line,'reserve_changed':False}))
'''
if __name__=='__main__':
 payload={'source':(Path(__file__).resolve().parent.parent/'backend'/'cache_guard.py').read_text(),'cron_line':GUARD_LINE,'cron_comment':CRON_COMMENT}
 script=REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(payload))+')')
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],input=script,text=True,check=True)
