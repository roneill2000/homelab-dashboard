"""Deploy via Proxmox SSH. Existing credentials never leave LXC 104."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parent
REMOTE = r"""
import ast, datetime, hashlib, json, os, pathlib, shutil, subprocess, time, urllib.request
payload = json.loads(PAYLOAD)
backend = pathlib.Path('/usr/local/bin/homelab-dashboard.py')
source = backend.read_text()
original_sha = hashlib.sha256(source.encode()).hexdigest()
node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'INDEX_HTML' for t in n.targets))
lines = source.splitlines(keepends=True)
lines[node.lineno-1:node.end_lineno] = ["INDEX_HTML = open('/opt/homelab-dashboard/index.html', encoding='utf-8').read()\n"]
updated = ''.join(lines)
patch_namespace = {}
exec(compile(payload['backend_patch.py'], 'backend_patch.py', 'exec'), patch_namespace)
updated = patch_namespace['patch_backend'](updated)
updated = patch_namespace['patch_log_details'](updated)
updated = patch_namespace['patch_log_groups'](updated)
marker = '# HOMELAB_STORAGE_V2'
if marker not in updated:
    updated = updated.replace('class DashboardHandler(', 'import storage_monitor\nstorage_monitor.start()\n\n'+marker+'\nclass DashboardHandler(', 1)
    route = '''        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/storage":
            try:
                window = int(urllib.parse.parse_qs(parsed.query).get("window", ["3600"])[0])
            except ValueError:
                self.send_error(400, "Invalid history window")
                return
            try:
                body = json.dumps(storage_monitor.snapshot(window)).encode("utf-8")
            except Exception:
                self.send_error(503, "Storage history unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
'''
    assert '    def do_GET(self):\n' in updated
    updated = updated.replace('    def do_GET(self):\n', '    def do_GET(self):\n'+route, 1)
compile(updated, str(backend), 'exec')
compile(payload['storage_monitor.py'], 'storage_monitor.py', 'exec')
compile(payload['activity_monitor.py'], 'activity_monitor.py', 'exec')
compile(payload['log_monitor.py'], 'log_monitor.py', 'exec')
assert '/*__INITIAL_TELEMETRY_SLOT__*/' in payload['index.html']
backup = pathlib.Path('/opt/homelab-dashboard/backups') / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
backup.mkdir(parents=True, mode=0o700)
os.chmod(backup.parent, 0o700)
targets = {'/usr/local/bin/log_monitor.py': payload['log_monitor.py'], '/usr/local/bin/activity_monitor.py': payload['activity_monitor.py'], str(backend): updated, '/usr/local/bin/storage_monitor.py': payload['storage_monitor.py'], '/opt/homelab-dashboard/index.html': payload['index.html']}
manifest = {}
for target, content in targets.items():
    p = pathlib.Path(target)
    manifest[target] = p.exists()
    if p.exists(): shutil.copy2(p, backup / p.name)
    stage = p.with_name(p.name+'.new')
    stage.write_text(content)
    os.chmod(stage, p.stat().st_mode & 0o777 if p.exists() else 0o644)
(backup/'manifest.json').write_text(json.dumps(manifest))
if hashlib.sha256(backend.read_bytes()).hexdigest() != original_sha:
    raise RuntimeError('Backend changed during staging; no files installed')
try:
    for target in targets:
        p = pathlib.Path(target)
        os.replace(p.with_name(p.name+'.new'), p)
    subprocess.run(['systemctl','restart','homelab-dashboard'], check=True)
    for attempt in range(25):
        try:
            with urllib.request.urlopen('http://127.0.0.1/api/storage', timeout=2) as response:
                data = json.load(response)
            assert len(data['disks']) == 4
            break
        except Exception:
            if attempt == 24: raise
            time.sleep(1)
except Exception:
    for target, existed in manifest.items():
        if existed: shutil.copy2(backup/pathlib.Path(target).name, target)
        else: pathlib.Path(target).unlink(missing_ok=True)
    subprocess.run(['systemctl','restart','homelab-dashboard'], check=True)
    raise RuntimeError('Deployment failed; previous files restored') from None
print(json.dumps({'backup':str(backup),'status':'healthy','backend_sha256':hashlib.sha256(backend.read_bytes()).hexdigest()}))
"""

if __name__ == '__main__':
    data = {name:(ROOT/name).read_text() for name in ('index.html','storage_monitor.py','activity_monitor.py','log_monitor.py','backend_patch.py')}
    script = REMOTE.replace('json.loads(PAYLOAD)', 'json.loads('+repr(json.dumps(data))+')')
    subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','104','--','python3','-'], input=script, text=True, check=True)
