#!/usr/bin/env python3
"""Guarded guest updates for Proxmox LXCs and Unraid Docker containers.

Installed on Proxmox as /usr/local/sbin/homelab-update. The dashboard reaches it
only through a restricted SSH key whose forced command is `homelab-update ssh`,
which accepts exactly `status`, `check` and `start`.

A run snapshots each LXC before `apt-get upgrade`, updates one guest at a time,
health-checks it and stops at the first failure. Nothing is rebooted and
nothing is rolled back automatically; failures report the rollback command.
"""
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path('/var/lib/homelab-update')
STATE = STATE_DIR / 'state.json'
LOCK = Path('/run/homelab-update.lock')
WEEKLY_EXCLUDE = {'itzg-minecraft-server'}  # Ryan, 22 Oct 2026: update by hand
WATCHDOG = Path('/usr/local/bin/homelab-health-watchdog.sh')
UNRAID = 'root@192.168.0.4'
PI = 'root@192.168.0.10'  # Orange Pi Zero 2W: Pi-hole replica, Uptime Kuma, nebula-sync
PI_ID = 'pi:orangepi'
TEMPLATES = '/boot/config/plugins/dockerMan/templates-user'
DOCKER_SCRIPTS = '/usr/local/emhttp/plugins/dynamix.docker.manager/scripts'
UPDATE_STATUS = '/var/lib/docker/unraid-update-status.json'
QWEN_CONTAINER = 'homelab-nightly-llm'
# Lower runs first. DNS replica and the dashboard itself go last; Glances feeds
# the dashboard's disk probes, so it is the last container.
PRIORITY = {'lxc:106': 90, 'pi:orangepi': 92, 'lxc:104': 99, 'docker:Glances': 95}
SNAP_PREFIX = 'preupd_'
APT_ENV = 'DEBIAN_FRONTEND=noninteractive'
APT_UPGRADE = ('apt-get -y -o Dpkg::Options::=--force-confdef '
               '-o Dpkg::Options::=--force-confold upgrade')
LOG_KEEP = 20


def sh(args, timeout=120, check=False):
    """Run a command; return (code, combined output)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        if check and r.returncode:
            raise RuntimeError(f'{args[0]} exited {r.returncode}: {out[-300:]}')
        return r.returncode, out
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(f'{args[0]} timed out after {timeout}s')
        return 124, f'timed out after {timeout}s'


def unraid(command, timeout=120, check=False):
    return sh(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', UNRAID, command],
              timeout=timeout, check=check)


def pi(command, timeout=120, check=False):
    return sh(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', PI, command], timeout=timeout, check=check)


def guest(vmid, command, timeout=120, check=False):
    return sh(['pct', 'exec', str(vmid), '--', 'sh', '-c', command], timeout=timeout, check=check)


# ---------- pure helpers (unit-tested) ----------

def order(items):
    return sorted(items, key=lambda i: (PRIORITY.get(i['id'], 50), i['id']))


def parse_pct_list(text):
    """Running LXCs from `pct list` output."""
    guests = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            guests.append(dict(vmid=int(parts[0]), status=parts[1], name=parts[-1]))
    return guests


def parse_docker_ps(text):
    containers = []
    for line in text.splitlines():
        name, image, state = (line.split('|') + ['', '', ''])[:3]
        if name:
            containers.append(dict(name=name, image=image if ':' in image.split('/')[-1] else image + ':latest',
                                   state=state))
    return containers


def docker_pending(status, image):
    """True/False when Unraid's update check knows, None when it does not."""
    entry = status.get(image)
    if not isinstance(entry, dict) or not entry.get('remote') or not entry.get('local'):
        return None
    return entry['local'] != entry['remote']


def snapshot_name(now):
    return SNAP_PREFIX + time.strftime('%Y%m%d%H%M', time.localtime(now))


def old_snapshots(listing, keep):
    """Older pre-update snapshot names from `pct listsnapshot`, excluding `keep`."""
    names = re.findall(r'(' + SNAP_PREFIX + r'\d{12})', listing)
    return sorted(n for n in set(names) if n != keep)


# Read-only probe run inside each LXC: failed units, then every network mount.
HEALTH_SH = r"""
echo "U $(systemctl --failed --plain --no-legend 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
awk '$3 ~ /^(cifs|smb3|nfs|nfs4)$/ {print $2}' /proc/mounts | while read -r m; do
  if timeout 5 stat -t "$m" >/dev/null 2>&1; then echo "M ok $m"; else echo "M stale $m"; fi
done
awk '$1 !~ /^#/ && $3 ~ /^(cifs|smb3|nfs|nfs4)$/ {print $2}' /etc/fstab 2>/dev/null | while read -r m; do
  awk -v m="$m" '$2==m{f=1} END{exit !f}' /proc/mounts || echo "M missing $m"
done
"""


PI_EXTRA_SH = r'''
read -r _ a1 b1 c1 d1 e1 f1 g1 h1 _ < /proc/stat; sleep 1; read -r _ a2 b2 c2 d2 e2 f2 g2 h2 _ < /proc/stat
echo "C $(( ( (a2+b2+c2+f2+g2+h2) - (a1+b1+c1+f1+g1+h1) ) * 1000 / ( (a2+b2+c2+d2+e2+f2+g2+h2) - (a1+b1+c1+d1+e1+f1+g1+h1) + 1 ) ))"
echo "S $(cut -d' ' -f1-3 /proc/loadavg)|$(free -m | awk '/Mem:/{printf "%d|%d",$3,$2}')|$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)|$(df -P / | awk 'NR==2{print $5}' | tr -d %)|$(cut -d' ' -f1 /proc/uptime)|$(. /etc/os-release; echo $PRETTY_NAME)|$(uname -r)|$(systemctl is-active pihole-FTL)|$(nproc)"
docker ps -a --format 'D {{.Names}}|{{.State}}|{{.Status}}|{{.Image}}' 2>/dev/null
'''


def parse_pi_extra(text):
    info = {'containers': []}
    for line in text.splitlines():
        if line.startswith('C ') and line[2:].strip().isdigit():
            info['cpu'] = round(int(line[2:]) / 10, 1)  # busy share over a 1 s sample, like the Proxmox/Unraid CPU %
        elif line.startswith('S '):
            f = (line[2:].split('|') + [''] * 10)[:10]
            load, used, total, temp, disk, up, osname, kernel, ftl, cpus = f
            num = lambda v, d=None: float(v) if v.replace('.', '', 1).isdigit() else d
            info.update(load=[num(x) for x in load.split()][:3], ram_used_mb=num(used), ram_total_mb=num(total),
                        ram_pct=round(100 * num(used, 0) / num(total), 1) if num(total) else None,
                        temp_c=round(num(temp) / 1000, 1) if num(temp) else None, disk_pct=num(disk),
                        uptime_s=num(up), os=osname or None, kernel=kernel or None, pihole=ftl or None, cpus=num(cpus))
        elif line.startswith('D '):
            name, state, status, image = (line[2:].split('|') + ['', '', '', ''])[:4]
            info['containers'].append(dict(name=name, state=state, status=status, image=image))
    return info


def parse_guest_health(text):
    units, mounts = None, []
    for line in text.splitlines():
        if line == 'U' or line.startswith('U '):  # bare 'U' when output stripping removed the space
            units = line[2:].split()
        elif line.startswith('M '):
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[1] in ('ok', 'stale', 'missing'):
                mounts.append(dict(path=parts[2], state=parts[1]))
    return dict(failed_units=units, mounts=mounts)


def update_summary(state):
    job = state.get('job') or {}
    items = state.get('items') or []
    return dict(checked_at=state.get('checked_at'),
                job={k: job.get(k) for k in ('kind', 'status', 'finished_at', 'error')} if job else None,
                pending=[dict(name=i['name'], kind=i['kind'], detail=i.get('detail')) for i in items if i.get('state') == 'pending'],
                failed=[dict(name=i['name'], kind=i['kind'], detail=i.get('detail'), rollback=i.get('rollback')) for i in items if i.get('state') == 'failed'],
                manual=[dict(name=i['name'], detail=i.get('detail')) for i in items if i.get('state') in ('skipped', 'unknown') and 'hand' in (i.get('detail') or '')])


def blank_state():
    return dict(job=None, items=[], checked_at=None)


# ---------- state ----------

def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return blank_state()


def write_state(state):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE)


class Run:
    def __init__(self, kind):
        self.state = read_state()
        self.state['job'] = dict(kind=kind, status='running', started_at=int(time.time()),
                                 finished_at=None, error=None)
        stamp = time.strftime('%Y%m%dT%H%M%S')
        (STATE_DIR / 'logs').mkdir(mode=0o700, parents=True, exist_ok=True)
        self.log_path = STATE_DIR / 'logs' / f'{stamp}-{kind}.log'
        self.state['job']['log'] = str(self.log_path)
        self.save()

    def save(self):
        write_state(self.state)

    def log(self, text):
        with open(self.log_path, 'a') as f:
            f.write(f'[{time.strftime("%H:%M:%S")}] {text}\n')

    def finish(self, status, error=None):
        self.state['job'].update(status=status, error=error, finished_at=int(time.time()))
        self.save()
        logs = sorted((STATE_DIR / 'logs').glob('*.log'))
        for old in logs[:-LOG_KEEP]:
            old.unlink(missing_ok=True)


# ---------- inventory and checks ----------

def inventory():
    items = []
    _, text = sh(['pct', 'list'], check=True)
    for g in parse_pct_list(text):
        conf = Path(f'/etc/pve/lxc/{g["vmid"]}.conf').read_text()
        if re.search(r'^template:\s*1', conf, re.M):
            continue
        item = dict(id=f'lxc:{g["vmid"]}', vmid=g['vmid'], name=g['name'], kind='LXC',
                    state='skipped' if g['status'] != 'running' else 'unknown',
                    detail='Stopped' if g['status'] != 'running' else '', pending=None)
        items.append(item)
    if pi('true', timeout=15)[0] == 0:
        items.append(dict(id=PI_ID, name='orangepi', kind='Pi', state='unknown', detail='', pending=None))
    else:
        items.append(dict(id=PI_ID, name='orangepi', kind='Pi', state='skipped', detail='Unreachable', pending=None))
    code, text = unraid("docker ps -a --format '{{.Names}}|{{.Image}}|{{.State}}'", check=True)
    _, templates = unraid(f'ls {TEMPLATES}')
    for c in parse_docker_ps(text):
        item = dict(id=f'docker:{c["name"]}', name=c['name'], image=c['image'], kind='Docker',
                    state='unknown', detail='', pending=None)
        if c['state'] != 'running':
            item.update(state='skipped', detail='Stopped')
        elif f'my-{c["name"]}.xml' not in templates.split():
            item.update(state='skipped', detail='No Unraid template; update it by hand')
        items.append(item)
    return order(items)


def check_lxc(item):
    code, out = guest(item['vmid'], f'{APT_ENV} apt-get update -qq >/dev/null 2>&1 || echo APTFAIL; '
                                    'apt-get -s upgrade 2>/dev/null | grep -c "^Inst" || true', timeout=300)
    count = out.strip().splitlines()[-1] if out.strip() else ''
    if code or 'APTFAIL' in out or not count.isdigit():
        item.update(state='unknown', detail='Check failed', pending=None)
    else:
        item.update(pending=int(count), state='pending' if int(count) else 'current',
                    detail=f'{count} package{"s" if count != "1" else ""}' if int(count) else 'Up to date')


def check_pi(item):
    code, out = pi(f'{APT_ENV} apt-get update -qq >/dev/null 2>&1 || echo APTFAIL; '
                   'apt-get -s upgrade 2>/dev/null | grep -c "^Inst" || true', timeout=300)
    count = out.strip().splitlines()[-1] if out.strip() else ''
    if code or 'APTFAIL' in out or not count.isdigit():
        item.update(state='unknown', detail='Check failed', pending=None)
        return
    n = int(count)
    # nebula-sync follows :latest via compose and is refreshed on every Pi update; Uptime Kuma is pinned (manual).
    item.update(pending=n, state='pending' if n else 'current',
                detail=(f'{n} package{"s" if n != 1 else ""}' if n else 'Up to date') + ' · Uptime Kuma pinned (update by hand)')


def check_docker(items):
    unraid(f'php {DOCKER_SCRIPTS}/dockerupdate check nonotify', timeout=600)
    code, raw = unraid(f'cat {UPDATE_STATUS}')
    try:
        status = json.loads(raw) if code == 0 else {}
    except ValueError:
        status = {}
    for item in items:
        if item['kind'] != 'Docker' or item['state'] == 'skipped':
            continue
        pending = docker_pending(status, item['image'])
        if pending is None:
            item.update(state='unknown', detail='Unraid does not track this image; update it by hand', pending=None)
        else:
            item.update(pending=int(pending), state='pending' if pending else 'current',
                        detail='New image available' if pending else 'Up to date')


def run_check(run):
    items = inventory()
    run.state['items'] = items
    run.save()
    for item in items:
        if item['kind'] in ('LXC', 'Pi') and item['state'] != 'skipped':
            item['state'] = 'checking'
            run.save()
            (check_pi if item['kind'] == 'Pi' else check_lxc)(item)
            run.log(f'{item["id"]}: {item["detail"]}')
            run.save()
    check_docker(items)
    run.state['checked_at'] = int(time.time())


# ---------- gates ----------

def busy_reasons():
    reasons = []
    if sh(['pgrep', '-x', 'vzdump'])[0] == 0:
        reasons.append('a Proxmox backup is running')
    code, out = unraid("grep -E '^mdResyncPos=' /proc/mdstat; pgrep -f '[/]usr/local/sbin/mover' >/dev/null && echo mover; "
                       f"docker inspect -f '{{{{.State.Running}}}}' {QWEN_CONTAINER} 2>/dev/null")
    if code == 255:
        reasons.append('Unraid is unreachable')
    else:
        pos = re.search(r'mdResyncPos=(\d+)', out)
        if pos and int(pos.group(1)) > 0:
            reasons.append('an Unraid parity operation is running')
        if 'mover' in out.split():
            reasons.append('the Unraid mover is running')
        if 'true' in out.split():
            reasons.append('Qwen is running')
    return reasons


# ---------- updates ----------

def failed_units(vmid):
    code, out = guest(vmid, 'systemctl --failed --plain --no-legend 2>/dev/null | wc -l')
    return int(out.split()[-1]) if code == 0 and out.split() and out.split()[-1].isdigit() else None


def update_lxc(run, item):
    vmid = item['vmid']
    before = failed_units(vmid)
    snap = snapshot_name(time.time())
    item.update(state='snapshot', detail='Taking snapshot')
    run.save()
    sh(['pct', 'snapshot', str(vmid), snap, '--description', 'homelab-update pre-update'], timeout=300, check=True)
    item['snapshot'] = snap
    run.log(f'{item["id"]}: snapshot {snap}')
    item.update(state='updating', detail=f'Upgrading {item.get("pending") or ""} packages'.replace('  ', ' '))
    run.save()
    code, out = guest(vmid, f'{APT_ENV} apt-get update -qq && {APT_ENV} {APT_UPGRADE}', timeout=1800)
    run.log(f'{item["id"]}: apt exit {code}\n{out[-6000:]}')
    if code:
        raise RuntimeError(f'apt-get upgrade exited {code}')
    item.update(state='verifying', detail='Checking services')
    run.save()
    _, status = sh(['pct', 'status', str(vmid)])
    after = failed_units(vmid)
    if 'running' not in status:
        raise RuntimeError('container is not running after upgrade')
    if after is None or (before is not None and after > before):
        raise RuntimeError(f'failed services went from {before} to {after}')
    reboot = guest(vmid, 'test -f /var/run/reboot-required')[0] == 0
    _, listing = sh(['pct', 'listsnapshot', str(vmid)])
    for old in old_snapshots(listing, snap):
        sh(['pct', 'delsnapshot', str(vmid), old], timeout=300)
        run.log(f'{item["id"]}: removed older snapshot {old}')
    item.update(state='done', pending=0, reboot_required=reboot,
                detail='Updated' + (' · reboot needed' if reboot else ''))


def rollback_names(name):
    slug = re.sub(r'[^a-z0-9_.-]', '-', name.lower())
    return f'homelab-rollback-{slug}', f'homelab-rollback/{slug}:pre-update'


def pi_failed_units():
    code, out = pi('systemctl --failed --plain --no-legend 2>/dev/null | wc -l')
    return int(out.split()[-1]) if code == 0 and out.split() and out.split()[-1].isdigit() else None


def update_pi(run, item):
    # The Pi is the DNS replica: never update it while the primary Pi-hole is down.
    if sh(['dig', '+time=3', '+tries=2', '+short', '@192.168.0.11', 'google.com'], timeout=20)[0] != 0:
        raise RuntimeError('primary Pi-hole (.11) is not answering DNS; not touching the replica')
    before = pi_failed_units()
    stamp = time.strftime('%Y%m%d%H%M')
    item.update(state='snapshot', detail='Backing up Uptime Kuma data')
    run.save()
    pi(f'tar -czf /opt/uptime-kuma-backup-{stamp}.tgz -C /opt uptime-kuma && ls -1t /opt/uptime-kuma-backup-*.tgz | tail -n +4 | xargs -r rm -f', timeout=120, check=True)
    item['snapshot'] = f'/opt/uptime-kuma-backup-{stamp}.tgz'
    item.update(state='updating', detail='Upgrading packages and nebula-sync')
    run.save()
    code, out = pi(f'{APT_ENV} apt-get update -qq && {APT_ENV} {APT_UPGRADE} && cd /opt/nebula-sync && docker compose pull -q && docker compose up -d', timeout=1800)
    run.log(f'{item["id"]}: exit {code}\n{out[-6000:]}')
    if code:
        raise RuntimeError(f'upgrade exited {code}')
    item.update(state='verifying', detail='Checking DNS and containers')
    run.save()
    time.sleep(5)
    if pi('dig +time=3 +tries=2 +short @127.0.0.1 google.com | grep -q .')[0] != 0:
        raise RuntimeError('Pi-hole on the Pi is not answering DNS after the upgrade')
    _, states = pi("docker ps -a --format '{{.Names}} {{.State}}'")
    stopped = [l.split()[0] for l in states.splitlines() if l.split() and l.split()[-1] != 'running']
    if stopped:
        raise RuntimeError('containers not running: ' + ', '.join(stopped))
    after = pi_failed_units()
    if after is None or (before is not None and after > before):
        raise RuntimeError(f'failed services went from {before} to {after}')
    reboot = pi('test -f /var/run/reboot-required')[0] == 0
    item.update(state='done', pending=0, reboot_required=reboot, detail='Updated' + (' · reboot needed' if reboot else ''))


def update_docker(run, item):
    name = shlex.quote(item['name'])
    _, old = unraid(f"docker inspect -f '{{{{.Image}}}}' {name}", check=True)
    old = old.strip()
    _, tag = rollback_names(item['name'])
    unraid(f'docker tag {shlex.quote(old)} {shlex.quote(tag)}', check=True)
    # update_container records the image ID *before* its own pull and deletes that ID afterwards
    # (forced, so tags and stopped containers don't protect it). Pulling first makes its "old" ID the
    # new image, so nothing is deleted and the previous image survives under the rollback tag.
    code, out = unraid(f'docker pull {shlex.quote(item["image"])}', timeout=1800)
    if code:
        raise RuntimeError('docker pull failed: ' + out[-200:])
    item.update(state='updating', detail='Pulling and recreating', rollback_image=tag)
    run.save()
    # update_container is what the Unraid Update button runs: pull, then recreate from the template.
    code, out = unraid(f'{DOCKER_SCRIPTS}/update_container {name}', timeout=1800)
    run.log(f'{item["id"]}: update_container exit {code}\n{re.sub(r"<[^>]+>", " ", out)[-4000:]}')
    if unraid(f'docker image inspect {shlex.quote(tag)} >/dev/null')[0]:
        run.log(f'{item["id"]}: rollback image {tag} missing after update')
        tag = None
    item.update(state='verifying', detail='Waiting for container')
    run.save()
    deadline = time.time() + 180
    while True:
        _, info = unraid(f"docker inspect -f '{{{{.State.Running}}}}|{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{end}}}}|{{{{.Image}}}}' {name}")
        running, health, image = (info.strip().split('|') + ['', '', ''])[:3]
        if running == 'true' and health in ('', 'healthy'):
            break
        if health == 'unhealthy' or time.time() > deadline:
            raise RuntimeError(f'container not healthy (running={running or "?"}, health={health or "none"})')
        time.sleep(5)
    if not image or image == old:
        raise RuntimeError('image did not change; the pull may have failed')
    item.update(state='done', pending=0, detail='Updated · previous image kept' if tag else 'Updated · previous image NOT kept', rollback_image=tag)


def run_update(run, weekly=False):
    reasons = busy_reasons()
    if reasons:
        raise Refused('Not started: ' + '; '.join(reasons) + '.')
    run_check(run)
    if weekly:
        for i in run.state['items']:
            if i['name'] in WEEKLY_EXCLUDE and i['state'] == 'pending':
                i.update(state='skipped', detail='Excluded from automatic updates; update by hand')
        run.save()
    items = run.state['items']
    queue = [i for i in items if i['state'] == 'pending']
    for i in queue:
        i['state'] = 'queued'
    run.save()
    for n, item in enumerate(queue):
        reasons = busy_reasons()
        if reasons:
            raise Refused('Paused before ' + item['name'] + ': ' + '; '.join(reasons) + '.')
        try:
            {'LXC': update_lxc, 'Pi': update_pi}.get(item['kind'], update_docker)(run, item)
            run.log(f'{item["id"]}: {item["detail"]}')
        except Exception as exc:
            hint = (f'Kuma data backed up at {item["snapshot"]} on the Pi' if item.get('kind') == 'Pi' and item.get('snapshot')
                    else f'Roll back: pct rollback {item["vmid"]} {item["snapshot"]}' if item.get('snapshot')
                    else (f'Previous image kept as {item["rollback_image"]}. Roll back: Unraid → Docker → '
                          f'{item["name"]} → Edit → Repository = {item["rollback_image"]} → Apply')
                    if item.get('rollback_image') else '')
            item.update(state='failed', detail=str(exc)[:300], rollback=hint)
            for rest in queue[n + 1:]:
                rest.update(state='not started', detail='Stopped after a failure')
            run.save()
            raise
        run.save()


def cloud_backup_busy():
    try:
        with open('/run/homelab-cloud-backup.lock', 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except OSError:
        return True


def ntfy(title, body, priority=2):
    m = re.search(r'^TOPIC="([^"]+)"', WATCHDOG.read_text(), re.M)
    if not m:
        return
    import urllib.request
    req = urllib.request.Request('https://ntfy.sh/' + m.group(1), data=body.encode(), method='POST',
                                 headers={'Title': title, 'Priority': str(priority), 'Tags': 'arrows_counterclockwise'})
    urllib.request.urlopen(req, timeout=15).read()


def weekly_summary(state):
    items = state.get('items') or []
    done = [i['name'] for i in items if i.get('state') == 'done']
    failed = [i['name'] for i in items if i.get('state') == 'failed']
    excluded = [i['name'] for i in items if 'Excluded from automatic' in (i.get('detail') or '')]
    lines = [f"Updated: {', '.join(done) if done else 'nothing needed updating'}"]
    if failed:
        lines.append(f"Failed: {', '.join(failed)} (alert sent separately; later items were not started)")
    if excluded:
        lines.append(f"Left for you: {', '.join(excluded)}")
    if Path('/var/run/reboot-required').exists():
        lines.append('Proxmox has a pending reboot for a new kernel (not rebooted automatically).')
    return ('Weekly updates done' if not failed else 'Weekly updates stopped after a failure'), '\n'.join(lines)


def run_weekly():
    if cloud_backup_busy():
        ntfy('Weekly updates skipped', 'The cloud backup or restore test was running. Next try: next Saturday.')
        return
    reasons = busy_reasons()
    if reasons:
        ntfy('Weekly updates skipped', 'Not started: ' + '; '.join(reasons) + '.')
        return
    with open(LOCK, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            ntfy('Weekly updates skipped', 'An update job was already running.')
            return
        run = Run('update')
        try:
            run_update(run, weekly=True)
            run.finish('complete')
        except Refused as exc:
            run.finish('refused', str(exc))
        except Exception as exc:
            run.log(f'error: {exc}')
            run.finish('failed', str(exc)[:300])
    title, body = weekly_summary(read_state())
    ntfy(title, body)


class Refused(Exception):
    pass


# ---------- entry points ----------

def spawn(kind):
    state = read_state()
    if state.get('job') and state['job'].get('status') == 'running' and locked():
        return dict(ok=False, error='An update job is already running.')
    unit = f'homelab-update-{kind}-{int(time.time())}'
    code, out = sh(['systemd-run', '--unit', unit, '--collect', '--quiet',
                    '/usr/local/sbin/homelab-update', f'run-{kind}'])
    return dict(ok=code == 0, error=None if code == 0 else 'Could not start the job.')


def locked():
    try:
        with open(LOCK, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except OSError:
        return True


def run_job(kind):
    with open(LOCK, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        run = Run(kind)
        try:
            (run_check if kind == 'check' else run_update)(run)
            run.finish('complete')
        except Refused as exc:
            run.finish('refused', str(exc))
        except Exception as exc:
            run.log(f'error: {exc}')
            run.finish('failed', str(exc)[:300])


def backup_progress(state):
    """Percent, bytes and ETA for the running upload: restic's own JSON status when available,
    otherwise bytes read by the restic process against the size of its sources (first runs only)."""
    try:
        j = json.loads((state / 'progress.json').read_text())
        if time.time() - (state / 'progress.json').stat().st_mtime < 120:
            phase = (state / 'phase').read_text().strip() if (state / 'phase').exists() else None
            return dict(source='restic', phase=phase, percent=round(100 * (j.get('percent_done') or 0), 1),
                        bytes_done=j.get('bytes_done'), bytes_total=j.get('total_bytes'), eta_s=j.get('seconds_remaining'))
    except (OSError, ValueError):
        pass
    code, pid = sh(['pgrep', '-f', '^restic backup'], timeout=5)
    if code or not pid.split():
        return None
    pid = pid.split()[0]
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except OSError:
        return None
    tag = args[args.index(b'--tag') + 1].decode() if b'--tag' in args else None
    sources = [a.decode() for a in args if a.startswith(b'/mnt/')]
    if not sources:
        return dict(source='estimate', phase=tag)
    # Total = size of what is being backed up (cached 6 h); done = bytes already stored on Drive (sampled every 3 min).
    cache = state / f'total-{tag}.json'
    try:
        total = json.loads(cache.read_text())
        if total.get('sources') != sources or time.time() - total['at'] > 6 * 3600:
            raise ValueError
    except (OSError, ValueError, KeyError):
        code, out = sh(['timeout', '120', 'du', '-sbc', *sources], timeout=130)
        last = out.strip().splitlines()[-1].split() if out.strip() else []
        if not last or not last[0].isdigit():
            return dict(source='estimate', phase=tag)
        total = dict(sources=sources, bytes=int(last[0]), at=time.time())
        cache.write_text(json.dumps(total))
    sample = state / 'repo-size.json'
    try:
        samples = json.loads(sample.read_text())
    except (OSError, ValueError):
        samples = []
    now = time.time()
    if not samples or now - samples[-1]['t'] > 180:
        code, out = sh(['timeout', '60', 'rclone', 'size', 'gdrive:homelab-restic', '--json'], timeout=70)
        try:
            samples = (samples + [dict(t=now, bytes=json.loads(out)['bytes'])])[-12:]
            sample.write_text(json.dumps(samples))
        except (ValueError, KeyError):
            pass
    if not samples:
        return dict(source='estimate', phase=tag)
    done = samples[-1]['bytes']; eta = None
    old = [x for x in samples if samples[-1]['t'] - x['t'] >= 300]
    if old:
        rate = (done - old[-1]['bytes']) / (samples[-1]['t'] - old[-1]['t'])
        if rate > 0:
            eta = int(max(0, total['bytes'] - done) / rate)
    return dict(source='estimate', phase=tag, percent=min(99.9, round(100 * done / total['bytes'], 1)),
                bytes_done=min(done, total['bytes']), bytes_total=total['bytes'], eta_s=eta)


def cloud_backup_status():
    """Off-site restic backup to Google Drive (homelab-cloud-backup.service)."""
    state = Path('/var/lib/homelab-cloud-backup')
    try:
        last = int((state / 'last_success').read_text().strip())
    except (OSError, ValueError):
        last = None
    _, active = sh(['systemctl', 'is-active', 'homelab-cloud-backup.service'], timeout=10)
    _, nxt = sh(['systemctl', 'show', 'homelab-cloud-backup.timer', '-p', 'NextElapseUSecRealtime', '--value'], timeout=10)
    failed = None
    try:
        lines = [l for l in (state / 'run.log').read_text(errors='replace').splitlines() if l.strip()]
        starts = [i for i, l in enumerate(lines) if l.startswith('=== ') and l.endswith(' start')]
        if starts and not any(l.endswith(' done') for l in lines[starts[-1]:]) and active.strip() != 'activating':
            failed = lines[starts[-1]][4:29]
    except OSError:
        pass
    running = active.strip() in ('activating', 'active')
    progress = backup_progress(state) if running else None
    try:  # newest snapshot per tag, cached every 30 min by homelab-alerts; includes Unraid's own job
        snaps = json.loads((state / 'snapshots.json').read_text())
        sources = [dict(tag=t, label=l, last=snaps['latest'].get(t), stale_h=h)
                   for t, l, h in (('photos', 'Photos', 36), ('proxmox', 'Proxmox', 36), ('unraid', 'Unraid', 30), ('configs', 'Configs', 36))]
        checked = snaps['checked_at']
    except (OSError, ValueError, KeyError):
        sources, checked = None, None
    try:
        restore = json.loads((state / 'restore-test.json').read_text())
    except (OSError, ValueError):
        restore = None
    try:
        configs_local = int(Path('/var/backups/homelab-configs/last_success').read_text().strip())
    except (OSError, ValueError):
        configs_local = None
    _, rnext = sh(['systemctl', 'show', 'homelab-restore-test.timer', '-p', 'NextElapseUSecRealtime', '--value'], timeout=10)
    return dict(last_success=last, running=running, progress=progress, next_run=nxt.strip() or None,
                restore_test=restore, restore_next=rnext.strip() or None, configs_local=configs_local,
                last_failed_start=failed, destination='Google Drive (restic, encrypted)',
                scope='Photos, Proxmox backups, Unraid appdata + flash, router + host configs', sources=sources, sources_checked_at=checked)


def health():
    now = int(time.time())
    out = dict(observed_at=now, guests=[], containers=None, updates=update_summary(read_state()))
    code, text = sh(['pct', 'list'], timeout=30)
    for g in parse_pct_list(text) if code == 0 else []:
        entry = dict(vmid=g['vmid'], name=g['name'], status=g['status'])
        if g['status'] == 'running':
            rc, probe = guest(g['vmid'], HEALTH_SH, timeout=45)
            entry.update(parse_guest_health(probe) if rc == 0 else dict(error='Probe failed'))
        out['guests'].append(entry)
    if code:
        out['guests'] = None
    rc, probe = pi(HEALTH_SH + PI_EXTRA_SH, timeout=45)
    if rc == 0:
        out['pi'] = dict(name='orangepi', ip='192.168.0.10', online=True, **parse_guest_health(probe), **parse_pi_extra(probe))
    else:
        out['pi'] = dict(name='orangepi', ip='192.168.0.10', online=False, error='Probe failed')
    out['cloud_backup'] = cloud_backup_status()
    rc, text = unraid("docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}'", timeout=20)
    if rc == 0:
        out['containers'] = [dict(zip(('name', 'state', 'status'), (line.split('|') + ['', ''])[:3]))
                             for line in text.splitlines() if line]
    rc, text = unraid('/usr/bin/python3 /mnt/disk1/.homelab-smart/homelab_smart.py status', timeout=60)
    try:
        out['smart'] = json.loads(text) if rc == 0 else None
    except ValueError:
        out['smart'] = None
    return out


# ---------- what changed (timeline) ----------

EVENT_DAYS = 7
TASK_KINDS = {  # Proxmox task type -> (timeline kind, verb)
    'vzdump': ('backup', 'Local backup'), 'vzstart': ('power', 'Started'), 'qmstart': ('power', 'Started'),
    'vzshutdown': ('power', 'Shut down'), 'qmshutdown': ('power', 'Shut down'), 'vzstop': ('power', 'Stopped'),
    'qmstop': ('power', 'Stopped'), 'vzreboot': ('power', 'Rebooted'), 'qmreboot': ('power', 'Rebooted'),
    'vzcreate': ('change', 'Created'), 'qmcreate': ('change', 'Created'), 'vzdestroy': ('change', 'Removed'),
    'qmdestroy': ('change', 'Removed'), 'vzrestore': ('change', 'Restored from backup'), 'qmrestore': ('change', 'Restored from backup'),
    'resize': ('change', 'Disk resized'),
}


def _iso(text):
    import datetime
    try:
        return datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _stamp(name, fmt):
    import calendar
    try:
        return calendar.timegm(time.strptime(name, fmt))
    except ValueError:
        return None


def events(days=EVENT_DAYS, now=None):
    """Recent changes from records that already exist. Correlation only: nothing here claims a cause."""
    now = now or time.time(); since = now - days * 86400; out = []

    def add(ts, kind, title, detail='', ok=True, source=''):
        if ts and since <= ts <= now + 300:
            out.append(dict(ts=int(ts), kind=kind, title=title, detail=detail, ok=ok, source=source))

    names = {}
    for cmd in (['pct', 'list'], ['qm', 'list']):
        code, text = sh(cmd, timeout=20)
        for line in text.splitlines()[1:] if code == 0 else []:
            parts = line.split()
            if parts and parts[0].isdigit():
                names[parts[0]] = parts[-1] if cmd[0] == 'pct' else parts[1]
    code, text = sh(['pvesh', 'get', f'/nodes/{socket.gethostname()}/tasks', '--limit', '500', '--since', str(int(since)),
                     '--output-format', 'json'], timeout=30)
    try:
        tasks = json.loads(text) if code == 0 else []
    except ValueError:
        tasks = []
    for t in tasks:
        kind = TASK_KINDS.get(t.get('type'))
        if not kind:
            continue
        gid = str(t.get('id') or '')
        who = f"{names.get(gid, 'guest')} ({gid})" if gid else 'all scheduled guests'
        status = t.get('status') or 'running'
        add(t.get('endtime') or t.get('starttime'), kind[0], f'{kind[1]}: {who}', '' if status == 'OK' else status,
            ok=status == 'OK' or None if status == 'running' else status == 'OK', source='Proxmox tasks')

    for log in sorted((STATE_DIR / 'logs').glob('*-update.log')):
        ts = _stamp(log.name[:15], '%Y%m%dT%H%M%S') or log.stat().st_mtime
        ts = ts - time.localtime(ts).tm_gmtoff if ts else ts  # log names are local time
        done, failed = [], []
        for line in log.read_text(errors='replace').splitlines():
            m = re.match(r'\[[\d:]+\] (\S+): (Updated|Failed.*|Rolled back.*)$', line)
            if m:
                (done if m[2] == 'Updated' else failed).append(m[1] + ('' if m[2] == 'Updated' else f' ({m[2]})'))
        if done or failed:
            add(log.stat().st_mtime, 'update', f"Updates applied to {len(done)} item{'s' if len(done) != 1 else ''}" + (f', {len(failed)} failed' if failed else ''),
                ', '.join(done + failed)[:400], ok=not failed, source='homelab-update')

    cb = Path('/var/lib/homelab-cloud-backup')
    try:
        lines = [l for l in (cb / 'run.log').read_text(errors='replace').splitlines() if l.startswith('=== ')]
    except OSError:
        lines = []
    for i, l in enumerate(lines):
        if l.endswith(' start'):
            started = _iso(l[4:-6]); nxt = lines[i + 1] if i + 1 < len(lines) else ''
            if nxt.endswith(' done'):
                add(_iso(nxt[4:-5]), 'backup', 'Cloud backup finished (photos, Proxmox, configs)', '', True, 'homelab-cloud-backup')
            elif i + 1 < len(lines):
                add(started, 'backup', 'Cloud backup did not finish', 'See run.log on Proxmox', False, 'homelab-cloud-backup')
    try:
        for line in (cb / 'restore-tests.jsonl').read_text().splitlines():
            r = json.loads(line)
            add(r.get('finished_at'), 'backup', 'Off-site restore test ' + ('passed' if r.get('ok') else 'failed' if r.get('ok') is False else 'incomplete'),
                '; '.join(f"{c.get('label')}: {c.get('detail')}" for c in r.get('checks', []))[:400], r.get('ok'), 'homelab-restore-test')
    except (OSError, ValueError):
        pass
    try:
        snaps = json.loads((cb / 'snapshots.json').read_text()).get('recent', {})
        for ts in snaps.get('unraid', []):
            add(ts, 'backup', 'Unraid cloud backup (appdata + flash)', '', True, 'restic snapshots')
    except (OSError, ValueError):
        pass

    code, text = sh(['journalctl', '-u', 'homelab-config-backup.service', '--since', f'@{int(since)}', '-o', 'json', '--no-pager'], timeout=20)
    for line in text.splitlines() if code == 0 else []:
        try:
            j = json.loads(line); msg = j.get('MESSAGE', '')
        except ValueError:
            continue
        if isinstance(msg, str) and (msg.startswith('Finished ') or 'Failed with result' in msg):
            add(int(j['__REALTIME_TIMESTAMP']) / 1e6, 'backup', 'Config backup ' + ('saved' if msg.startswith('Finished') else 'failed'),
                'router, Proxmox host, Orange Pi', msg.startswith('Finished'), 'homelab-config-backup')
    code, text = sh(['journalctl', '-t', 'unraid-mount', '--since', f'@{int(since)}', '-o', 'json', '--no-pager'], timeout=20)
    for line in text.splitlines() if code == 0 else []:
        try:
            j = json.loads(line)
        except ValueError:
            continue
        add(int(j['__REALTIME_TIMESTAMP']) / 1e6, 'repair', 'Proxmox backup share remounted', str(j.get('MESSAGE', ''))[:200], True, 'fix-unraid-mount')
    for vmid, name in names.items():
        code, text = sh(['pct', 'exec', vmid, '--', 'journalctl', '-t', 'mount-heal', '--since', f'@{int(since)}', '-o', 'json', '--no-pager'], timeout=20)
        for line in text.splitlines() if code == 0 else []:
            try:
                j = json.loads(line); msg = str(j.get('MESSAGE', ''))
            except ValueError:
                continue
            add(int(j['__REALTIME_TIMESTAMP']) / 1e6, 'repair', f'Share repaired in {name} ({vmid})', msg[:200], 'failed' not in msg, 'mount-heal')

    code, text = sh(['pct', 'exec', '104', '--', 'ls', '/opt/homelab-dashboard/backups'], timeout=20)
    for n in text.split() if code == 0 else []:
        add(_stamp(n[:15], '%Y%m%dT%H%M%S'), 'deploy', 'Homepage deployed', '', True, 'deploy.py')
    try:
        for line in (Path('/var/lib/homelab-alerts/events.jsonl')).read_text().splitlines()[-500:]:
            e = json.loads(line); add(e.get('ts'), 'alert', e.get('title', ''), e.get('detail', ''), e.get('ok'), 'homelab-alerts')
    except (OSError, ValueError):
        pass

    code, text = unraid(f"""ls /mnt/disk1/.homelab-nightly/backups 2>/dev/null | sed 's/^/NIGHTLY /'
cat /mnt/disk1/.homelab-smart/schedule.log 2>/dev/null | sed 's/^/SMART /'
docker ps -a --format 'DOCKER {{{{.Names}}}}|{{{{.Image}}}}|{{{{.CreatedAt}}}}'""", timeout=30)
    for line in text.splitlines() if code == 0 else []:
        tag, _, rest = line.partition(' ')
        if tag == 'NIGHTLY':
            add(_stamp(rest[:15], '%Y%m%dT%H%M%S'), 'deploy', 'Ask Qwen / nightly review updated on Unraid', '', True, 'nightly installers')
        elif tag == 'SMART':
            ts = time.mktime(time.strptime(rest[:16], '%Y-%m-%d %H:%M')) if re.match(r'\d{4}-\d\d-\d\d \d\d:\d\d', rest) else None
            add(ts, 'disk', 'Drive self-tests started', rest[17:], 'FAILED' not in rest, 'homelab-smart')
        elif tag == 'DOCKER':
            name, image, created = (rest.split('|') + ['', ''])[:3]
            m = re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([+-]\d{4})', created)
            if m:
                import datetime
                ts = datetime.datetime.strptime(' '.join(m.groups()), '%Y-%m-%d %H:%M:%S %z').timestamp()
                add(ts, 'update', f'Container recreated: {name}', image.split('@')[0][:120], True, 'Unraid Docker')
    out.sort(key=lambda e: e['ts'], reverse=True)
    return out[:400]


def status():
    state = read_state()
    job = state.get('job')
    if job and job.get('status') == 'running' and not locked():
        job.update(status='failed', error='The job stopped unexpectedly.')
    return state


def main(argv):
    command = argv[1] if len(argv) > 1 else ''
    if command == 'ssh':
        command = os.environ.get('SSH_ORIGINAL_COMMAND', '').strip()
        if command not in ('status', 'health', 'check', 'start', 'events'):
            print(json.dumps(dict(ok=False, error='Unsupported command.')))
            return 2
    if command == 'status':
        print(json.dumps(status()))
    elif command == 'health':
        print(json.dumps(health()))
    elif command == 'events':
        print(json.dumps(dict(observed_at=int(time.time()), days=EVENT_DAYS, events=events())))
    elif command in ('check', 'start'):
        print(json.dumps(spawn('check' if command == 'check' else 'update')))
    elif command in ('run-check', 'run-update'):
        run_job(command[4:])
    elif command == 'run-weekly':
        run_weekly()
    elif command == 'weekly-dry-run':
        print(json.dumps(dict(cloud_backup_busy=cloud_backup_busy(), busy=busy_reasons(), exclude=sorted(WEEKLY_EXCLUDE))))
    else:
        print('usage: homelab-update status|health|check|start', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
