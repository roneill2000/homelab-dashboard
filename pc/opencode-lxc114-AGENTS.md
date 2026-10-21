# Homelab rules for OpenCode

You run in LXC 114 (opencode, 192.168.0.19) on Proxmox. Models come from the gaming PC router (192.168.0.12); if the PC is off, say so and stop.

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
- Keep outputs short; you have a 32K context.

## IP map (from Homepage, live)

| IP | Name | Role | URL |
| --- | --- | --- | --- |
| 192.168.0.1 | GL-BE9300 Router | Gateway | http://192.168.0.1 |
| 192.168.0.2 | Homelab Command | LXC 104 | http://192.168.0.2 |
| 192.168.0.3 | Proxmox VE 8 | Hypervisor | https://192.168.0.3:8006 |
| 192.168.0.4 | Unraid NAS | Storage | http://192.168.0.4 |
| 192.168.0.6 | Guacamole | LXC 111 | http://192.168.0.6:8080/guacamole/ |
| 192.168.0.10 | Orange Pi Zero 2W | Replica DNS | http://192.168.0.10/admin |
| 192.168.0.11 | Pi-hole Primary | LXC 106 | http://192.168.0.11/admin |
| 192.168.0.13 | Utilities Stack | LXC 102 | http://192.168.0.13:8989 |
| 192.168.0.14 | Speedtest Tracker | LXC 105 | http://192.168.0.14 |
| 192.168.0.15 | Kasm Workspaces | LXC 103 | https://192.168.0.15 |
| 192.168.0.16 | Open WebUI | LXC 112 | http://192.168.0.16:3000 |
| 192.168.0.17 | Jellyfin Server | Arc A310 br0 | http://192.168.0.17:8096 |
| 192.168.0.18 | Memos | LXC 113 | http://192.168.0.18:5230 |
| 192.168.0.19 | OpenCode | LXC 114 | http://192.168.0.19:4096 |
| 192.168.0.25 | Minecraft Server | Unraid br0 | http://192.168.0.4/Docker |
| 192.168.0.30 | WireGuard Gateway | VM 107 | http://192.168.0.3:8999 |
| 192.168.0.254 | Macvlan Shim | Unraid Route | http://192.168.0.4 |

## Containers on Proxmox

- 100 Kali (VM, stopped)
- 101 ubuntu (VM, stopped)
- 102 utilities (LXC, running)
- 103 kasm (LXC, running)
- 104 homepage (LXC, running)
- 105 speedtest-tracker (LXC, running)
- 106 pihole-secondary (LXC, running)
- 107 Wireguard (VM, running)
- 109 VM 109 (VM, stopped)
- 110 Windows (VM, stopped)
- 111 apache-guacamole (LXC, running)
- 112 open-webui (LXC, running)
- 113 memos (LXC, running)
- 114 opencode (LXC, running)

## Unraid Docker

- itzg-minecraft-server
- Jellyfin
- Scrutiny
- Glances
- Audiobookshelf

Other: WireGuard VPN range 10.0.0.0/24 (VM 107 gateway, .30). Gaming PC 192.168.0.12 (RTX 5070 Ti, llama.cpp router on :8080, often off). Shares: Proxmox /mnt/share (SMB n100), Unraid array /mnt/disk1, cache /mnt/cache.
