"""Start Unraid's stock mover above 70% cache use; never move files ourselves."""
import fcntl,json,os,subprocess,time
from pathlib import Path
THRESHOLD=70
STATE=Path('/var/run/homelab-cache-guard.json')

def mover_running():
 try:
  pid=int(Path('/var/run/mover.pid').read_text())
  return b'/usr/local/sbin/mover' in (Path('/proc')/str(pid)/'cmdline').read_bytes()
 except (OSError,ValueError):return False

def should_start(used_pct,running,array_started,mounted):
 return mounted and array_started and not running and used_pct>=THRESHOLD

def main():
 with open('/var/run/homelab-cache-guard.lock','w') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  mounted=os.path.ismount('/mnt/cache')
  var=Path('/var/local/emhttp/var.ini').read_text()
  started='mdState="STARTED"' in var
  running=mover_running();pct=None
  if mounted:
   s=os.statvfs('/mnt/cache');pct=100*(s.f_blocks-s.f_bavail)/s.f_blocks
  action='already_running' if running else 'below_threshold'
  if not started or not mounted:action='array_unavailable'
  if should_start(pct or 0,running,started,mounted):
   log=Path('/var/log/homelab-cache-mover.log')
   if log.exists() and log.stat().st_size>1024*1024:log.write_text('')
   with log.open('ab') as output:
    subprocess.Popen(['/usr/local/sbin/mover','start'],stdout=output,stderr=output,start_new_session=True)
   action='started'
  report=dict(observed_at=int(time.time()),used_pct=pct,threshold_pct=THRESHOLD,action=action)
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(report));tmp.chmod(0o600);os.replace(tmp,STATE)
  print(json.dumps(report))

if __name__=='__main__':main()
