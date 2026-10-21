#!/bin/bash
set -euo pipefail
if [ -f /etc/systemd/system/memos.service ]; then echo "Memos is already installed; use the documented upgrade procedure." >&2; exit 1; fi
version=0.31.0
archive=$(mktemp)
curl -fL --retry 3 "https://github.com/usememos/memos/releases/download/v${version}/memos_${version}_linux_amd64.tar.gz" -o "$archive"
printf '%s  %s\n' d99bf9de5e947cd41f7f1ae59e1e97d9af933d1bcc2d1316b3ab1ffe0a69e5c0 "$archive" | sha256sum -c -
staging=$(mktemp -d)
tar -xzf "$archive" -C "$staging"
install -m 0755 "$staging/memos" /usr/local/bin/memos
rm -rf "$staging" "$archive"
id memos >/dev/null 2>&1 || useradd --system --home-dir /var/lib/memos --shell /usr/sbin/nologin memos
install -d -o memos -g memos -m 0700 /var/lib/memos
install -d -o root -g memos -m 0750 /etc/secrets
printf '%s\n' '{"key":"ACCESS","accessSetting":{"accessMode":"INSTANCE_ACCESS_MODE_PRIVATE"}}' > /etc/secrets/memos-instance-setting-access.json
chown root:memos /etc/secrets/memos-instance-setting-access.json
chmod 0640 /etc/secrets/memos-instance-setting-access.json
cat > /etc/systemd/system/memos.service <<'UNIT'
[Unit]
Description=Memos private notes
After=network-online.target
Wants=network-online.target
[Service]
User=memos
Group=memos
WorkingDirectory=/var/lib/memos
ExecStart=/usr/local/bin/memos --addr 127.0.0.1 --port 5230 --data /var/lib/memos --instance-url http://192.168.0.18:5230
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/memos
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now memos
sleep 2
systemctl is-active memos
curl -fsS http://127.0.0.1:5230/api/v1/instance/profile
