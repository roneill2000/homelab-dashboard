#!/bin/sh
# unraid-restic post-backup hook: $1 is restic's exit code. A failure goes to the homelab ntfy topic right away,
# with the Ask AI button, instead of waiting 30 h for homelab-alerts to call the snapshot stale.
# Installed at /mnt/user/appdata/unraid-restic/hooks (mounted as /hooks); the topic lives beside it in ntfy.topic.
rc="$1"
[ "$rc" = 0 ] && exit 0
topic=$(cat /hooks/ntfy.topic 2>/dev/null)
[ -n "$topic" ] || { echo "post-backup: no ntfy topic, alert not sent"; exit 0; }
err=$(grep -E 'Fatal|[Ee]rror' /var/log/backup-last.log 2>/dev/null | tail -3 | cut -c1-300)
msg="Unraid's Google Drive backup failed (restic exit $rc).
${err:-See /var/log/backup-last.log in the unraid-restic container.}"
enc() { printf %s "$1" | od -An -tx1 | tr -d ' \n' | sed 's/../%&/g'; }
why="http://192.168.0.3:8997/why?title=$(enc 'Unraid cloud backup failed')&detail=$(enc "$msg")"
curl -s -m 15 -H "Title: Unraid cloud backup failed" -H "Priority: 4" -H "Tags: warning" \
  -H "Actions: http, Ask AI, $why, method=POST, clear=true" -d "$msg" "https://ntfy.sh/$topic" >/dev/null \
  && echo "post-backup: failure alert sent" || echo "post-backup: ntfy send failed"
exit 0
