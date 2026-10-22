# Homelab: backups, Ask Qwen, ops features — 2026-10-22

**Task:** harden and extend Ryan's personal homelab and its Homepage dashboard. **Audience:** Ryan only.
**Workstream:** homelab-dashboard (personal; repo `~/homelab`, remote `n100` only, never ryan-tools or sevenai).

## Working state
- `~/homelab`, branch `main`, history rewritten on n100 at 13:44/14:00 by another session (trailers → Antigravity); this brief and the 429 fix were dropped and re-applied, clean tree (git, 22 Oct ~13:45).
- `fcb547e diagrams: …` was pushed by agy from `PLAN-DIAGRAM.md`; not reviewed yet.
- `OPERATIONS.md` is private notes, gitignored; never commit it.

## Settled (with source)
- Cloud backups (restic → Google Drive, one repo, tags photos/proxmox/unraid/configs) all have snapshots; Unraid container `unraid-restic` runs 04:00 local, tag-scoped forget. Source: `restic snapshots`, `backup/unraid-restic-container.sh`.
- Nightly config backup 00:45 (router sysupgrade, Proxmox host, Orange Pi Kuma/nebula-sync) → `/var/backups/homelab-configs`, uploaded as tag `configs`. Source: `backup/homelab-config-backup.sh`, snapshot `b9d0d387`.
- Ask Qwen: 27B on gaming PC when on, else A310 (Qwen3.5-4B, SYCL built ahead-of-time for acm-g11, 6K window, 256-token thinking cap; nightly review keeps 768). Live: 27B 84 s, A310 51 s. Source: `LOCAL-LLM-NOTES.md`, `evals/`.
- A310 limits are hardware (4 GB VRAM, 64-bit bus, no Resizable BAR on the HP board); smaller quants and speculative decoding don't help. Source: `evals/a310-backends-2026-10-22/RESULTS.md`.
- Benchmarks: 27B 8/10, A310 4–6/10. Source: `evals/ask-qwen-2026-10-22/RESULTS.md`, `THINK-CAP.md`.
- New ntfy senders: 07:00 digest, "Ask Qwen why" button (homelab-why :8997), SMART failed/overdue, config backup stale, Unraid backup stale. Source: `alerts/SENDERS.md`.
- SMART scheduler on Unraid 05:10 daily: extended every 90 d on HDDs, short every 30 d on SSD, skips Sunday/parity. First HDD extended tests start Thu 23 Oct 05:10. disk1 (sdc) has 1 CRC error. Source: `smart/homelab_smart.py`, `/api/fleet` smart.
- Syslog: Proxmox's own journal now monitored; Pi-hole log names fixed (orangepi.log / pihole-primary.log). Source: `syslog/`.
- "Bedroom-2" DHCP churn = Apple TV sleep behaviour (wired drops every 60 s only while asleep); harmless. Router Wi-Fi block on `c4:f7:c1:3d:1a:86` is runtime-only and still on until router reboot.

## Decisions Ryan made
- Homelab is personal: push to n100 only; Ryan pushes to his own GitHub.
- Keep Ask Qwen at 6K; route to 27B; 256 thinking cap for Ask Qwen.
- OAuth publishing "is whatever"; rate limiting is acceptable.

## Open
- **Google OAuth app in Testing mode: token expires ~28 Oct and backups then stop.** Keep the custom client (rclone's shared client is retiring during 2026, per rclone.org/drive; my earlier switch-back advice was wrong, corrected by AUDIT-TERRA-2026-10-22). Settle: publish the app (needs a home page + privacy URL), then `rclone config reconnect gdrive:` on Proxmox and Unraid and confirm a new snapshot from each.
- Did the "Ask Qwen why" button work from Ryan's phone? Settle: Ryan taps it on the test alert with WireGuard on.
- agy's diagrams (`diagrams/`) unreviewed. Settle: check against `PLAN-DIAGRAM.md` Step 4.
- Remove or keep the Apple TV Wi-Fi block (`hostapd_cli -p /var/run/hostapd-wifiN deny_acl DEL_MAC …`).
- Restic password still only on Proxmox (`/root/.config/restic/gdrive.pass`); Ryan to save it in a password manager.
- Audit leftovers: SSH passwords off, read-only PVE token, MariaDB socket in LXC 102, Proxmox firewall. Source: `AUDIT-NETWORK-2026-10-21.md`.

## Files that matter
- `nightly/qwen_service.py`, `nightly/runner.py`, `nightly/install_qwen.py`, `nightly/install_runner.py` — Ask Qwen and A310 container.
- `evals/a310-backends-2026-10-22/build_aot.sh` — rebuild `homelab/llama-sycl-a310:b11804` after a llama.cpp update.
- `alerts/homelab_alerts.py` (+ `.service/.timer`) — checks, digest, why server; deploy by atomic copy to `/usr/local/bin/homelab-alerts`.
- `backup/`, `smart/install.sh`, `updates/install.py`, `deploy.py` (build with `npm run build` first).

## Next step
Before 28 Oct: put up a minimal home page + privacy page Ryan controls, publish the Google OAuth app, reconnect rclone on both hosts, then run one manual backup each and confirm a new snapshot. Then pick from AUDIT-TERRA-2026-10-22 (auth on dashboard actions, SSH/firewall, restore checks).

## Unverified
- Ryan's phone receiving/acting on the why button; Thursday's SMART tests actually starting (check `/mnt/disk1/.homelab-smart/schedule.log`).
