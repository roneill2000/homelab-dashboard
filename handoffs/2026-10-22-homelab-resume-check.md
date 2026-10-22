# Homelab resume check — 2026-10-22

**Task:** resume backups, Qwen and operations work. **Audience:** Ryan only.
**Workstream:** homelab-dashboard; personal repo, n100 remote only.
**Earlier:** [Backups, Qwen and ops](2026-10-22-homelab-backups-qwen-ops.md).

## Working state
- Resume found `~/homelab`, branch `main`, clean at `cc81ceb`, matching local `n100/main`; remote head was not fetched. This brief is the only new change.
- `cc81ceb` changes repeated Ask Qwen why taps from HTTP 429 to 202. Source: git show; deployment not checked.
- `OPERATIONS.md` stays private and ignored.

## Verified this session
- SSH to Proxmox and onward to Unraid works. Both rclone gdrive configs contain a custom client ID and secret; values were not printed.
- Proxmox cloud-backup service: inactive, Result=success, ExecMainStatus=0. Last-success marker: 2026-10-22 11:53:36 UTC.
- Config-backup last-success marker: 2026-10-22 17:29:05 UTC.
- Restic snapshot listing succeeds from both Proxmox and the Unraid container.
- Latest snapshots read from Proxmox: photos `5ba2015b` (02:00 EDT), proxmox `825bd0d6` (03:25 EDT), unraid `1ab9532e` (08:08 EDT), configs `b9d0d387` (13:29 EDT), all October 22. Unraid independently lists the same unraid snapshot.
- Source: bounded SSH configuration-presence checks, systemctl show, last-success files, and restic snapshots --json on October 22. No configuration changed or backup started.

## Open and next step
- Complete Google authentication migration on both hosts, then verify a new backup snapshot from each. The earlier brief proposes rclone's shared client; this session only checked current state.
- Google sign-in requires Ryan. Preserve working configs until replacement authorization succeeds.
- Testing-mode refresh-token expiry is documented at https://developers.google.com/identity/protocols/oauth2/ ; shared-client setup is documented at https://rclone.org/drive/ .
- October 28 expiry and current Google app publishing status remain inherited from the earlier brief, not independently verified.
- Phone button behavior, SMART test execution, diagrams review, Apple TV Wi-Fi block, password-manager copy and audit leftovers remain open as recorded in the earlier brief.
