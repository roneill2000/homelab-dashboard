# ntfy senders (inventory 21 October 2026)

All post to the same private ntfy.sh topic. Before adding an alert, check this list.

| Sender | Where | What it alerts on |
| --- | --- | --- |
| Proxmox notification system | `/etc/pve/notifications.cfg`, webhook target `Ntfy`, matcher `ntfy-matcher` (mode all) | Every Proxmox notification: backup job results, replication, package updates, fencing |
| homelab-health-watchdog | Proxmox `/usr/local/bin/homelab-health-watchdog.sh`, `homelab-watchdog.timer` every 3 min | Primary Pi-hole DNS (.11), secondary Pi-hole DNS on the Orange Pi (.10, also means Pi unreachable), DuckDNS mismatch, WireGuard VM 107, Proxmox root disk ≥85% |
| smart-notify | Proxmox `/usr/local/bin/smart-notify.sh` | Proxmox SMART warnings |
| fix-unraid-mount | Proxmox `/usr/local/bin/fix-unraid-mount.sh`, cron every 5 min | Proxmox → Unraid backup mount went stale and was remounted |
| Unraid notifications | Unraid `/boot/config/plugins/dynamix/notifications/agents/ntfy.sh` (normal, warning, alert on) | Array and parity events, disk usage warning 70% / critical 90%, disk temperature hot 60 °C / max 65 °C, Docker update notices, Unraid notices |
| Scrutiny | Unraid `appdata/scrutiny/config/scrutiny.yaml` `notify.urls` | Drive SMART failures |
| Uptime Kuma | Orange Pi, notification "Ntfy Alert" (default) on all 7 monitors | Proxmox ping, Proxmox UI, Proxmox SMB 445, Unraid ping, Unraid UI, Unraid SMB 445, DuckDNS DNS |
| ddns-update | Orange Pi `/usr/local/bin/ddns-update.sh`, cron every 5 min | Public IP changed, DuckDNS update failed |
| NetAlertX | LXC 102 `/opt/netalertx/data/config/app.conf` (`NTFY_RUN='on_notification'`, `NTFPRCS_INCLUDED_SECTIONS=['new_devices']`) | A device it has never seen joins 192.168.0.0/24. Turned on 21 Oct 2026; the 55 existing devices were marked known and connect/disconnect alerts are off. |
| homelab-alerts | Proxmox `/usr/local/bin/homelab-alerts`, timer every 5 min | Only gaps: stale/missing shares in LXCs, failed systemd units in LXCs, stopped Unraid Docker apps (except the GPU benchmark), Strata or llama-server down while the gaming PC is awake (llama-server only when no game is running), failed update runs, speed tests older than 12 h, Google Drive backup older than 36 h (Proxmox job), Unraid snapshot on Google Drive older than 30 h, config backup (router / Proxmox host / Orange Pi) older than 36 h, Unraid SMART self-test failed or overdue, Homepage data stale, Orange Pi failed units, Uptime Kuma or nebula-sync stopped, Pi SD card ≥85%, Pi ≥75 °C |

Configured but **off**: Speedtest Tracker ntfy (`ntfy_enabled=false`).
Not ntfy: Proxmox cron pings healthchecks.io every 15 min (dead-man alert from that service).

| homelab-digest | Proxmox `homelab-alerts --digest`, `homelab-digest.timer` 07:00 | One morning summary: backups (photos, Proxmox, Unraid, configs), storage, logs, internet, pending updates, SMART problems, active alerts, plus a two-sentence Ask Qwen note. Priority 2 when all good, 3 when something needs attention. Replaces the old "Backups OK" message. |
| homelab-why | Proxmox `homelab-why.service` on :8997 | Answers the **Ask Qwen why** button on homelab-alerts notifications: asks Ask Qwen about that alert and posts "Why: <alert>". Only fires when you tap the button (phone on LAN or WireGuard). |
| homelab-weekly-update | Proxmox homelab-weekly-update.timer, Sat 07:30 | One summary after the weekly update run, or one "skipped" message. Failures also raise the existing updates-failed alert. |

## Ask Qwen why button (failures only, 23 Oct 2026)

Failure alerts carry the button; recoveries and info don't. homelab-why accepts `?key=` (homelab-alerts, cloud backup) or `?title=&detail=` (senders on other hosts).

| Sender | How |
| --- | --- |
| homelab-alerts | `down` alerts, `?key=` |
| homelab-cloud-backup | failures, `?key=cloud-backup` (title + run-log tail in details.json) |
| homelab-health-watchdog | `high` priority (ALERT:) messages |
| smart-notify | always |
| Unraid notifications | warning and alert levels |
| ddns-update (Orange Pi) | DDNS UPDATE FAILED only |
| Proxmox notifications | matcher `ntfy-fail-matcher` (severity error) → webhook `NtfyFail`, title only; other severities stay on `Ntfy` |

Not covered: Uptime Kuma and Scrutiny (no custom action support in their ntfy senders).

## Alert snooze (30 Oct 2026)

Quick actions → **Snooze alerts** (30 min, 2 h, 4 h) on the dashboard. State: LXC 104 `/var/lib/homelab-dashboard/maintenance.json`, read by senders at `GET http://192.168.0.2/api/maintenance` (fails open: no answer = not snoozed).

| Sender | During a snooze |
| --- | --- |
| homelab-alerts | New problems are held and alert on the first run after the snooze if still bad; recoveries still send |
| homelab-health-watchdog | Same (newly failed services are not recorded until the snooze ends) |
| Unraid notifications | Normal and warning notices are dropped (still in Unraid's own list); critical alerts still send |
| Everything else (Uptime Kuma, Scrutiny, Proxmox notifications, NetAlertX, ddns-update) | Unaffected |

Backups of the patched scripts: `homelab-health-watchdog.sh.bak-20261030-snooze` (Proxmox), `ntfy.sh.bak-20261030-snooze` (Unraid agents).

## Tap to open, Snooze button, one notification per problem (30 Oct 2026)

homelab-alerts, the Proxmox watchdog and the Unraid ntfy agent set:

- `Click: homelab://view/<page>`: tapping the notification opens the Android app (v1.6+) on the matching page (homelab-alerts maps keys to pages in `PAGES`; the watchdog opens Services; Unraid opens Storage).
- A **Snooze 2 h** button on problem alerts (`http` action to `POST /api/action/maintenance` with `X-Dashboard-Action: 1`), next to Ask Qwen why. Like Ask Qwen why, it works only from the phone on home Wi-Fi or WireGuard.
- `X-Sequence-ID` from the alert key (homelab-alerts, watchdog): the recovery replaces the problem's notification instead of adding a second one.

Uptime Kuma, Scrutiny and NetAlertX use their built-in ntfy integrations and stay plain. The Android app's own 15-minute problem check is off by default since v1.6 (pref `app_alerts`).
