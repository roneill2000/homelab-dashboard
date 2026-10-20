#!/bin/sh
# Copy this project's tracked files to the n100 share repo as one commit.
# OPERATIONS.md (private operating notes) never leaves ryan-tools.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REMOTE=ssh://root@192.168.0.3/mnt/share/repos/homelab-dashboard
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git clone -q "$REMOTE" "$TMP/share"
cd "$HERE"
git ls-files | grep -vxE 'OPERATIONS.md' > "$TMP/files"
find "$TMP/share" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
rsync -a --files-from="$TMP/files" ./ "$TMP/share/"
cd "$TMP/share"
git add -A
if git diff --cached --quiet; then echo "share already current"; exit 0; fi
git commit -q -m "${1:-sync from ryan-tools $(git -C "$HERE" rev-parse --short HEAD)}"
git push -q origin HEAD:main
ssh -o BatchMode=yes root@192.168.0.3 chown -R roneill /mnt/share/repos/homelab-dashboard
git log --oneline -1
