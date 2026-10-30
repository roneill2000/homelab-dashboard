#!/usr/bin/env python3
"""Push ntfy alerts for things the existing watchdog, Uptime Kuma and NetAlertX don't cover.

Runs on Proxmox every 5 minutes (homelab-alerts.timer). Reads Homepage's APIs, compares with the
previous run, and sends one alert when a check goes bad and one when it recovers. A check must be
bad on two consecutive runs before alerting, except failed update runs, which alert immediately.
The ntfy topic is read from the existing watchdog so it lives in one place.

Also: `--digest` (homelab-digest.timer, 07:00) sends one morning summary with a short Ask Qwen note, and
`--serve-why` (homelab-why.service, :8997) answers the "Ask Qwen why" button on alerts by asking Ask Qwen about
that alert and pushing the answer back to ntfy.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = 'http://192.168.0.2'
WATCHDOG = Path('/usr/local/bin/homelab-health-watchdog.sh')
STATE = Path('/var/lib/homelab-alerts/state.json')
INTENTIONALLY_STOPPED = {'Transcoding-GPU-Benchmark'}
CONSECUTIVE = 2
SNAPSHOTS = Path('/var/lib/homelab-cloud-backup/snapshots.json')
SOURCES = {'photos': ('Photos', 36), 'proxmox': ('Proxmox', 36), 'unraid': ('Unraid', 30), 'configs': ('Configs', 36)}  # tag: (label, stale after h)
DETAILS = Path('/var/lib/homelab-alerts/details.json')  # last title/detail per alert key, for the why button and digest
CONFIG_BACKUP = Path('/var/backups/homelab-configs/last_success')
EVENTS = Path('/var/lib/homelab-alerts/events.jsonl')  # sent alerts, read by the runner's "what changed" timeline
RESTORE_TEST = Path('/var/lib/homelab-cloud-backup/restore-test.json')
NIGHTLY_HISTORY = Path('/var/lib/homelab-alerts/nightly-history.json')  # local_date -> final status of the 03:00 review
WHY_URL = 'http://192.168.0.3:8997/why'
QWEN_URL = HOME + '/api/qwen'
PC = '192.168.0.12'
GAME_FLAG = r'C:\llm\service\paused-for-game.txt'  # written by game-watch.ps1 while it stops llama-server for a game


def fetch(path, timeout=60):
    with urllib.request.urlopen(HOME + path, timeout=timeout) as r:
        return json.load(r)


def checks(t, s, f, now):
    """Return {key: (bad, title, detail, priority)} for every check that has data. Missing data is not a bad signal."""
    out = {}
    if t is not None:
        age = now - (t.get('updated_at') or 0)
        out['homepage-stale'] = (age > 300, 'Homepage data is stale', f'Telemetry last updated {int(age // 60)} min ago.', 4)
        # Not checked here, because another sender already alerts on them (see alerts/SENDERS.md):
        # Unraid disk usage 70/90% and temperatures (Unraid ntfy agent), drive failures (Scrutiny),
        # Proxmox backup results (Proxmox notification webhook).
        st = t.get('speedtest') or {}
        tested = st.get('tested_at')
        if tested:
            try:
                ts = time.mktime(time.strptime(tested, '%Y-%m-%d %H:%M:%S'))  # Speedtest Tracker local time
                hours = (now - ts) / 3600
                out['speedtest-stale'] = (hours > 12, 'Speed tests failing', f'Last completed test was {int(hours)} h ago.', 3)
            except ValueError:
                pass
    if f is not None:
        guests = f.get('guests') or []
        for g in guests:
            for m in g.get('mounts') or []:
                out[f"mount:{g['name']}:{m['path']}"] = (m['state'] != 'ok', f"Share {m['state']} in {g['name']}", f"{m['path']} in LXC {g['vmid']} is {m['state']}.", 4)
            units = g.get('failed_units')
            if units is not None:
                out[f"units:{g['name']}"] = (bool(units), f"Failed services in {g['name']}", f"LXC {g['vmid']}: " + (', '.join(units) if units else 'none'), 3)
        for c in f.get('containers') or []:
            if c['name'] in INTENTIONALLY_STOPPED:
                continue
            out[f"docker:{c['name']}"] = (c['state'] != 'running', f"Docker app {c['name']} stopped", f"{c['name']}: {c.get('status') or c['state']}", 4)
        pi = f.get('pi')
        if pi is not None:
            # Pi unreachable / Pi-hole replica down: already alerted by homelab-health-watchdog ('Secondary Pi-hole (Orange Pi)').
            if pi.get('online'):
                if pi.get('failed_units') is not None:
                    out['pi-units'] = (bool(pi['failed_units']), 'Failed services on the Orange Pi', ', '.join(pi['failed_units']) or 'none', 3)
                for c in pi.get('containers') or []:
                    out[f"pi-docker:{c['name']}"] = (c['state'] != 'running', f"{c['name']} stopped on the Orange Pi", c.get('status') or c['state'], 4)
                if isinstance(pi.get('disk_pct'), (int, float)):
                    out['pi-disk'] = (pi['disk_pct'] >= 85, 'Orange Pi SD card filling', f"{pi['disk_pct']}% used.", 3)
                if isinstance(pi.get('temp_c'), (int, float)):
                    out['pi-temp'] = (pi['temp_c'] >= 75, 'Orange Pi running hot', f"{pi['temp_c']} °C.", 3)
        upd = f.get('updates') or {}
        failed = upd.get('failed') or []
        out['updates-failed'] = (bool(failed), 'Update run failed',
                                 '; '.join(f"{x['name']}: {x.get('detail')}" + (f" ({x['rollback']})" if x.get('rollback') else '') for x in failed) or 'No failed updates.', 5)
        out.update(smart_checks(f.get('smart'), now))
    return out


def smart_checks(smart, now):
    """Unraid self-test results and schedule (smart/homelab_smart.py). Attribute health is Scrutiny's job."""
    out = {}
    if not smart:
        return out
    settled = now - (smart.get('scheduler_installed_at') or now) > 3 * 86400  # give the scheduler time to catch up
    for d in smart.get('disks') or []:
        name = f"{d.get('role')} ({d.get('device')})"
        kind, every = ('extended', smart.get('extended_every_days', 90)) if d.get('rotational') else ('short', smart.get('short_every_days', 30))
        launch = (smart.get('launches') or {}).get(d.get('device'))
        if launch:  # an explicit failed launch is actionable; a missing reading stays unknown
            out[f"smart-launch:{d.get('device')}"] = (not launch.get('ok'), f'Drive self-test did not start on {name}',
                                                     f"{kind} test launch at {time.strftime('%d %b %H:%M', time.localtime(launch.get('at') or 0))}: {launch.get('message') or 'smartctl did not confirm the start'}", 3)
        t = d.get(f'last_{kind}')
        if not t:
            if settled and d.get('running_remaining_pct') is None:
                out[f"smart-overdue:{d.get('device')}"] = (True, f'Drive self-tests not running on {name}', f'No {kind} test is recorded for this disk.', 3)
            continue
        result = t.get('result') or ''
        ok = result.startswith('Completed without error') or 'progress' in result.lower() or 'aborted by host' in result.lower()
        out[f"smart-test:{d.get('device')}"] = (not ok, f'Drive self-test failed on {name}', f"{d.get('model')}: {kind} test {result} at {t.get('at_hours')} h.", 4)
        if settled and t.get('hours_ago') is not None and d.get('running_remaining_pct') is None:
            out[f"smart-overdue:{d.get('device')}"] = (t['hours_ago'] > (every + 30) * 24, f'Drive self-tests not running on {name}',
                                                      f"Last {kind} test was {t['hours_ago'] // 24} days ago; the scheduler should run one every {every} days.", 3)
    return out


def config_backup_check(now, path=CONFIG_BACKUP):
    """Nightly router / Proxmox / Orange Pi config backup (homelab-config-backup, 00:45)."""
    try:
        last = int(path.read_text().strip())
    except (OSError, ValueError):
        return {}
    hours = (now - last) / 3600
    return {'config-backup': (hours > 36, 'Config backup failing', f'Router / Proxmox / Orange Pi config backup last succeeded {int(hours)} h ago.', 3)}


def cloud_backup_check(now, path=Path('/var/lib/homelab-cloud-backup/last_success')):
    """Off-site backup freshness. Silent until the first successful run exists."""
    try:
        last = int(path.read_text().strip())
    except (OSError, ValueError):
        return {}
    hours = (now - last) / 3600
    return {'cloud-backup': (hours > 36, 'Cloud backup is stale', f'Last successful Google Drive backup was {int(hours)} h ago.', 4)}


STALE_H = {'photos': 36, 'proxmox': 36, 'unraid': 30, 'configs': 36}


def snapshots_due(cache, now, every=1800, last_success=None):
    """Re-read the repository every 30 min, right after the Proxmox job succeeds, and every run (5 min) while a
    source looks stale, so a fresh upload shows within minutes instead of up to half an hour."""
    if not cache or now - cache.get('checked_at', 0) >= every:
        return True
    if last_success and last_success > cache['checked_at']:
        return True
    latest = cache.get('latest') or {}
    return any(now - latest.get(tag, 0) > h * 3600 for tag, h in STALE_H.items())


def refresh_snapshots(now, path=SNAPSHOTS, every=1800):
    """Cache the newest Google Drive snapshot per tag. Proxmox and Unraid each run their own job into the
    same repository, so the repository is the one place that sees both."""
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = None
    try:
        last = int((path.parent / 'last_success').read_text().strip())
    except (OSError, ValueError):
        last = None
    if not snapshots_due(cache, now, every, last):
        return
    env = dict(os.environ, RESTIC_REPOSITORY='rclone:gdrive:homelab-restic',
               RESTIC_PASSWORD_FILE='/root/.config/restic/gdrive.pass')
    try:
        r = subprocess.run(['restic', 'snapshots', '--no-lock', '--json'], env=env, capture_output=True, text=True, timeout=180)
        snaps = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        snaps = None
    if snaps is None:  # unreachable is not stale; keep the old cache
        return
    tmp = path.with_suffix('.new')
    tmp.write_text(json.dumps({'checked_at': now, 'latest': latest_by_tag(snaps), 'recent': recent_by_tag(snaps)}))
    tmp.replace(path)


def latest_by_tag(snaps):
    from datetime import datetime
    latest = {}
    for snap in snaps:
        ts = datetime.fromisoformat(re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], snap['time']).replace('Z', '+00:00')).timestamp()
        for tag in snap.get('tags') or []:
            latest[tag] = max(latest.get(tag, 0), int(ts))
    return latest


def recent_by_tag(snaps, keep=20):
    times = {}
    for tag, ts in ((t, v) for s in snaps for t, v in [(t, latest_by_tag([s])[t]) for t in (s.get('tags') or [])]):
        times.setdefault(tag, []).append(ts)
    return {t: sorted(v, reverse=True)[:keep] for t, v in times.items()}


def log_event(title, detail, ok, now, path=EVENTS):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        f.write(json.dumps({'ts': int(now), 'title': title, 'detail': detail[:300], 'ok': ok}) + '\n')
    lines = path.read_text().splitlines()
    if len(lines) > 1000:
        path.write_text('\n'.join(lines[-500:]) + '\n')


def nightly_check(nightly, path=NIGHTLY_HISTORY):
    """Alert when the 03:00 log review fails two nights in a row. One failed night is reported in the digest only."""
    if not isinstance(nightly, dict) or nightly.get('status') not in ('complete', 'failed') or not nightly.get('local_date'):
        return {}
    try:
        hist = json.loads(path.read_text())
    except (OSError, ValueError):
        hist = {}
    hist[nightly['local_date']] = nightly['status']
    hist = dict(sorted(hist.items())[-14:])
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(hist))
    last = list(hist.items())[-2:]
    import datetime
    consecutive = len(last) == 2 and (datetime.date.fromisoformat(last[1][0]) - datetime.date.fromisoformat(last[0][0])).days == 1
    bad = consecutive and all(v == 'failed' for _, v in last)
    return {'nightly-failing': (bad, 'Nightly log review failed two nights running',
                                f"Last error: {nightly.get('error') or 'unknown'}. See Infrastructure > Nightly log review.", 3)}


def restore_test_check(now, path=RESTORE_TEST):
    """Monthly off-site restore check (homelab-restore-test, 15th). A check that couldn't run is not a failure."""
    try:
        r = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    failed = [c for c in r.get('checks', []) if c.get('ok') is False]
    days = (now - (r.get('finished_at') or 0)) / 86400
    return {'restore-test': (bool(failed), 'Off-site restore test failed', '; '.join(f"{c.get('label')}: {c.get('detail')}" for c in failed) or 'All checks passed.', 4),
            'restore-overdue': (days > 40, 'Off-site restore test overdue', f'Last restore test finished {int(days)} days ago; it should run on the 15th of each month.', 3)}


def read_latest(path=SNAPSHOTS):
    try:
        return json.loads(path.read_text())['latest']
    except (OSError, ValueError, KeyError):
        return None


def unraid_backup_check(now, latest):
    """Unraid's own job (unraid-restic container, 04:00). Photos and Proxmox are covered by cloud_backup_check."""
    if not latest or 'unraid' not in latest:
        return {}
    hours = (now - latest['unraid']) / 3600
    return {'cloud-unraid': (hours > SOURCES['unraid'][1], 'Unraid cloud backup is stale',
                             f'Newest Unraid snapshot on Google Drive is {int(hours)} h old. Check the unraid-restic container.', 4)}


def digest_text(now, t, f, latest, active, nightly=None):
    """Morning summary lines. Returns (title, body, problems) from measured data only; missing data says so."""
    lines, problems = [], []
    bk = []
    for tag, (label, limit) in SOURCES.items():
        ts = (latest or {}).get(tag)
        if ts is None:
            bk.append(f'{label} no snapshot'); problems.append(f'{label} backup has no snapshot'); continue
        old = (now - ts) / 3600 > limit
        bk.append(f"{label} {time.strftime('%-I:%M %p', time.localtime(ts))}" + (' STALE' if old else ''))
        if old:
            problems.append(f'{label} backup stale')
    lines.append('Backups: ' + (', '.join(bk) if latest else 'unknown (snapshot list unavailable)'))
    try:
        rt = json.loads(RESTORE_TEST.read_text())
        state = {True: 'passed', False: 'FAILED', None: 'incomplete'}[rt.get('ok')]
        lines.append(f"Last restore test: {state} {time.strftime('%-d %b', time.localtime(rt['finished_at']))}")
        if rt.get('ok') is False:
            problems.append('Off-site restore test failed')
    except (OSError, ValueError, KeyError):
        pass
    if isinstance(nightly, dict) and nightly.get('status'):
        label = (nightly.get('model_label') or nightly.get('model') or 'local model').split(' \u00b7 ')[0]
        if nightly['status'] == 'complete':
            n = len(nightly.get('findings') or []); sk = len(nightly.get('skipped_groups') or [])
            groups = (nightly.get('evidence') or {}).get('distinct_groups')
            line = f"Nightly review: {groups if groups is not None else '?'} log groups, {n} explained on {label}" + (f', {sk} skipped' if sk else '')
            if nightly.get('fallback_reason'):
                line += f" (fallback: {nightly['fallback_reason']})"
            lines.append(line)
        elif nightly['status'] == 'failed':
            lines.append(f"Nightly review FAILED ({nightly.get('error') or 'unknown'})"); problems.append('Nightly log review failed')
    if t:
        u = t.get('unraid') or {}
        if u.get('cache_pct') is not None:
            lines.append(f"Storage: cache {u['cache_pct']}%, array {u.get('array_pct')}%")
        sy = t.get('syslog') or {}
        if sy.get('online'):
            lines.append(f"Logs (24 h): {sy.get('errors_24h', 0)} errors, {sy.get('warnings_24h', 0)} warnings from {sy.get('hosts_reporting')} hosts")
        st = t.get('speedtest') or {}
        if st.get('dl') is not None:
            lines.append(f"Internet: {round(st['dl'])} down / {round(st.get('ul') or 0)} up Mbps")
    if f:
        pend = (f.get('updates') or {}).get('pending') or []
        if pend:
            lines.append(f'Updates waiting: {len(pend)} (' + ', '.join(x['name'] for x in pend[:4]) + (' …' if len(pend) > 4 else '') + ')')
        smart = f.get('smart') or {}
        for d in smart.get('disks') or []:
            a = d.get('attrs') or {}
            bad = {k: v for k, v in a.items() if k in ('reallocated', 'pending', 'offline_uncorrectable', 'reported_uncorrectable') and v}
            if bad:
                problems.append(f"{d.get('role')} disk: " + ', '.join(f'{v} {k.replace("_", " ")}' for k, v in bad.items()))
            if d.get('running_remaining_pct') is not None:
                lines.append(f"Self-test running on {d.get('role')} ({d['running_remaining_pct']}% left)")
    if active:
        problems += [f'Active alert: {x}' for x in active]
    if problems:
        lines.insert(0, 'Needs attention: ' + '; '.join(problems))
    title = 'Homelab: all good' if not problems else f'Homelab: {len(problems)} thing' + ('s' if len(problems) > 1 else '') + ' to check'
    return title, '\n'.join(lines), problems


def ask_qwen(prompt, wait=420):
    """Ask Homepage's Ask Qwen (27B on the PC when on, else the A310). Returns (answer, model) or (None, reason)."""
    body = json.dumps({'prompt': prompt[:3000], 'target': 'auto'}).encode()  # digest and why: 27B when the PC is on
    headers = {'Content-Type': 'application/json', 'X-Qwen-Request': '1'}
    deadline = time.time() + wait
    while True:
        try:
            urllib.request.urlopen(urllib.request.Request(QWEN_URL, data=body, method='POST', headers=headers), timeout=20).read()
            break
        except urllib.error.HTTPError as e:
            if e.code != 409 or time.time() > deadline:
                return None, f'Ask Qwen refused the question ({e.code}).'
            time.sleep(30)  # another question or the nightly review is running
        except Exception:
            return None, 'Ask Qwen is unreachable.'
    while time.time() < deadline:
        time.sleep(5)
        try:
            with urllib.request.urlopen(urllib.request.Request(QWEN_URL, headers={'X-Qwen-Request': '1'}), timeout=20) as r:
                job = json.load(r)
        except Exception:
            continue
        if job.get('status') == 'complete':
            return job.get('answer'), job.get('model')
        if job.get('status') == 'failed':
            return None, job.get('error') or 'Ask Qwen could not answer.'
    return None, 'Ask Qwen took too long.'


def active_alerts(state=None, details=None):
    state = state if state is not None else (json.loads(STATE.read_text()) if STATE.exists() else {})
    details = details if details is not None else read_details()
    return [details.get(k, {}).get('title', k) for k, v in state.items() if v.get('alerted')]


def read_details():
    try:
        return json.loads(DETAILS.read_text())
    except (OSError, ValueError):
        return {}


def remember(key, title, detail, now):
    d = read_details()
    d[key] = {'title': title, 'detail': detail, 'at': int(now)}
    DETAILS.parent.mkdir(parents=True, exist_ok=True)
    DETAILS.write_text(json.dumps(d))


def run_digest(now):
    def safe(path):
        try:
            return fetch(path, timeout=120)
        except Exception:
            return None
    t, f, s = safe('/api/telemetry'), safe('/api/fleet'), safe('/api/storage?window=900')
    refresh_snapshots(now, every=0)
    title, body, problems = digest_text(now, t, f, read_latest(), active_alerts(), ((s or {}).get('activity') or {}).get('nightly'))
    answer, model = ask_qwen('This is my homelab morning summary, from measured data:\n' + body +
                             '\nIn at most two short sentences: is anything unusual or worth doing today? '
                             'If nothing stands out, say so. Do not repeat the numbers.', wait=480)
    if answer:
        body += f'\n\nQwen ({(model or "local").split(" · ")[0]}): {answer.strip()[:600]}'
    send('info', title, body, 2 if not problems else 3, markdown=False)


def http_ok(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def pc_awake():
    return subprocess.run(['ping', '-c', '1', '-W', '2', PC], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def paused_for_game():
    """True/False from the PC's game-watch flag, None when the PC can't be asked (then nothing is concluded)."""
    try:
        out = subprocess.run(['/usr/local/bin/pcps'], input=f"if (Test-Path '{GAME_FLAG}') {{ 'GAME=yes' }} else {{ 'GAME=no' }}",
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    return True if 'GAME=yes' in out else False if 'GAME=no' in out else None


def ai_engine_checks(awake=pc_awake, strata_ok=None, llama_ok=None, game=paused_for_game):
    """The PC's two AI engines, only while the PC is awake (asleep is normal, and keeps the last state).
    Strata's server stays up during games (game-watch only unloads its model); llama-server is stopped on purpose
    during games, so it only counts as down when no game is running."""
    if not awake():
        return {}
    out = {}
    up = strata_ok() if strata_ok else http_ok(f'http://{PC}:8088/health')
    out['strata-down'] = (not up, 'Strata is not running',
                          'The gaming PC is awake but Strata (:8088) does not answer. On the PC: Start-ScheduledTask Strata.', 3)
    up = llama_ok() if llama_ok else http_ok(f'http://{PC}:8080/health')
    if up:
        out['llama-down'] = (False, 'llama-server is not running', '', 3)
    else:
        playing = game()
        if playing is not None:
            out['llama-down'] = (not playing, 'llama-server is not running',
                                 'The gaming PC is awake, no game is running, and llama-server (:8080) does not answer. On the PC: Start-Service llm-router.', 3)
    return out


def hold_for_maintenance(sends, state, snoozed):
    """While alerts are snoozed, new problems are not sent and stay un-alerted, so anything still bad when the snooze
    ends alerts on the next run. Recoveries of earlier alerts still go out."""
    if not snoozed:
        return sends
    kept = []
    for send in sends:
        if send[1] == 'down':
            state[send[0]]['alerted'] = False
        else:
            kept.append(send)
    return kept


def transitions(prev, current):
    """Yield (key, kind, title, detail, priority) to send, and the new state."""
    sends, state = [], {}
    for key, (bad, title, detail, prio) in current.items():
        p = prev.get(key, {'count': 0, 'alerted': False})
        count = p['count'] + 1 if bad else 0
        need = 1 if key == 'updates-failed' else CONSECUTIVE
        alerted = p['alerted']
        if bad and not alerted and count >= need:
            sends.append((key, 'down', title, detail, prio)); alerted = True
        elif not bad and alerted:
            sends.append((key, 'up', title, detail, 3)); alerted = False
        state[key] = {'count': count, 'alerted': alerted}
    for key, p in prev.items():  # checks that vanished keep their state (missing data is not a recovery)
        state.setdefault(key, p)
    return sends, state


def topic():
    m = re.search(r'^TOPIC="([^"]+)"', WATCHDOG.read_text(), re.M)
    if not m:
        raise SystemExit('No TOPIC in watchdog')
    return m.group(1)


def header_text(text):
    """ntfy reads raw header bytes as UTF-8, so anything outside ASCII (°, ·, accents) goes as an RFC 2047 encoded word."""
    try:
        text.encode('ascii')
        return text
    except UnicodeEncodeError:
        import base64
        return '=?UTF-8?B?' + base64.b64encode(text.encode()).decode() + '?='


def send(kind, title, detail, prio, key=None, markdown=False, tags=None):
    head = ('RECOVERED: ' if kind == 'up' else '') + title
    tags = tags or {'up': 'white_check_mark', 'info': 'house'}.get(kind, 'warning')
    headers = {'Title': header_text(head), 'Priority': str(prio), 'Tags': tags}
    if markdown:
        headers['Markdown'] = 'yes'
    if kind == 'down' and key:  # phone (on LAN or WireGuard) calls homelab-why, which pushes the answer back
        headers['Actions'] = f'http, Ask Qwen why, {WHY_URL}?key={urllib.parse.quote(key)}, method=POST, clear=true'
    req = urllib.request.Request('https://ntfy.sh/' + topic(), data=detail.encode(), method='POST', headers=headers)
    urllib.request.urlopen(req, timeout=15).read()


def why_prompt(title, detail):
    return (f'My homelab alert just fired: "{title}". Detail: {detail}\n'
            'What does it most likely mean, what should I check first, and is it urgent? Under 120 words. '
            'Say which parts come from the observations and which are general knowledge.')


def serve_why(port=8997):
    """POST /why?key=<alert key> or /why?title=..&detail=..: answer in the background, push to ntfy. Only known alert keys; one at a time."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    busy = threading.Lock()

    def answer(key, info):
        try:
            text, model = ask_qwen(why_prompt(info['title'], info['detail']))
            send('info', f"Why: {info['title']}", (text or f'No answer: {model}')[:3500], 3, markdown=True, tags='robot')
        finally:
            busy.release()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            key = (q.get('key') or [''])[0]
            info = read_details().get(key)
            if not info and q.get('title'):  # senders on other hosts put the alert text in the button URL
                info = {'title': q['title'][0][:200], 'detail': (q.get('detail') or [''])[0][:1500]}
            if urllib.parse.urlparse(self.path).path != '/why' or not info:
                return self.reply(404, 'Unknown alert.')
            if not busy.acquire(blocking=False):
                return self.reply(202, 'Already asking Qwen about an alert. That answer arrives as a notification; tap again after it if you need this one too.')  # 2xx so ntfy doesn't show a failure on a repeat tap
            threading.Thread(target=answer, args=(key, info), daemon=True).start()
            self.reply(202, 'Asking Qwen. The answer arrives as a notification.')

        def reply(self, code, text):
            raw = text.encode()
            self.send_response(code); self.send_header('Content-Type', 'text/plain'); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def log_message(self, *a):
            pass

    with ThreadingHTTPServer(('0.0.0.0', port), Handler) as srv:
        srv.serve_forever()


def main(argv):
    if '--digest' in argv:
        run_digest(time.time())
        return 0
    if '--serve-why' in argv:
        serve_why()
        return 0
    if '--test' in argv:
        detail = 'homelab-alerts is installed and can reach your ntfy topic. Tap "Ask Qwen why" to test the button (needs LAN or WireGuard).'
        remember('test', 'Homelab alerts test', 'A test alert sent by hand to check ntfy delivery and the Ask Qwen why button.', time.time())
        send('down', 'Homelab alerts test', detail, 2, key='test')
        return 0
    now = time.time()
    def safe(path):
        try:
            return fetch(path)
        except Exception:
            return None
    t, s, f = safe('/api/telemetry'), safe('/api/storage?window=900'), safe('/api/fleet')
    if t is None:  # Homepage unreachable: Uptime Kuma already watches it; record only
        pass
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    current = checks(t, s, f, now)
    current.update(cloud_backup_check(now))
    refresh_snapshots(now)
    latest = read_latest()
    current.update(unraid_backup_check(now, latest))
    current.update(config_backup_check(now))
    current.update(restore_test_check(now))
    current.update(nightly_check(((s or {}).get('activity') or {}).get('nightly')))
    try:
        current.update(ai_engine_checks())
    except Exception:
        pass
    sends, state = transitions(prev, current)
    snooze = safe('/api/maintenance')  # dashboard unreachable: not snoozed (alerts fail open)
    sends = hold_for_maintenance(sends, state, bool(isinstance(snooze, dict) and snooze.get('active') is True))
    for key, kind, title, detail, prio in sends:
        try:
            if kind == 'down':
                remember(key, title, detail, now)
            send(kind, title, detail, prio, key=key)
            log_event(('Recovered: ' if kind == 'up' else '') + title, detail, kind == 'up', now)
        except Exception:
            state[key]['alerted'] = not state[key]['alerted']  # retry next run
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    if '--dry-run' in argv:
        print(json.dumps({k: v[0] for k, v in checks(t, s, f, now).items()}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
