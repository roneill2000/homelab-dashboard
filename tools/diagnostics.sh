#!/bin/bash
# Forced SSH command: no user-supplied arguments reach a shell or program.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
host=${1:?host required}
case "$host" in proxmox|unraid) ;; *) exit 64;; esac
case "${SSH_ORIGINAL_COMMAND:-}" in
 summary)
  uptime
  free -m
  if [ "$host" = proxmox ]; then df -h / /mnt/share; else df -h /mnt/cache /mnt/disk1 /mnt/user; fi
  ;;
 storage)
  lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
  if [ "$host" = proxmox ]; then df -h / /mnt/share; else df -h /mnt/cache /mnt/disk1 /mnt/user; cat /proc/mdstat; fi
  ;;
 services)
  if [ "$host" = proxmox ]; then systemctl --failed --no-pager --plain; else docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'; fi
  ;;
 guests)
  if [ "$host" = proxmox ]; then pct list; qm list; else docker ps -a --format '{{.Names}}\t{{.Status}}'; fi
  ;;
 warnings)
  # Repeats are collapsed to one line with a count (most frequent first, 25 max)
  # so the result stays small enough for a model's context window.
  # awk reads all input, so no early-exit SIGPIPE trips pipefail.
  if [ "$host" = proxmox ]; then
   journalctl --since '-24 hours' -p warning --no-pager -n 400 -o short-iso |
    sed -E 's/^[^ ]+ [^ ]+ //; s/\[[0-9]+\]//' | sort | uniq -c | sort -rn | awk 'NR<=25{print substr($0,1,220)}'
  else
   # awk succeeds for a valid empty match; pipefail preserves source-read errors.
   tail -n 2000 /var/log/syslog | awk 'tolower($0) ~ /warning|error|failed|critical|out of memory|no space/' |
    sed -E 's/^[A-Z][a-z]{2} +[0-9]+ [0-9:]+ [^ ]+ //; s/\[[0-9]+\]//' | sort | uniq -c | sort -rn | awk 'NR<=25{print substr($0,1,220)}'
  fi
  ;;
 *) printf '%s\n' 'Denied: supported commands are summary, storage, services, guests, warnings.' >&2; exit 64;;
esac
