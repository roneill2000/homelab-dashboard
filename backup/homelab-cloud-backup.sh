#!/bin/bash
# Encrypted off-site backup to Google Drive with restic (rclone backend). Runs on Proxmox.
# Backs up: photos (Unraid 4TB/Photos via the /mnt/unraid automount, Proxmox /mnt/share/pics)
#           and the newest vzdump archive per guest from the Unraid backup share.
# Media, the Private share and everything else are deliberately excluded (Ryan, 21 Oct 2026).
set -euo pipefail
export RESTIC_REPOSITORY=rclone:gdrive:homelab-restic
export RESTIC_PASSWORD_FILE=/root/.config/restic/gdrive.pass
export RESTIC_CACHE_DIR=/var/cache/restic
# Google Drive is slow at creating many small files: 64 MiB packs (4x fewer files) and more parallel connections.
export RESTIC_PACK_SIZE=64
export RCLONE_DRIVE_CHUNK_SIZE=64M   # one request per 64 MiB pack instead of 8 MiB slices
RCLONE_OPTS=(-o rclone.connections=8)
STATE=/var/lib/homelab-cloud-backup
DUMP=/mnt/pve/unraid/dump
PHOTOS=(/mnt/unraid/Photos /mnt/share/pics)
mkdir -p "$STATE"
exec > >(tee -a "$STATE/run.log") 2>&1
echo "=== $(date -Is) start"

export RESTIC_PROGRESS_FPS=0.2   # one JSON status line every 5 s
backup_tagged() {  # backup_tagged <tag> <restic args...>: keeps $STATE/progress.json current for Homepage
  local tag=$1; shift
  echo "$tag" > "$STATE/phase"
  restic backup --json "$@" | while IFS= read -r line; do
    case "$line" in
      *'"message_type":"status"'*) printf '%s\n' "$line" > "$STATE/progress.json.tmp" && mv -f "$STATE/progress.json.tmp" "$STATE/progress.json" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done
}

notify() {  # topic lives only in the watchdog script
  local topic; topic=$(grep -oP '^TOPIC="\K[^"]+' /usr/local/bin/homelab-health-watchdog.sh) || return 0
  curl -s -m 15 -H "Title: $1" -H "Priority: $2" -H "Tags: $3" -d "$4" "https://ntfy.sh/$topic" >/dev/null || true
}
trap 'notify "Cloud backup failed" 4 warning "homelab-cloud-backup stopped at line $LINENO. See $STATE/run.log on Proxmox."' ERR

exec 9>/run/homelab-cloud-backup.lock
flock -n 9 || { echo "already running"; exit 0; }

# Never create a repository here: an empty or wrong destination must fail loudly, not start a fresh repo.
# First-time setup is `restic init` by hand, then save its id to $REPO_ID (Terra review, 22 Oct 2026).
REPO_ID=/root/.config/restic/gdrive.repo-id
ID=$(restic cat config --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])') \
  || { notify "Cloud backup failed" 4 warning "Google Drive repository unreadable (missing, wrong password, or Drive sign-in expired). Nothing was uploaded."; echo "Google Drive repository unreadable (missing, wrong password, or Drive sign-in expired). Nothing was uploaded."; exit 1; }
[ "$ID" = "$(cat "$REPO_ID")" ] || { notify "Cloud backup failed" 4 warning "Google Drive repository id $ID is not the expected one. Nothing was uploaded."; echo "Google Drive repository id $ID is not the expected one. Nothing was uploaded."; exit 1; }

# Clear locks left by a killed restic (e.g. an interrupted restore test). Without --remove-all this removes
# only stale locks, never one held by a live process. A leftover lock stopped forget on 23 Oct 2026.
restic unlock

# Full speed for the very first upload; after that cap at ~200 Mbps (24414 KiB/s) so ~100 Mbps of the
# 300 Mbps uplink stays free (Ryan, 21 Oct 2026).
LIMIT=()
restic snapshots --tag photos --json 2>/dev/null | grep -q '"id"' && LIMIT=(--limit-upload 24414)

# Photos: refuse to run against an empty or missing mount (would look like everything was deleted).
for d in "${PHOTOS[@]}"; do
  timeout 60 test -n "$(timeout 60 ls -A "$d" 2>/dev/null | head -1)" || { echo "photo source missing or empty: $d"; false; }
done
backup_tagged photos "${RCLONE_OPTS[@]}" "${LIMIT[@]}" --ignore-inode --tag photos --host homelab "${PHOTOS[@]}"

# Proxmox: newest archive (+ its .log/.notes) per guest.
LIST=$(mktemp)
for id in $(ls "$DUMP" | sed -nE 's/^vzdump-(lxc|qemu)-([0-9]+)-.*\.(tar|vma)\.zst$/\2/p' | sort -u); do
  newest=$(ls -1t "$DUMP"/vzdump-*-"$id"-*.zst | head -1)
  base=${newest%.*.zst}
  for f in "$newest" "$newest.notes" "$base.log"; do [ -f "$f" ] && echo "$f"; done
done > "$LIST"
test -s "$LIST"
backup_tagged proxmox "${RCLONE_OPTS[@]}" "${LIMIT[@]}" --tag proxmox --host homelab --files-from-verbatim "$LIST"
rm -f "$LIST"

# Configs: router, Proxmox host and Orange Pi (written 00:45 by homelab-config-backup). Small, so no upload limit.
[ -d /var/backups/homelab-configs ] && backup_tagged configs "${RCLONE_OPTS[@]}" --tag configs --host homelab /var/backups/homelab-configs

# Group by tag, not by path: each night's Proxmox file list differs, so path grouping never expired one.
restic forget --group-by host,tags --tag photos --host homelab --keep-daily 7 --keep-weekly 4 --keep-monthly 12
restic forget --group-by host,tags --tag proxmox --host homelab --keep-daily 7 --keep-weekly 4
restic forget --group-by host,tags --tag configs --host homelab --keep-daily 7 --keep-weekly 4 --keep-monthly 12

if [ "$(date +%u)" = 7 ]; then          # Sundays: reclaim space and prove the data is readable
  restic prune "${LIMIT[@]}"
  restic check --read-data-subset=2%
fi

rm -f "$STATE/progress.json" "$STATE/phase"
date +%s > "$STATE/last_success"
echo "=== $(date -Is) done"
