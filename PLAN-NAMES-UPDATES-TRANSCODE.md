# Plan: service names + HTTPS, weekly hands-off updates, A310 transcoding check

For agy. Three independent projects; do them in this order. Ryan chose `.lan` names and Saturday 7:30 AM updates.

## Rules (all three)

1. **Git:** work in your own clone of the `n100` remote, pull with rebase before starting and before pushing, never force-push, never push anywhere but `n100`. **No `Co-Authored-By` or other credit lines in commit messages** (Ryan's rule).
2. **UTF-8:** every Python read/write uses `encoding='utf-8'`. Run `deploy.py` only after `npm run build`, and check the live page has no `Â`.
3. **No secrets in files or commits:** no tokens, API keys, ntfy topic, CA private keys, passwords.
4. **Ask Ryan before** anything that restarts Jellyfin, Pi-hole or Proxmox guests outside the steps below, and before the first real automatic update run.
5. **The A310 is shared.** Ask Qwen and the 03:00 nightly review use it. Before any GPU benchmark, take the lock: `flock -n /mnt/disk1/.homelab-nightly/run.lock` (on Unraid) and release it after. Never stop the `homelab-nightly-llm` container.
6. Save results and docs in the repo; tests must pass (`python3 -m unittest discover -p 'test_*.py'`).

---

## 1. Service names and HTTPS (`*.lan`)

**Goal:** `https://jellyfin.lan` instead of `http://192.168.0.17:8096`, with no browser warning on Ryan's devices.

**Known facts (verify first where marked)**
- Primary Pi-hole: LXC 106 at .11 (Pi-hole v6, config in `/etc/pihole/pihole.toml`). Replica: Orange Pi .10, synced from the primary by nebula-sync (full sync). **Edit the primary only.**
- The GL.iNet router may also hand out `<device>.lan` names (OpenWrt default domain). Verify first: `dig bedroom.lan @192.168.0.11` and Pi-hole's conditional forwarding settings, so device names keep working.
- Service list and IPs: the catalog at the top of `src/app.jsx`, plus Proxmox (.3:8006, HTTPS self-signed), Unraid (.4), router (.1), Pi-hole admins (.11, .10), Uptime Kuma (.10:3001), syslog monitor (.3:8999), Glances, Scrutiny, NetAlertX.

**Steps**
1. **Proxy LXC 115 `proxy`:** Debian 13, 1 core, 512 MB, 4 GB disk, static IP. Pick a free address (proposal: .20) and prove it is free (ping, router DHCP leases, NetAlertX). Start on boot. Add it to the Proxmox backup job (`backup-e08b168e-2331`) and to syslog forwarding like the other LXCs.
2. **Caddy** from the Debian repository (`apt install caddy`; record the version). `/etc/caddy/Caddyfile`:
   - global `local_certs` (Caddy's own CA; nothing touches the internet)
   - one site block per name: `jellyfin.lan { reverse_proxy 192.168.0.17:8096 }`
   - HTTPS upstreams with self-signed certs (Proxmox 8006, Kasm 443): `reverse_proxy https://…` with `transport http { tls_insecure_skip_verify }` (LAN only)
   - Guacamole: `redir / /guacamole/` then proxy
   - WebSockets work by default (Proxmox console, Open WebUI, Jellyfin, OpenCode)
3. **Names** (short, lowercase): `home` (Homepage), `proxmox`, `unraid`, `router`, `jellyfin`, `sonarr`, `readarr`, `prowlarr`, `sab`, `rdt`, `audiobooks`, `files`, `glances`, `scrutiny`, `guac`, `kasm`, `ai` (Open WebUI), `opencode`, `memos`, `speedtest`, `pihole`, `pihole2`, `kuma`, `netalertx`, `logs`. Adjust to what actually exists; put the final table in `proxy/README.md`.
4. **DNS:** on the primary Pi-hole add one local record per name pointing at the proxy (`192.168.0.20 jellyfin.lan` …), via the v6 web UI (Local DNS) or `pihole-FTL --config dns.hosts` (verify the v6 syntax first). No wildcard: it would swallow router device names. Confirm the replica receives them after nebula-sync, then `dig +short jellyfin.lan @192.168.0.11` and `@192.168.0.10` both return the proxy IP.
5. **Trust the CA:** copy Caddy's root certificate (`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`, public part only) to the repo as `proxy/homelab-root-ca.crt`. Write `proxy/TRUST.md` with exact steps for macOS (Keychain → Always Trust), Windows (`certutil -addstore Root`) and Android (Settings → Security → Install certificate). Ryan does the device clicks. Note: some Android apps (Jellyfin app) ignore user CAs; keep IP access for apps.
6. **Protect the CA key:** add `/var/lib/caddy` from LXC 115 to the nightly config backup (`backup/homelab-config-backup.sh`, which already uploads to Google Drive). Never commit the key.
7. **Homepage:**
   - Change catalog links in `src/app.jsx` to `https://<name>.lan`; keep the IP:port shown in the small address line.
   - `qwen_proxy.py` and `update_proxy.py` accept requests only for known Host/Origin values. Add `home.lan`, or Ask Qwen and the update and power buttons will refuse requests from the new name. Add a test for it.
   - Run `qwen-check.mjs` and `ui-smoke.mjs` against `https://home.lan`.
8. **Verify:** `curl -sI --cacert proxy/homelab-root-ca.crt https://<name>.lan` gives 200/302 for every name. In a browser: Proxmox console, an Open WebUI chat, Jellyfin playback, an Ask Qwen question from `https://home.lan`.
9. **Docs:** update `alerts/SENDERS.md` if anything new alerts, the `diagrams/` network and services pages, and `README.md`.

---

## 2. Weekly hands-off updates (Saturday 7:30 AM)

**Known facts**
- The runner is `updates/homelab_update.py`, installed on Proxmox as `/usr/local/sbin/homelab-update` (install: `python3 updates/install.py`). It already does check and update jobs with a snapshot before each LXC, one guest at a time, health checks, Docker rollback, stop on first failure, an Orange Pi primary-DNS gate, and Uptime Kuma left manual. Homepage's update button uses the same code.
- Saturday is clear of: the Sunday midnight parity check (~7 h), Sunday backup cleanup, SMART tests (never Sunday), the 03:00 nightly review, and the 02:00 cloud backup.
- `homelab-alerts` already sends an immediate alert for a failed update (`updates-failed`). Don't send a second alert for the same failure (`alerts/SENDERS.md`).

**Steps**
1. **Timer on Proxmox:** `homelab-weekly-update.timer`, `OnCalendar=Sat *-*-* 07:30`, `Persistent=false`. Service: run a check job, then an update job, through the runner's existing job commands (verify the names in `main()`; `run-check` / `run-update` today).
2. **Skip guards** (skip with one quiet ntfy, never half-run):
   - a cloud backup or restore test holds `/run/homelab-cloud-backup.lock`
   - Unraid parity check running (`ssh root@192.168.0.4 /usr/local/sbin/mdcmd status`, `mdResyncPos` > 0)
   - an update job is already running
   - nothing pending after the check
3. **Exclusions:** `/etc/homelab-update/weekly-exclude` (one name per line). Start it with anything the runner marks manual. Ask Ryan whether Minecraft or Kasm should be excluded.
4. **Summary:** one ntfy message when done, priority 2: what updated, anything skipped or rolled back, and whether Proxmox needs a reboot for a new kernel (report only; don't reboot). Failures say "alert sent" instead of repeating the alert. Read the topic from `/usr/local/bin/homelab-health-watchdog.sh`, as `alerts/homelab_alerts.py` does.
5. **Homepage Updates page:** show "Automatic updates: Saturdays 7:30 AM · last run <date>: <result>", from the runner's state.
6. **Tests:** unit tests for the guards and the summary text. Then one manual run of the service with a `--dry-run` flag that only checks and prints what would update. Ask Ryan before the first real run, or let the first Saturday be the real run if he agrees.
7. The "What changed" timeline already picks up update logs; confirm the automatic run shows there.

---

## 3. A310 hardware transcoding: verify and benchmark

**Known facts (verify first)**
- Jellyfin runs on Unraid as Docker `Jellyfin` (`jellyfin/jellyfin:latest`), at 192.168.0.17 on br0.
- Two Intel GPUs on Unraid: **A310 = `/dev/dri/renderD129` (card1)**, iGPU HD 630 = `renderD128` (card0). Jellyfin must use the A310.
- The A310 has 4 GB. Ask Qwen's Gemma E4B uses ~3.2 GB while loaded (only during a question). The nightly 12B uses most of it from ~03:00. Transcoding needs a few hundred MB per stream.

**Steps**
1. **Config check, read-only:** `docker inspect Jellyfin` (which `/dev/dri` devices are passed in), Jellyfin's `encoding.xml` (hardware acceleration type QSV or VA-API, device `renderD129`, enabled decode codecs incl. HEVC 10-bit and AV1, HEVC/AV1 encode, tone mapping). Inside the container: `/usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD129`. Report anything pointing at the iGPU or disabled.
2. **Benchmark** with Jellyfin's own ffmpeg (`/usr/lib/jellyfin-ffmpeg/ffmpeg`), holding the A310 lock (rule 5), writing to `/dev/null`:
   - 4K HEVC 10-bit HDR → 1080p H.264 with tone mapping
   - 1080p H.264 → 720p H.264
   - 4K AV1 decode → 1080p H.264
   - 1080p → HEVC encode
   Use a generated test clip or a library file opened read-only. Record fps, then run 1, 2, 3… copies in parallel until one drops below real time (24 fps) to get the stream count. Record GPU engine use (Homepage's GPU panel or `intel_gpu_top`) and VRAM.
3. **Real playback:** with Ryan, play a 4K file on the phone at a low quality setting. Confirm Jellyfin's dashboard shows hardware transcoding and the ffmpeg log uses QSV/VA-API on `renderD129`.
4. **GPU sharing:** test one transcode while an Ask Qwen question runs. Note whether either slows or fails. If VRAM runs out, propose (don't build) a rule such as Ask Qwen waiting while a transcode is active.
5. **Fix** any misconfiguration found in step 1, only with Ryan's OK, since it restarts Jellyfin.
6. **Save** `evals/a310-transcode-<date>/RESULTS.md` (config found, fps table, stream counts, sharing result) and add one line to Homepage's Media page, e.g. "Hardware transcoding: A310 (QSV) · up to N × 4K→1080p".

## Done means

- Every `https://<name>.lan` opens without a warning on Ryan's Mac after the CA is trusted; Ask Qwen and the update buttons work from `https://home.lan`.
- The weekly update timer is installed, its dry run is clean, and Homepage shows the next run.
- The transcoding results file exists with real numbers, and Jellyfin is confirmed on the A310.
- All of it committed and pushed to `n100`, tests passing, no credit lines.
