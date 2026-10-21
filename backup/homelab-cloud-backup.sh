#!/bin/bash
# Encrypted off-site backup to Google Drive with restic (rclone backend). Runs on Proxmox.
# Backs up: photos (Unraid 4TB/Photos via the /mnt/unraid automount, Proxmox /mnt/share/pics)
#           and the newest vzdump archive per guest from the Unraid backup share.
# Media, the Private share and everything else are deliberately excluded (Ryan, 21 Oct 2026).
set -euo pipefail
export RESTIC_REPOSITORY=rclone:gdrive:homelab-restic
export RESTIC_PASSWORD_FILE=/root/.config/restic/gdrive.pass
export RESTIC_CACHE_DIR=/var/cache/restic
STATE=/var/lib/homelab-cloud-backup
DUMP=/mnt/pve/unraid/dump
PHOTOS=(/mnt/unraid/Photos /mnt/share/pics)
mkdir -p "$STATE"
exec > >(tee -a "$STATE/run.log") 2>&1
echo "=== $(date -Is) start"

notify() {  # topic lives only in the watchdog script
  local topic; topic=$(grep -oP '^TOPIC="\K[^"]+' /usr/local/bin/homelab-health-watchdog.sh) || return 0
  curl -s -m 15 -H "Title: $1" -H "Priority: $2" -H "Tags: $3" -d "$4" "https://ntfy.sh/$topic" >/dev/null || true
}
trap 'notify "Cloud backup failed" 4 warning "homelab-cloud-backup stopped at line $LINENO. See $STATE/run.log on Proxmox."' ERR

exec 9>/run/homelab-cloud-backup.lock
flock -n 9 || { echo "already running"; exit 0; }

restic cat config >/dev/null 2>&1 || restic init

# Photos: refuse to run against an empty or missing mount (would look like everything was deleted).
for d in "${PHOTOS[@]}"; do
  timeout 60 test -n "$(timeout 60 ls -A "$d" 2>/dev/null | head -1)" || { echo "photo source missing or empty: $d"; false; }
done
restic backup --tag photos --host homelab "${PHOTOS[@]}"

# Proxmox: newest archive (+ its .log/.notes) per guest.
LIST=$(mktemp)
for id in $(ls "$DUMP" | sed -nE 's/^vzdump-(lxc|qemu)-([0-9]+)-.*\.(tar|vma)\.zst$/\2/p' | sort -u); do
  newest=$(ls -1t "$DUMP"/vzdump-*-"$id"-*.zst | head -1)
  base=${newest%.*.zst}
  for f in "$newest" "$newest.notes" "$base.log"; do [ -f "$f" ] && echo "$f"; done
done > "$LIST"
test -s "$LIST"
restic backup --tag proxmox --host homelab --files-from-verbatim "$LIST"
rm -f "$LIST"

restic forget --tag photos --host homelab --keep-daily 7 --keep-weekly 4 --keep-monthly 12
restic forget --tag proxmox --host homelab --keep-daily 7 --keep-weekly 4

if [ "$(date +%u)" = 7 ]; then          # Sundays: reclaim space and prove the data is readable
  restic prune
  restic check --read-data-subset=2%
fi

date +%s > "$STATE/last_success"
echo "=== $(date -Is) done"
