# Homelab rules for OpenCode

You are working on Ryan's homelab from his Windows gaming PC (192.168.0.12, user Raven).

## Hosts
| Host | Address | How to reach |
| --- | --- | --- |
| Proxmox (n100-proxmox) | 192.168.0.3 | `ssh proxmox` (root). Containers: `pct exec <id> -- <cmd>` |
| Unraid | 192.168.0.4 | `ssh proxmox "ssh root@192.168.0.4 '<cmd>'"` (hop through Proxmox) |
| Homepage dashboard | 192.168.0.2 (LXC 104) | `http://192.168.0.2/api/telemetry`, `/api/storage`, `/api/fleet` (read-only JSON) |
| Open WebUI | 192.168.0.16:3000 (LXC 112) | |
| Jellyfin 12.2 | 192.168.0.17:8096 (Unraid Docker) | |
| FileBrowser | 192.168.0.13:6501 (LXC 102, native) | serves /mnt incl. /mnt/UNRAID (CIFS) |
| Memos | 192.168.0.18:5230 (LXC 113) | |
| Pi-hole | .11 primary, .10 replica (Orange Pi, `ssh pi`) | |

LXCs: 102 utilities, 103 kasm, 104 homepage, 105 speedtest, 106 pihole-secondary, 111 guacamole, 112 open-webui, 113 memos. VMs 100/101/109/110 are off on purpose.

## Start here
For "is anything wrong", read `curl -s http://192.168.0.2/api/fleet` first (mounts, failed units, Docker states, pending updates). Only then drill into hosts.

## Rules
- Read before you change. Say what you will change and why, then wait for approval.
- Before changing an LXC: `pct snapshot <id> pre_<short-reason>`. Never delete snapshots, backups, containers or data.
- Never stop the Unraid array, reboot hosts, or force-unmount without explicit approval.
- Use `pgrep -f '[p]attern'` (bracket trick) so commands never match their own SSH shell.
- Wrap anything touching network mounts in `timeout`; a stale FUSE/CIFS mount can hang a shell.
- Zurg is disabled on purpose (2026-10-20). Don't re-enable it.
- Don't print secrets (API keys, tokens, `api-key.txt`, `webui-secret.txt`).
- `bastion-dev` in ~/.ssh/config is a work system. Never connect to it.
- Keep outputs short; you have a 32K context.
