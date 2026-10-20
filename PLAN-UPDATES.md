# Update button — plan (20 October 2026)

Ryan's choices: running Proxmox LXCs plus Unraid Docker containers; snapshot first, one guest at a time, stop on first failure, no auto-reboot.

## Shape

- **Homepage** gets an Updates panel. "Check" lists pending package/image updates per guest (read-only). "Update all" starts one job; each row shows its state (queued, snapshot, updating, health check, done, failed, skipped).
- **Proxmox side:** a fixed-command script on PVE, reached through a new `restrict`ed SSH key with a forced command (same pattern as the diagnostics broker). The dashboard never gets a general root shell. Per LXC: `pct snapshot` named `pre-update-<stamp>`, then `apt-get update && apt-get -y upgrade` via `pct exec`, then the existing service probe. A changed `/var/run/reboot-required` is reported, not acted on.
- **Unraid side:** pull each container's image, recreate from its existing template only when the digest changed, and wait for running/healthy. Old image is kept for rollback.
- **Ordering:** infrastructure last (Pi-hole secondary before primary, Homepage 104 last). Stopped guests and templates are skipped. A run refuses to start while backup, parity, mover or Qwen is active.
- **Guarding:** the same-origin action guard from `0090f1a`, plus a confirmation dialog. One run at a time (file lock). Logs are kept private on the host.

## Check before building

1. Each LXC's rootfs storage type (`pct snapshot` needs LVM-thin, ZFS or similar; plain directory storage cannot snapshot).
2. Which Unraid containers use templates the script can recreate from, and which have no health check.
3. Whether any LXC runs a non-Debian distro (Alpine uses `apk`).

Estimate: about 3–4 hours once on the home LAN, including tests and one supervised live run on a low-risk LXC.
