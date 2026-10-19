"""Add one read-only endpoint to the existing syslog monitor, with rollback."""
from pathlib import Path
import json,subprocess
REMOTE=r"""
import ast,json,pathlib,datetime,shutil,subprocess,time,urllib.request
p=json.loads(PAYLOAD)
path=pathlib.Path('/usr/local/bin/syslog-monitor.py');source=path.read_text()
marker='# HOMELAB_NIGHTLY_SNAPSHOT_V1'
if marker not in source:
 needle="        if path == '/api/status':"
 # Source can use either quote style; insert by AST line location.
 tree=ast.parse(source)
 method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='handle_get_or_head')
 node=next(n for n in method.body if isinstance(n,ast.If))
 lines=source.splitlines(keepends=True)
 route='''        # HOMELAB_NIGHTLY_SNAPSHOT_V1
        if path == '/api/nightly-snapshot':
            from pathlib import Path
            from homelab_log_snapshot import snapshot
            try:
                payload = snapshot(Path(LOG_DIR), CRITICAL_PATTERNS, WARNING_PATTERNS)
                self._send_bytes(200, 'application/json; charset=utf-8', json.dumps(payload).encode('utf-8'), head_only)
            except Exception:
                self._send_bytes(503, 'application/json', b'{"error":"Snapshot unavailable"}', head_only)
            return
'''
 lines[node.lineno-1:node.lineno-1]=[route];source=''.join(lines)
compile(source,str(path),'exec');compile(p['module'],'snapshot','exec')
backup=path.parent/('homelab-syslog-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
module=path.parent/'homelab_log_snapshot.py'
shutil.copy2(path,backup/path.name)
existed=module.exists()
if existed:shutil.copy2(module,backup/module.name)
try:
 module.write_text(p['module']);path.write_text(source)
 subprocess.run(['systemctl','restart','syslog-monitor'],check=True)
 for attempt in range(15):
  try:
   result=json.load(urllib.request.urlopen('http://127.0.0.1:8999/api/nightly-snapshot',timeout=15))
   assert result['files_scanned']>0
   print(json.dumps({'backup':str(backup),'files_scanned':result['files_scanned'],'groups':result['distinct_groups'],'matched':result['matched_entries'],'coverage_limited':result['coverage_limited']}));break
  except Exception:
   if attempt==14:raise
   time.sleep(1)
except Exception:
 shutil.copy2(backup/path.name,path)
 if existed:shutil.copy2(backup/module.name,module)
 else:module.unlink(missing_ok=True)
 subprocess.run(['systemctl','restart','syslog-monitor'],check=True)
 raise RuntimeError('Snapshot endpoint failed; original restored') from None
"""
if __name__=='__main__':
 data={'module':(Path(__file__).parent/'log_snapshot.py').read_text()}
 script=REMOTE.replace('json.loads(PAYLOAD)','json.loads('+repr(json.dumps(data))+')')
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','python3','-'],input=script,text=True,check=True)
