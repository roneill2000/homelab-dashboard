set -e
IMG=ghcr.io/lobaro/restic-backup-docker:latest
docker inspect unraid-restic >/dev/null 2>&1 && { docker exec unraid-restic sh -c 'pgrep -f "restic backup" >/dev/null' && { echo "backup running, aborting"; exit 1; } ; docker stop unraid-restic >/dev/null; docker rename unraid-restic unraid-restic-old; }
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
  "$IMG" >/dev/null
sleep 8
docker logs unraid-restic 2>&1 | tail -4
docker exec unraid-restic sh -c 'date; hostname; env | grep -cE "^RESTIC_PASSWORD="; crontab -l 2>/dev/null | grep -v "^#" | head -2; restic snapshots --tag unraid --compact 2>&1 | tail -4'
docker rm unraid-restic-old >/dev/null && echo "old container removed"
