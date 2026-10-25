#!/bin/bash
# Nightly config backup (00:45, before vzdump and the 02:00 cloud backup, which uploads this folder as tag "configs").
# Router (GL.iNet sysupgrade backup: WireGuard keys, forwards, reservations), Proxmox host config, Orange Pi
# (Uptime Kuma data, nebula-sync, its scripts and crontab). Keeps 14 of each locally. Root-only files.
set -euo pipefail
D=/var/backups/homelab-configs
STAMP=$(date +%Y%m%d)
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)
install -d -m 700 "$D"
fail=()

save() {  # save <name> <command producing a .tgz on stdout>
  local out="$D/$1-$STAMP.tgz"
  if bash -c "$2" > "$out.part" 2>/dev/null && tar -tzf "$out.part" >/dev/null 2>&1 && [ "$(stat -c %s "$out.part")" -gt 1024 ]; then
    chmod 600 "$out.part"; mv "$out.part" "$out"
    ls -1t "$D/$1"-*.tgz | tail -n +15 | xargs -r rm -f
  else
    rm -f "$out.part"; fail+=("$1")
  fi
}

save router "${SSH[*]} root@192.168.0.1 'sysupgrade -b /tmp/homelab-cfg.tgz >/dev/null 2>&1 && cat /tmp/homelab-cfg.tgz; rm -f /tmp/homelab-cfg.tgz'"
save proxmox "tar -czf - --ignore-failed-read --exclude=/root/.config/restic/gdrive.pass /etc/pve /etc/network/interfaces /etc/hosts /etc/fstab /etc/sysctl.d /etc/tmpfiles.d /etc/udev/rules.d /etc/rsyslog.d /etc/logrotate.d /etc/cron.d /etc/systemd/system /usr/local/bin /usr/local/sbin /root/.config /root/.ssh/config 2>/dev/null"
save orangepi "${SSH[*]} root@192.168.0.10 'crontab -l > /tmp/root.crontab 2>/dev/null; tar -czf - --ignore-failed-read /opt/uptime-kuma /opt/nebula-sync /etc/unbound /etc/pihole/pihole.toml /etc/pihole/hosts /etc/default/cpufrequtils /usr/lib/orangepi/orangepi-hardware-optimization /etc/sysctl.d /etc/systemd/system.conf.d /usr/local/bin/ddns-update.sh /usr/local/bin/dns-watchdog.sh /tmp/root.crontab 2>/dev/null; rm -f /tmp/root.crontab'"
save proxy "pct exec 115 -- tar -czf - -C /var/lib/caddy .local/share/caddy /etc/caddy 2>/dev/null"

if [ ${#fail[@]} -eq 0 ]; then date +%s > "$D/last_success"; else echo "failed: ${fail[*]}" >&2; exit 1; fi
