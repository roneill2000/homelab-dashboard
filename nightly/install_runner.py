"""Install runner/report files; scheduling is an explicit final flag."""
from pathlib import Path
import ast,json,subprocess,sys
REMOTE=r'''
import pathlib,json,datetime,shutil,subprocess,os
p=json.loads(PAYLOAD)
root=pathlib.Path('/mnt/disk1/.homelab-nightly');root.mkdir(mode=0o700,exist_ok=True)
backup=root/'backups'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup.mkdir(parents=True,mode=0o700)
runner=root/'runner.py'
if runner.exists():shutil.copy2(runner,backup/runner.name)
compile(p['runner'],str(runner),'exec');stage=root/'runner.py.new';stage.write_text(p['runner']);stage.chmod(0o700);os.replace(stage,runner)
(root/'log_monitor.py').write_text(p['log_monitor'])
(root/'reports').mkdir(mode=0o700,exist_ok=True)
manifest=dict(p['manifest'],device='/dev/dri/renderD129',schedule='03:00 America/New_York')
(root/'manifest.json').write_text(json.dumps(manifest,indent=2))
if p['schedule']:
 cron=pathlib.Path('/boot/config/plugins/dynamix/homelab-nightly.cron')
 if cron.exists():shutil.copy2(cron,backup/cron.name)
 line='0 3 * * * /usr/bin/timeout -s TERM -k 60 3h /usr/bin/python3 /mnt/disk1/.homelab-nightly/runner.py --scheduled >> /mnt/disk1/.homelab-nightly/runner.log 2>&1\n'  # hard stop after 3 h; SIGTERM saves a failed report and frees the GPU
 cron.write_text('# Homelab local log review; host timezone America/New_York\n'+line)
 subprocess.run(['/bin/bash','/usr/local/sbin/update_cron'],check=True)
 active=subprocess.check_output(['crontab','-c','/etc/cron.d','-l'],text=True)
 if line.strip() not in active:raise RuntimeError('Cron verification failed')
 print(json.dumps({'backup':str(backup),'schedule':'0 3 * * *','timezone':str(pathlib.Path('/etc/localtime').resolve()),'enabled':True}))
else: print(json.dumps({'backup':str(backup),'runner_installed':True,'schedule_enabled':False}))
'''
if __name__=='__main__':
 payload=dict(runner=(Path(__file__).parent/'runner.py').read_text(),schedule='--schedule' in sys.argv,log_monitor=(Path(__file__).parent.parent/'log_monitor.py').read_text())
 tree=ast.parse(payload['runner'])
 values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('MODEL','MODEL_REVISION','MODEL_REPO','MODEL_FILE','MODEL_SHA256','IMAGE')}
 payload['manifest']={k:values[v] for k,v in [('model','MODEL'),('revision','MODEL_REVISION'),('repository','MODEL_REPO'),('file','MODEL_FILE'),('sha256','MODEL_SHA256'),('image','IMAGE')]}
 script=REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(payload))+')')
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.4','python3','-'],input=script,text=True,check=True)
