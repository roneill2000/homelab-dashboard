# Memos

Ryan's notes at http://192.168.0.18:5230, linked from Homepage. Installed 19 October 2026.

- Proxmox `.3`, unprivileged Debian 13 LXC113; 1 CPU, 1GiB RAM, 256MiB swap, 8GiB disk; starts with host.
- Memos **0.31.0** native binary, SHA256 pinned in `install.sh`; system user `memos`.
- Data: `/var/lib/memos` (SQLite database, WAL and attachments).
- `memos.service`: authenticated upstream on **127.0.0.1:5231**.
- `memos-lan.service`: single-user auto-login proxy on **0.0.0.0:5230**, as explicitly requested by Ryan. Anyone reaching this LAN URL has the owner's read/write/admin access, including to notes labeled Private. No public port forwarding was configured.
- Owner account remains internal. Proxy credentials in `/etc/memos-auto-login.json`, mode0600, never committed. The proxy renews access tokens using refresh cookies and falls back to password sign-in. Changing the internal password requires updating that file and restarting the proxy.
- Registration remains disabled; private upstream access policy in `/etc/secrets/memos-instance-setting-*.json`. These files are authoritative on restart.

## Use

Open the URL to write notes without signing in. Moe Memos Android supports0.31.0. Its Host field is the same URL, with no `/api/v1` suffix. A dedicated verified, non-expiring PAT named **Moe Memos Android** was created; the host and token are saved privately in Ryan's Downloads as `MOE_MEMOS_ANDROID.txt`. It is not stored in this repository. Phone access requires home LAN or WireGuard. The proxy grants owner access independently of client tokens, so revoking a PAT does not remove the intentionally open LAN access.

## Backups and recovery

LXC113 is included in existing Proxmox job `backup-e08b168e-2331`, 03:00 America/New_York, snapshot/zstd to `unraid`, retaining3 backups. Previous VM membership was preserved. First archive18:58EDT; final configuration archive `/mnt/pve/unraid/dump/vzdump-lxc-113-2026_10_19-19_04_15.tar.zst` (~281MB). Its SQLite database and WAL were extracted to a temporary directory; SQLite integrity_check returned ok. A full isolated restore passed on20October using the03:15:59 archive: both services active, owner auto-login HTTP200, SQLite integrity_check ok. Temporary LXC9113 had no network interface and onboot disabled; it was shut down and removed after verification. Production113 remained running.

Restore the **whole container** to a spare ID and isolated/test network first. It contains data, both systemd units, application binary and private proxy configuration. To restore only notes, stop Memos, restore the complete `/var/lib/memos` directory with ownership `memos:memos`, then start it. Preserve SQLite WAL alongside the database if restoring raw snapshot files. Never copy only a live SQLite database without its WAL; use SQLite's backup API for online database-only exports and separately back up attachments.

For upgrades, back up first, deliberately select a release, verify its published digest and Moe Memos compatibility, then update the binary and re-run the LAN checks. The proxy's Connect protobuf refresh response is matched to0.31.0; verify before changing versions. `install.sh` is first-install only and refuses an existing service.

## Verification

`verify_lan.py` runs inside113: no-credential create/read/edit/delete, persistence through both service restarts, and rejected cross-origin mutation. `verify.py` checks the internal authenticated service and closed registration; it reads the private owner credential from Downloads locally. `../homelab-dashboard/memos-check.mjs` uses an isolated browser through task-owned SSH forwards8818/8802: fresh-browser automatic sign-in,448px and1440px layouts, Homepage link and online indicator. No Android device was controlled; its PAT was verified directly against the authenticated upstream.

Sources: [release](https://github.com/usememos/memos/releases/tag/v0.31.0), [binary deployment](https://usememos.com/docs/deploy/binary), [backup](https://usememos.com/docs/operations/backup-restore), [Moe Memos compatibility](https://memos.moe/faq/).


## Proxy transport fix — 20 October 2026

If the upstream response is interrupted after headers have been sent, the proxy now closes that response instead of appending a second HTTP502 response. Failures before headers still produce502. Two transport regression tests passed, and the deployed owner API returned200. Only memos-lan was restarted. Rollback source: `/usr/local/bin/memos-lan-proxy.py.bak-20261020T100047` inside113.
