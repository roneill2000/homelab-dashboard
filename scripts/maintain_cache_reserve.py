"""One explicitly authorized Unraid maintenance window; run on host from /root.

Uses stock emcmd stop/start, never force-unmounts or kills unrelated processes.
State/logs live on the boot device so an array unmount cannot interrupt recovery.
"""
from pathlib import Path
import configparser
import datetime
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request

BOOT=Path('/boot/config/homelab-backups')
ROOT=Path('/mnt/disk1/.homelab-nightly')
CRON=Path('/boot/config/plugins/dynamix/homelab-qwen.cron')
TARGET=104857600

def ini(path,flat=False):
    c=configparser.ConfigParser(interpolation=None)
    c.read_string(('[state]\n' if flat else '')+Path(path).read_text())
    return {s.strip('"'):{k:v.strip('"') for k,v in c[s].items()} for s in c.sections()}

def run(args,timeout=30):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=timeout)

def state():return ini('/var/local/emhttp/var.ini',True)['state']

def wait_state(expected,seconds=300):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if state().get('mdstate')==expected:return
        time.sleep(2)
    raise RuntimeError('Array did not reach '+expected+'; no force unmount attempted')

def main():
    os.umask(0o077);os.chdir('/root')
    folder=BOOT/('reserve-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True);progress={'status':'preflight','backup':str(folder)}
    def save(**values):
        progress.update(values,updated_at=int(time.time()));(folder/'result.json').write_text(json.dumps(progress))
        print(json.dumps(progress),flush=True)
    original_cron=None;interrupted=False
    try:
        v=state();assert v.get('mdstate')=='STARTED','Array must initially be started'
        assert int(v.get('mdresync','-1'))==0 and int(v.get('mdresyncpos','-1'))==0,'Parity/rebuild active or unverified'
        assert subprocess.run(['pgrep','-f','[/]usr/local/sbin/mover'],stdout=subprocess.DEVNULL).returncode==1,'Mover active'
        locks=run(['smbstatus','--locks'])
        assert 'RDWR' not in locks and 'WRONLY' not in locks,'Writable SMB locks present; retry after copies finish'
        with urllib.request.urlopen('http://192.168.0.2/api/storage',timeout=15) as response:storage=json.load(response)
        backup=storage['activity']['recovery']['backup']
        assert backup['running'] is False and 0<=time.time()-backup['observed_at']<180,'Backup busy or unverified'
        cache=ini('/var/local/emhttp/disks.ini')['cache'];idx=cache['idx']
        if int(cache['sharefloor'])==TARGET:save(status='already_applied');return
        running=run(['docker','ps','--format','{{.Names}}']).splitlines()
        save(running_containers=running,old_reserve_KiB=int(cache['sharefloor']))
        shutil.copy2('/boot/config/pools/cache.cfg',folder/'cache.cfg')
        original_cron=CRON.read_bytes();(folder/CRON.name).write_bytes(original_cron)
        CRON.unlink();run(['/bin/bash','/usr/local/sbin/update_cron'])
        import fcntl
        with open(ROOT/'run.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Exact manager command only; no active GPU job can own this lock.
            pids=[]
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit():continue
                try:args=(proc/'cmdline').read_bytes().split(b'\0')
                except OSError:continue
                if args[:2]==[b'/usr/bin/python3',str(ROOT/'qwen_service.py').encode()] and b'--ensure' not in args:
                    os.kill(int(proc.name),signal.SIGTERM);pids.append(proc)
            for _ in range(30):
                if not any(p.exists() for p in pids):break
                time.sleep(1)
            assert not any(p.exists() for p in pids),'Qwen manager did not stop'
        # Close the array-backed lock before requesting an unmount.
        save(status='stopping');interrupted=True
        run(['/usr/local/sbin/emcmd','cmdStop=Stop'],timeout=300);wait_state('STOPPED')
        save(status='applying')
        run(['/usr/local/sbin/emcmd',f'changeDisk=Apply&diskShareFloor.{idx}={TARGET}'])
        config=Path('/boot/config/pools/cache.cfg').read_text()
        assert f'diskShareFloor="{TARGET}"' in config,'Persistent reserve verification failed'
        save(status='starting');run(['/usr/local/sbin/emcmd','cmdStart=Start'],timeout=300);wait_state('STARTED')
        assert int(ini('/var/local/emhttp/disks.ini')['cache']['sharefloor'])==TARGET,'Effective reserve verification failed'
        for _ in range(60):
            if subprocess.run(['docker','info','--format','{{.ServerVersion}}'],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0:break
            time.sleep(2)
        else:raise RuntimeError('Docker did not become ready after array start')
        # Restore exactly those containers that were running before maintenance.
        for name in running:
            if run(['docker','inspect','--format','{{.State.Running}}',name]).strip()!='true':run(['docker','start',name],timeout=90)
        save(status='complete',reserve_GiB=100,array_state='STARTED')
    except Exception as exc:
        save(status='failed',error=str(exc)[:300])
        # A failed setting operation must not strand an otherwise stopped array.
        if interrupted and state().get('mdstate')=='STOPPING':
            try:wait_state('STOPPED',300)
            except Exception:pass
        if interrupted and state().get('mdstate')=='STOPPED':
            try:run(['/usr/local/sbin/emcmd','cmdStart=Start'],timeout=300);wait_state('STARTED')
            except Exception as recovery:save(restart_error=str(recovery)[:200])
        raise
    finally:
        if original_cron is not None and state().get('mdstate')=='STARTED':
            CRON.write_bytes(original_cron);CRON.chmod(0o644)
            run(['/bin/bash','/usr/local/sbin/update_cron'])
            if state().get('mdstate')=='STARTED':
                run(['/usr/bin/python3',str(ROOT/'qwen_service.py'),'--ensure'])
        save(final_array_state=state().get('mdstate'))

if __name__=='__main__':main()
