#!/bin/sh
# Remount fstab CIFS/NFS mounts that are stale, hung or missing.
# A mount must fail two checks 15 s apart before it is touched.
set -u
FSTAB=${FSTAB:-/etc/fstab}
mounted() { awk -v m="$1" '$2==m{f=1} END{exit !f}' /proc/mounts; }
readable() { timeout 20 stat -t "$1" >/dev/null 2>&1 && timeout 20 ls "$1" >/dev/null 2>&1; }
awk '$1 !~ /^#/ && $3 ~ /^(cifs|smb3|nfs|nfs4)$/ {print $2}' "$FSTAB" | while read -r m; do
  if mounted "$m"; then
    readable "$m" && continue
    sleep 15
    readable "$m" && continue
    logger -t mount-heal "$m unreadable twice; remounting"
    umount -l "$m" || { logger -t mount-heal "$m unmount failed"; continue; }
  else
    logger -t mount-heal "$m not mounted; mounting"
  fi
  if timeout 60 mount -T "$FSTAB" "$m"; then logger -t mount-heal "$m mounted"
  else logger -t mount-heal "$m mount failed; retrying next run"; fi
done
