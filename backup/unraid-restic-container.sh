set -e
# Failure alerts: post-backup.sh from the repo, plus the ntfy topic copied from Unraid's own ntfy agent (never committed).
H=/mnt/user/appdata/unraid-restic/hooks; mkdir -p "$H"
[ -f /tmp/unraid-restic-post-backup.sh ] && install -m 755 /tmp/unraid-restic-post-backup.sh "$H/post-backup.sh"
[ -s "$H/ntfy.topic" ] || sed -nE 's/^TOPIC="?([A-Za-z0-9_-]+)"?$/\1/p' /boot/config/plugins/dynamix/notifications/agents/ntfy.sh | head -1 > "$H/ntfy.topic"
chmod 600 "$H/ntfy.topic"; [ -s "$H/ntfy.topic" ] || { echo "no ntfy topic found"; exit 1; }
IMG=ghcr.io/lobaro/restic-backup-docker:latest
docker inspect unraid-restic >/dev/null 2>&1 && { docker exec unraid-restic sh -c 'pgrep -f "[r]estic backup" >/dev/null' && { echo "backup running, aborting"; exit 1; } ; docker stop unraid-restic >/dev/null; docker rename unraid-restic unraid-restic-old; }
docker run -d --name unraid-restic --hostname unraid --restart unless-stopped \
  -e TZ=America/New_York \
  -e RESTIC_REPOSITORY=rclone:gdrive:homelab-restic \
  -e RESTIC_PASSWORD_FILE=/etc/restic/password \
  -e RESTIC_TAG=unraid \
  -e BACKUP_CRON="0 4 * * *" \
  -e BACKUP_SOURCES="/data/appdata /data/system /data/backup/backups/pihole /boot-config" \
  -e RESTIC_JOB_ARGS="--exclude /data/appdata/rclone-cache --exclude /data/appdata/homelab-models --exclude /data/system/docker/docker.img --exclude /boot-config/restic" \
  -e RESTIC_FORGET_ARGS="--tag unraid --host unraid --keep-daily 7 --keep-weekly 4 --keep-monthly 12" \
  -v /mnt/user:/data:ro \
  -v /boot/config:/boot-config:ro \
  -v /boot/config/restic/unraid.pass:/etc/restic/password:ro \
  -v /boot/config/restic/rclone:/root/.config/rclone \
  -v /mnt/user/appdata/unraid-restic/hooks:/hooks:ro \
  "$IMG" >/dev/null
sleep 8
docker logs unraid-restic 2>&1 | tail -4
docker exec unraid-restic sh -c 'date; hostname; env | grep -cE "^RESTIC_PASSWORD="; crontab -l 2>/dev/null | grep -v "^#" | head -2; restic snapshots --tag unraid --compact 2>&1 | tail -4'
docker rm unraid-restic-old >/dev/null && echo "old container removed"
