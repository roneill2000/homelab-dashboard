"""Start Unraid's stock mover above 85% cache use; defer while a download is writing; never move files ourselves."""
import fcntl,json,os,subprocess,time
from pathlib import Path
THRESHOLD=85          # was 70; the cache's base load is ~60 GB, so 70% left ~107 GB and fired mid-download
CEILING=92            # even with a download active, start at or above this: a full cache is worse than SSD contention
ACTIVE_WINDOW_S=180   # a download is active when a regular file under the share's cache copy is newer than this
SHARE=Path('/mnt/cache/4TB')  # the 4TB share's cache copy (shareCachePool="cache")
MAX_DEPTH=3           # levels below SHARE we scan; 4TB/Movies/<item>/<file>.mkv sits at level 3
MAX_ENTRIES=50000     # walk budget; past it treat the download as active (safer: defer, and the ceiling still starts)
STATE=Path('/var/run/homelab-cache-guard.json')

def mover_running():
 try:
  pid=int(Path('/var/run/mover.pid').read_text())
  return b'/usr/local/sbin/mover' in (Path('/proc')/str(pid)/'cmdline').read_bytes()
 except (OSError,ValueError):return False

def download_active(root=SHARE,now=None,window=ACTIVE_WINDOW_S,max_depth=MAX_DEPTH,max_entries=MAX_ENTRIES):
 # Bounded: scandir down to max_depth levels, no symlinks, stop at the first fresh file. Measured on Unraid:
 # 1572 entries at depth 3 under /mnt/disk1/4TB cost 2.0 ms, /mnt/cache/appdata 411 entries 6.9 ms, so far under 1 s.
 cutoff=(now if now is not None else time.time())-window
 stack=[(str(root),1)];visited=0
 while stack:
  directory,depth=stack.pop()
  try:entries=list(os.scandir(directory))
  except OSError:continue  # root missing: no 4TB file on cache yet, so nothing is writing there
  for entry in entries:
   visited+=1
   if visited>max_entries:return True
   try:
    if entry.is_symlink():continue
    if entry.is_file(follow_symlinks=False):
     if entry.stat(follow_symlinks=False).st_mtime>=cutoff:return True
    elif entry.is_dir(follow_symlinks=False) and depth<max_depth:stack.append((entry.path,depth+1))
   except OSError:continue
 return False

def should_start(used_pct,running,array_started,mounted,active=False):
 if not mounted or not array_started or running:return False
 if used_pct is None:return False
 if used_pct>=CEILING:return True
 return used_pct>=THRESHOLD and not active

def main():
 with open('/var/run/homelab-cache-guard.lock','w') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  mounted=os.path.ismount('/mnt/cache')
  var=Path('/var/local/emhttp/var.ini').read_text()
  started='mdState="STARTED"' in var
  running=mover_running();now=int(time.time());pct=None
  if mounted:
   s=os.statvfs('/mnt/cache');pct=100*(s.f_blocks-s.f_bavail)/s.f_blocks
  active=download_active(SHARE,now)
  if not started or not mounted:
   action,reason='array_unavailable','array stopped or cache not mounted'
  elif running:
   action,reason='already_running','mover already running'
  elif active and pct>=CEILING:
   action,reason='started_ceiling','%.1f%% at or above ceiling %d%% during a download' % (pct,CEILING)
  elif active and pct>=THRESHOLD:
   action,reason='deferred_download','%.1f%% over threshold, but a file in %s changed in the last %d s' % (pct,SHARE,ACTIVE_WINDOW_S)
  elif pct>=THRESHOLD:
   action,reason='started','%.1f%% at or above threshold %d%% with no download active' % (pct,THRESHOLD)
  else:
   action,reason='below_threshold','%.1f%% under threshold %d%%' % (pct,THRESHOLD)
  if should_start(pct or 0,running,started,mounted,active):
   log=Path('/var/log/homelab-cache-mover.log')
   if log.exists() and log.stat().st_size>1024*1024:log.write_text('')
   with log.open('ab') as output:
    subprocess.Popen(['/usr/local/sbin/mover','start'],stdout=output,stderr=output,start_new_session=True)
  report=dict(observed_at=now,used_pct=pct,threshold_pct=THRESHOLD,ceiling_pct=CEILING,active_window_s=ACTIVE_WINDOW_S,active_download=active,action=action,reason=reason)
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(report));tmp.chmod(0o600);os.replace(tmp,STATE)
  print(json.dumps(report))

if __name__=='__main__':main()
