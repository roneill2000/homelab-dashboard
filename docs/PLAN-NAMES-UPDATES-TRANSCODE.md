# Plan: service names + HTTPS, weekly updates, A310 transcoding benchmark

For agy. **Every value below was checked live on 22 Oct 2026. Do not change names, IPs, ports or commands.**
Run each step, compare with **Expect**, and only continue when it matches. If a step fails, stop and report
the step number and the exact output to Ryan. Do not improvise a fix.

## Rules
1. Work in your own clone of the `n100` remote. `git pull --rebase` before you start and before every push. Never force-push. Push only to `n100`.
2. **Commit messages must not contain `Co-Authored-By` or any other credit line.**
3. Python file reads and writes always use `encoding='utf-8'`.
4. Never write tokens, API keys, passwords, the ntfy topic or the Caddy CA private key into the repo.
5. Shell commands marked **[Proxmox]** run on `root@192.168.0.3`; **[Unraid]** run on `root@192.168.0.4` (reach it with `ssh root@192.168.0.3` then `ssh root@192.168.0.4`); **[proxy]** run inside the new container (`pct exec 115 -- bash -c '…'` from Proxmox).

---

## Part 1: names and HTTPS (`*.lan`) — about 1 hour

Facts: proxy IP **192.168.0.20** is free and outside the router's DHCP pool (pool starts at .33). Pi-hole primary is LXC 106 (.11); the replica (.10) copies it every 30 min with nebula-sync (`FULL_SYNC=true`), so **only edit the primary**. Six existing records move to the proxy; machine names (`unraid.lan`, `n100-proxmox.lan`, `router.lan`, …) do not change. Nothing in the homelab uses the six moved names.

### 1.1 Create LXC 115 [Proxmox]
```sh
pct create 115 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname proxy --cores 1 --memory 512 --swap 256 --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,gw=192.168.0.1,ip=192.168.0.20/24,type=veth \
  --nameserver "192.168.0.11 192.168.0.10" --searchdomain lan --timezone America/New_York \
  --unprivileged 1 --onboot 1 --tags proxy --ostype debian
pct start 115 && sleep 5 && pct exec 115 -- ping -c1 -W2 192.168.0.1
```
**Expect:** `1 packets transmitted, 1 received`.

### 1.2 Syslog forwarding and backups [Proxmox]
```sh
pct exec 113 -- cat /etc/rsyslog.d/99-forward-proxmox.conf > /tmp/fwd.conf
pct exec 115 -- bash -c 'apt-get update -qq && apt-get install -y -qq rsyslog >/dev/null'
pct push 115 /tmp/fwd.conf /etc/rsyslog.d/99-forward-proxmox.conf && pct exec 115 -- systemctl restart rsyslog
pvesh set /cluster/backup/backup-e08b168e-2331 --vmid 102,104,105,106,107,111,112,113,114,115
pvesh get /cluster/backup/backup-e08b168e-2331 --output-format json | grep -o '"vmid":"[^"]*"'
```
**Expect:** `"vmid":"102,104,105,106,107,111,112,113,114,115"`.

### 1.3 Install Caddy [proxy]
```sh
apt-get install -y -qq caddy && caddy version
```
**Expect:** a version starting `v2.6.2`.

### 1.4 Caddyfile [proxy] — write exactly this to `/etc/caddy/Caddyfile`, also save it in the repo as `proxy/Caddyfile`
```
{
	local_certs
}

home.lan, homepage.lan {
	reverse_proxy 192.168.0.2:80
}
proxmox.lan {
	reverse_proxy https://192.168.0.3:8006 {
		transport http {
			tls_insecure_skip_verify
		}
	}
}
kasm.lan {
	reverse_proxy https://192.168.0.15:443 {
		transport http {
			tls_insecure_skip_verify
		}
	}
}
nas.lan {
	reverse_proxy 192.168.0.4:80
}
scrutiny.lan {
	reverse_proxy 192.168.0.4:8080
}
glances.lan {
	reverse_proxy 192.168.0.4:61208
}
audiobooks.lan {
	reverse_proxy 192.168.0.4:13378
}
logs.lan {
	reverse_proxy 192.168.0.3:8999
}
pihole.lan {
	reverse_proxy 192.168.0.11:80
}
pihole2.lan {
	reverse_proxy 192.168.0.10:80
}
kuma.lan {
	reverse_proxy 192.168.0.10:3001
}
jellyfin.lan {
	reverse_proxy 192.168.0.17:8096
}
ai.lan {
	reverse_proxy 192.168.0.16:3000
}
opencode.lan {
	reverse_proxy 192.168.0.19:4096
}
memos.lan {
	reverse_proxy 192.168.0.18:5230
}
speedtest.lan {
	reverse_proxy 192.168.0.14:80
}
lanspeed.lan {
	reverse_proxy 192.168.0.13:3000
}
netalertx.lan {
	reverse_proxy 192.168.0.13:20211
}
tv.lan {
	reverse_proxy 192.168.0.13:8080
}
sonarr.lan {
	reverse_proxy 192.168.0.13:8989
}
readarr.lan {
	reverse_proxy 192.168.0.13:8787
}
prowlarr.lan {
	reverse_proxy 192.168.0.13:9696
}
sab.lan {
	reverse_proxy 192.168.0.13:8085
}
rdt.lan {
	reverse_proxy 192.168.0.13:6500
}
files.lan {
	reverse_proxy 192.168.0.13:6501
}
guac.lan, guacamole.lan {
	redir / /guacamole/
	reverse_proxy 192.168.0.6:8080
}

# Old direct ports keep working after jellyfin.lan / guacamole.lan move to this proxy (TV apps, bookmarks).
http://:8096 {
	reverse_proxy 192.168.0.17:8096
}
http://:8080 {
	reverse_proxy 192.168.0.6:8080
}
```
Then: `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && systemctl reload caddy && systemctl is-active caddy`
**Expect:** `Valid configuration` and `active`.

### 1.5 DNS records [Proxmox] — save the old list first, then set the new one
```sh
pct exec 106 -- pihole-FTL --config dns.hosts > /root/pihole-dns-hosts-before-proxy.txt
pct exec 106 -- pihole-FTL --config dns.hosts '["192.168.0.1 router.lan","192.168.0.1 router","192.168.0.20 kasm.lan","192.168.0.15 kasm","192.168.0.3 n100-proxmox.lan","192.168.0.3 n100-proxmox","192.168.0.4 unraid.lan","192.168.0.4 unraid","192.168.0.20 homepage.lan","192.168.0.2 homepage","192.168.0.20 guacamole.lan","192.168.0.6 guacamole","192.168.0.10 orangepi.lan","192.168.0.10 orangepi","192.168.0.20 pihole.lan","192.168.0.11 pihole","192.168.0.13 utilities.lan","192.168.0.13 utilities","192.168.0.20 speedtest.lan","192.168.0.14 speedtest","192.168.0.20 jellyfin.lan","192.168.0.17 jellyfin","192.168.0.25 minecraft.lan","192.168.0.25 minecraft","192.168.0.30 wireguard.lan","192.168.0.30 wireguard","192.168.0.20 proxy.lan","192.168.0.20 home.lan","192.168.0.20 proxmox.lan","192.168.0.20 nas.lan","192.168.0.20 scrutiny.lan","192.168.0.20 glances.lan","192.168.0.20 audiobooks.lan","192.168.0.20 logs.lan","192.168.0.20 pihole2.lan","192.168.0.20 kuma.lan","192.168.0.20 ai.lan","192.168.0.20 opencode.lan","192.168.0.20 memos.lan","192.168.0.20 lanspeed.lan","192.168.0.20 netalertx.lan","192.168.0.20 tv.lan","192.168.0.20 sonarr.lan","192.168.0.20 readarr.lan","192.168.0.20 prowlarr.lan","192.168.0.20 sab.lan","192.168.0.20 rdt.lan","192.168.0.20 files.lan","192.168.0.20 guac.lan"]'
for n in home jellyfin proxmox unraid n100-proxmox sonarr; do printf "%s.lan " $n; dig +short $n.lan @192.168.0.11; done
```
**Expect:** `home.lan 192.168.0.20`, `jellyfin.lan 192.168.0.20`, `proxmox.lan 192.168.0.20`, `unraid.lan 192.168.0.4`, `n100-proxmox.lan 192.168.0.3`, `sonarr.lan 192.168.0.20`.
**If the `--config` set command errors:** stop. Do not hand-edit `pihole.toml`. Report the error.
After 30 minutes, run the same loop with `@192.168.0.10`. **Expect:** the same answers (nebula-sync copied them).

### 1.6 Certificate trust files [Proxmox]
```sh
pct pull 115 /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt /root/homelab-root-ca.crt
openssl x509 -in /root/homelab-root-ca.crt -noout -subject -enddate
```
**Expect:** subject contains `Caddy Local Authority`. Copy the file into the repo as `proxy/homelab-root-ca.crt` (it is the public certificate; never copy `root.key`). Create `proxy/TRUST.md` with exactly:
```
# Trust the homelab certificate (once per device)
macOS: double-click homelab-root-ca.crt → Keychain Access opens → find "Caddy Local Authority" in System → double-click → Trust → "Always Trust" → close (password).
Windows: open PowerShell as administrator → certutil -addstore Root homelab-root-ca.crt
Android: copy the file to the phone → Settings → Security & privacy → More security → Encryption & credentials → Install a certificate → CA certificate.
iPhone: AirDrop the file → Settings → Profile Downloaded → Install → Settings → General → About → Certificate Trust Settings → turn on "Caddy Local Authority".
Some apps (for example the Jellyfin Android app) ignore user-installed certificates: keep using http://192.168.0.17:8096 or http://jellyfin.lan:8096 in those apps.
```

### 1.7 Back up the CA key [repo + Proxmox]
In `backup/homelab-config-backup.sh`, after the `save orangepi …` line, add:
```bash
save proxy "pct exec 115 -- tar -czf - -C /var/lib/caddy .local/share/caddy 2>/dev/null"
```
Install it: `scp backup/homelab-config-backup.sh root@192.168.0.3:/usr/local/sbin/homelab-config-backup.new` then on Proxmox
`chmod 700 /usr/local/sbin/homelab-config-backup.new && mv /usr/local/sbin/homelab-config-backup.new /usr/local/sbin/homelab-config-backup && systemctl start homelab-config-backup && ls /var/backups/homelab-configs/ | grep proxy`
**Expect:** a `proxy-YYYYMMDD.tgz` file.

### 1.8 Homepage accepts the new names [repo]
In `qwen_proxy.py`, replace these two lines:
```python
HOSTS={'192.168.0.2','192.168.0.2:80','127.0.0.1','localhost'}
```
```python
    return handler.headers.get(header)=='1' and host in HOSTS and (origin is None or origin=='http://'+host)
```
with:
```python
HOSTS={'192.168.0.2','192.168.0.2:80','127.0.0.1','localhost','home.lan','homepage.lan'}
```
```python
    return handler.headers.get(header)=='1' and host in HOSTS and (origin is None or origin in ('http://'+host,'https://'+host))
```
In `test_action_guard.py`, inside `test_homepage_request_runs_with_bounded_timer`, add one more request after the existing `ok` request, with headers `{'Host': 'home.lan', 'X-Dashboard-Action': '1', 'Origin': 'https://home.lan'}`, expecting the same success as `ok`. Copy the pattern of the existing `ok` call exactly.
Run `python3 -m unittest discover -p 'test_*.py'`. **Expect:** `OK`.

### 1.9 Homepage links [repo]
In `src/app.jsx`, in the service catalog near the top, replace only the URL (4th field) for these ids:
| id | new URL |
| --- | --- |
| memos | https://memos.lan |
| openwebui | https://ai.lan |
| opencode | https://opencode.lan |
| pve | https://proxmox.lan |
| unraid | https://nas.lan |
| scrutiny | https://scrutiny.lan |
| syslog | https://logs.lan |
| pihole11 | https://pihole.lan/admin |
| pihole10 | https://pihole2.lan/admin |
| openspeedtest | https://lanspeed.lan |
| speedtest | https://speedtest.lan |
| netalertx | https://netalertx.lan |
| kuma | https://kuma.lan |
| jellyfin | https://jellyfin.lan |
| uhf | https://tv.lan |
| sonarr | https://sonarr.lan |
| readarr | https://readarr.lan |
| prowlarr | https://prowlarr.lan |
| sabnzbd | https://sab.lan |
| rdt | https://rdt.lan |
| audiobooks | https://audiobooks.lan |
| kasm | https://kasm.lan |
| guacamole | https://guac.lan |
| files | https://files.lan |
| glances | https://glances.lan |

Leave `router`, `minecraft` and `flaresolverr` unchanged. Then `npm run build`, `python3 deploy.py`.
**Expect:** deploy prints `"status": "healthy"`.

### 1.10 Verify [Mac or Proxmox, with the CA file]
```sh
for n in home proxmox nas scrutiny glances audiobooks logs pihole pihole2 kuma jellyfin ai opencode memos speedtest lanspeed netalertx tv sonarr readarr prowlarr sab rdt files guac kasm; do printf "%-12s " $n; curl -s -o /dev/null -w "%{http_code}\n" --cacert proxy/homelab-root-ca.crt --resolve $n.lan:443:192.168.0.20 https://$n.lan/; done
curl -s -o /dev/null -w "jellyfin:8096 %{http_code}\n" --resolve jellyfin.lan:8096:192.168.0.20 http://jellyfin.lan:8096/
```
**Expect:** every line is `200`, `301`, `302`, `303`, `307`, `308` or `401`. Any `000` or `502`: stop and report that name.
Then ask Ryan to trust the certificate on his Mac (`proxy/TRUST.md`) and check in a browser: `https://home.lan` loads, an Ask Qwen question works, the Proxmox console opens at `https://proxmox.lan`, a Jellyfin video plays at `https://jellyfin.lan`.

### 1.11 Commit
Add `proxy/README.md` with the table from step 1.9 plus `proxy.lan → 192.168.0.20 (LXC 115, Caddy)`. Commit `proxy/`, `qwen_proxy.py`, `test_action_guard.py`, `src/app.jsx`, `index.html`, `backup/homelab-config-backup.sh` with message `proxy: HTTPS names for every service (*.lan via Caddy on LXC 115)`. Push.

---

## Part 2: weekly hands-off updates (Saturday 7:30 AM) — about 45 minutes

Facts: the update runner is `updates/homelab_update.py`, installed on Proxmox as `/usr/local/sbin/homelab-update` by `python3 updates/install.py`. `run_update()` already refuses to start or continue during a Proxmox backup, an Unraid parity check, the mover, or an Ask Qwen question (`busy_reasons()`), snapshots each LXC first, goes one at a time and stops at the first failure. Exclude **`kasm`** (LXC 103) and **`itzg-minecraft-server`** (Docker) — Ryan's choice. `homelab-alerts` already alerts immediately on a failed update, so the summary must not repeat that alert.

### 2.1 Code [repo: `updates/homelab_update.py`]
a. Under `LOCK = Path('/run/homelab-update.lock')` add:
```python
WEEKLY_EXCLUDE = {'kasm', 'itzg-minecraft-server'}  # Ryan, 22 Oct 2026: update these by hand
WATCHDOG = Path('/usr/local/bin/homelab-health-watchdog.sh')
```
b. Change the first line of `def run_update(run):` to `def run_update(run, weekly=False):` and, directly after the line `run_check(run)` inside it, add:
```python
    if weekly:
        for i in run.state['items']:
            if i['name'] in WEEKLY_EXCLUDE and i['state'] == 'pending':
                i.update(state='skipped', detail='Excluded from automatic updates; update by hand')
        run.save()
```
c. Add these functions directly above `class Refused(Exception):`:
```python
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
```
(A successful update leaves the item in state `'done'`; checked 22 Oct.)

d. In `main(argv)`, add a branch next to `elif command in ('run-check', 'run-update'):`:
```python
    elif command == 'run-weekly':
        run_weekly()
    elif command == 'weekly-dry-run':
        print(json.dumps(dict(cloud_backup_busy=cloud_backup_busy(), busy=busy_reasons(), exclude=sorted(WEEKLY_EXCLUDE))))
```

### 2.2 Tests [repo: `test_updates.py`]
Add one test class `WeeklyTests` with three tests, following the style of the existing tests in that file:
1. `weekly_summary` with items `[{'name':'memos','state':'done'},{'name':'kasm','state':'skipped','detail':'Excluded from automatic updates; update by hand'}]` returns title `'Weekly updates done'` and a body containing `Updated: memos` and `Left for you: kasm`.
2. `weekly_summary` with one `failed` item returns title `'Weekly updates stopped after a failure'`.
3. `run_weekly` with `cloud_backup_busy` patched to return True calls `ntfy` once with title `'Weekly updates skipped'` and never calls `run_update`.
Run `python3 -m unittest discover -p 'test_*.py'`. **Expect:** `OK`.

### 2.3 Timer files [repo: `updates/`]
`updates/homelab-weekly-update.service`:
```
[Unit]
Description=Weekly hands-off updates (homelab-update)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/homelab-update run-weekly
TimeoutStartSec=3h
```
`updates/homelab-weekly-update.timer`:
```
[Unit]
Description=Weekly hands-off updates, Saturday 07:30

[Timer]
OnCalendar=Sat *-*-* 07:30:00
Persistent=false

[Install]
WantedBy=timers.target
```

### 2.4 Install and dry run [Mac, then Proxmox]
```sh
python3 updates/install.py
scp updates/homelab-weekly-update.service updates/homelab-weekly-update.timer root@192.168.0.3:/etc/systemd/system/
```
[Proxmox]:
```sh
systemctl daemon-reload && systemctl enable --now homelab-weekly-update.timer
systemctl list-timers homelab-weekly-update.timer --no-pager | sed -n 2p
/usr/local/sbin/homelab-update weekly-dry-run
```
**Expect:** the timer line shows the next Saturday 07:30, and the dry run prints JSON with `"exclude": ["itzg-minecraft-server", "kasm"]`. **Do not** run `run-weekly` by hand; the first real run is Saturday unless Ryan says otherwise.

### 2.5 Homepage note [repo: `src/app.jsx`]
On the Updates page, directly under its page heading, add one line of text: `Automatic updates run Saturdays at 7:30 AM (Kasm and Minecraft are left for you). Results arrive by ntfy.` Use the existing `source-note` class. `npm run build`, `python3 deploy.py`. **Expect:** `"status": "healthy"`.

### 2.6 Docs and commit
Add a row to `alerts/SENDERS.md`: `| homelab-weekly-update | Proxmox homelab-weekly-update.timer, Sat 07:30 | One summary after the weekly update run, or one "skipped" message. Failures also raise the existing updates-failed alert. |`
Commit with message `updates: weekly hands-off run on Saturdays at 7:30 (Kasm and Minecraft excluded)`. Push.

---

## Part 3: A310 transcoding benchmark — about 30 minutes

Facts checked: Jellyfin already uses the A310 (`HardwareAccelerationType=qsv`, `QsvDevice=/dev/dri/renderD129`, the only GPU passed into the container). HEVC 10-bit, VP9, AV1 decode, low-power H.264/HEVC encoders, HEVC/AV1 encoding and VPP tone mapping are on. `VaapiDevice=/dev/dri/renderD128` is unused in QSV mode; leave it. **No config change is needed; this part only measures.**

### 3.1 Take the A310 lock and start [Unraid]
The lock stops Ask Qwen and the nightly review from loading a model during the test. Run everything in one shell:
```sh
exec 9>/mnt/disk1/.homelab-nightly/run.lock && flock -n 9 && echo LOCKED
```
**Expect:** `LOCKED`. If not, wait 5 minutes and retry (a question or the nightly review is running).

### 3.2 Make test clips (inside the Jellyfin container) [Unraid, same shell]
```sh
J="docker exec Jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -loglevel error -y"
HW="-init_hw_device vaapi=va:/dev/dri/renderD129 -init_hw_device qsv=qs@va -filter_hw_device qs"
$J $HW -f lavfi -i testsrc2=size=3840x2160:rate=24 -t 30 -vf "format=p010le,hwupload=extra_hw_frames=64" -c:v hevc_qsv -profile:v main10 -b:v 40M /transcode/bench-4k-hevc10.mkv
$J $HW -f lavfi -i testsrc2=size=3840x2160:rate=24 -t 30 -vf "format=nv12,hwupload=extra_hw_frames=64" -c:v av1_qsv -b:v 20M /transcode/bench-4k-av1.mkv
$J $HW -f lavfi -i testsrc2=size=1920x1080:rate=24 -t 30 -vf "format=nv12,hwupload=extra_hw_frames=64" -c:v h264_qsv -b:v 12M /transcode/bench-1080p-h264.mkv
docker exec Jellyfin ls -la /transcode/ | grep bench
```
**Expect:** three `bench-*` files.

### 3.3 Run the four tests (each prints a speed like `speed=4.1x`) [Unraid, same shell]
```sh
T="docker exec Jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -nostats -benchmark -y"
D="-hwaccel qsv -hwaccel_output_format qsv"
echo A; $T $HW $D -i /transcode/bench-4k-hevc10.mkv -vf "vpp_qsv=w=1920:h=1080:format=nv12:tonemap=1" -c:v h264_qsv -b:v 8M -f null - 2>&1 | grep -E "bench: utime|frame=" | tail -2
echo B; $T $HW $D -i /transcode/bench-1080p-h264.mkv -vf "vpp_qsv=w=1280:h=720" -c:v h264_qsv -b:v 4M -f null - 2>&1 | grep -E "bench: utime|frame=" | tail -2
echo C; $T $HW $D -i /transcode/bench-4k-av1.mkv -vf "vpp_qsv=w=1920:h=1080:format=nv12" -c:v h264_qsv -b:v 8M -f null - 2>&1 | grep -E "bench: utime|frame=" | tail -2
echo D; $T $HW $D -i /transcode/bench-1080p-h264.mkv -c:v hevc_qsv -b:v 6M -f null - 2>&1 | grep -E "bench: utime|frame=" | tail -2
```
Note each test's `fps=` and `speed=` (720 frames each; real time is 24 fps). **If a test prints an error instead,** copy the error into the results file for that test and continue with the next one. Do not change the command.

### 3.4 Streams at once (test A, the hardest) [Unraid, same shell]
```sh
for n in 2 3 4 5 6; do echo "== $n at once"; for i in $(seq $n); do ($T $HW $D -i /transcode/bench-4k-hevc10.mkv -vf "vpp_qsv=w=1920:h=1080:format=nv12:tonemap=1" -c:v h264_qsv -b:v 8M -f null - 2>&1 | grep -oE "fps=[ 0-9.]+" | tail -1) & done; wait; done
```
The stream count is the largest `n` where every line still shows `fps=` 24 or more.

### 3.5 Sharing with Ask Qwen
Release the lock first: `flock -u 9`. Then ask Ryan to ask one question in Homepage's Ask Qwen box with the dropdown on "A310 · Gemma 4 E4B". While it is answering, run test A once more (step 3.3, line A). Note whether test A's speed drops, and whether Ask Qwen finished or showed an error.

### 3.6 Clean up [Unraid]
```sh
docker exec Jellyfin sh -c 'rm -f /transcode/bench-*.mkv'
```

### 3.7 Results and commit [repo]
Write `evals/a310-transcode-2026-10-22/RESULTS.md` with: the facts block from the top of Part 3, a table `Test | fps | speed` for A–D, the stream count from 3.4, and the sharing result from 3.5. On Homepage's Media page add one `source-note` line: `Hardware transcoding: Intel Arc A310 (QSV) · up to N 4K HDR → 1080p streams at once` (use the real N). `npm run build`, `python3 deploy.py`. Commit with message `evals: A310 transcoding benchmark`. Push.

---

## Done means
- All 26 names in step 1.10 answer, Ryan's Mac trusts the certificate, and Ask Qwen works from `https://home.lan`.
- The weekly timer shows next Saturday 07:30 and the dry run lists the two exclusions.
- `evals/a310-transcode-2026-10-22/RESULTS.md` has real numbers.
- Every commit is pushed to `n100`, tests pass, and no commit has a credit line.
