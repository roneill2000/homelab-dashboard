# Plan: homelab diagrams

For agy. Produce four diagrams of Ryan's homelab from live, read-only facts, saved as source plus SVG in this repo.

## Output

```
diagrams/
  inventory.json          # single source of truth, every diagram is drawn from it
  01-network.mmd / .svg   # physical boxes, IPs, links, VPN
  02-services.mmd / .svg  # what runs where (LXCs, VMs, Docker apps, ports)
  03-data-backup.mmd/.svg # storage, mounts, local and cloud backups
  04-ops.mmd / .svg       # monitoring, alerts (ntfy), dashboards, local AI
  README.md               # how to regenerate, date of the inventory, open questions
```

- Mermaid (`flowchart`) for all four. Render with `npx -y @mermaid-js/mermaid-cli@11.4.2 -i X.mmd -o X.svg -b transparent`.
- One diagram per file, at most ~25 nodes each. Split rather than cram.
- Label nodes `Name<br/>IP · role` (for example `Pi-hole primary<br/>.11 · LXC 106`). Use the last octet after the first mention of 192.168.0.0/24.
- Group with subgraphs per physical box: Router, Proxmox, Unraid, Orange Pi, Gaming PC, Phone/Laptop (VPN).
- Edge labels say what flows (`DNS`, `SMB /mnt/user`, `restic → Google Drive`, `ntfy`), never "connects to".

## Rules

1. **Read-only.** No config changes, restarts, installs or package updates anywhere. GET requests and `cat`/`ls`/`ip`/`pct list`-style commands only.
2. **No secrets in any file.** No tokens, passwords, API keys, the ntfy topic, OAuth data, WireGuard keys, MAC addresses of personal devices. IPs and hostnames are fine.
3. **Verify, don't copy.** The facts below are a starting point from 22 Oct 2026. Confirm each one live; if live data disagrees, use live data and list the difference in `diagrams/README.md`. Some repo docs are stale (for example `pc/opencode-lxc114-AGENTS.md` still says Proxmox VE 8; it is 9.2).
4. **Missing data stays missing.** If something can't be confirmed, draw it dashed and label it `unverified`. Never guess a port, IP or link.
5. **Git:** work in your own clone, write files as UTF-8 (Python: `encoding='utf-8'` on every read and write), commit, and push to the `n100` remote only. Never edit the share's working tree directly, never push to any other remote, never run `deploy.py`.

## Step 1: collect the inventory (about 20 min)

Pull from these, in this order. Save raw results under `diagrams/raw/` (gitignored) and the cleaned result as `diagrams/inventory.json`.

| Source | How | Gives you |
| --- | --- | --- |
| Homepage APIs | `curl http://192.168.0.2/api/telemetry`, `/api/fleet`, `/api/storage?window=900` | Hosts, guests, Docker states, mounts, Pi-holes, backups, cloud backup sources, network counters |
| Proxmox | `ssh proxmox "pct list; qm list; pvesh get /nodes/n100-proxmox/lxc --output-format json"` and `pct config <id>` for IP/hostname/mounts | LXCs, VMs, IPs, bind mounts |
| Unraid | `ssh proxmox "ssh root@192.168.0.4 'docker ps -a --format \"{{.Names}}|{{.Ports}}|{{.Networks}}\"'"` | Docker apps, br0 IPs (Jellyfin .17, Minecraft .25), ports |
| Orange Pi | `ssh proxmox "ssh root@192.168.0.10 'docker ps --format \"{{.Names}}\"; systemctl list-units --type=service --state=running --no-legend | head -40'"` | Pi-hole replica, Uptime Kuma, nebula-sync |
| Router | `ssh root@192.168.0.1 "cat /tmp/dhcp.leases | wc -l; uci show network | grep -E 'ipaddr|netmask'"` (counts only, no MACs) | LAN, IoT and guest subnets |
| Repo docs | `alerts/SENDERS.md`, `syslog/README.md`, `backup/`, `LOCAL-LLM-NOTES.md`, `private/AUDIT-NETWORK-2026-10-21.md (local only)` | Alert senders, log flow, backup design, AI setup |

`inventory.json` shape:

```json
{
  "collected_at": "ISO time",
  "hosts":    [{"id": "proxmox", "name": "...", "ip": "...", "kind": "hypervisor|nas|sbc|router|pc", "hardware": "..."}],
  "guests":   [{"id": "lxc-104", "host": "proxmox", "name": "homepage", "ip": "...", "kind": "lxc|vm|docker", "ports": [], "status": "running|stopped"}],
  "links":    [{"from": "...", "to": "...", "what": "DNS|SMB|NFS|restic|syslog|ntfy|HTTP|WireGuard", "verified": true}],
  "storage":  [{"id": "...", "host": "...", "size": "...", "role": "..."}],
  "backups":  [{"what": "...", "from": "...", "to": "...", "schedule": "...", "retention": "..."}],
  "unverified": ["anything you could not confirm"]
}
```

## Step 2: starting facts to verify

**Network:** GL.iNet Flint 3 (GL-BE9300) router .1, Comcast 2.5 Gb. WireGuard VM 107 at .30, VPN range 10.0.0.0/24. Pi-hole primary .11 (LXC 106, hostname still `pihole-secondary`), replica .10 (Orange Pi), synced by nebula-sync. Separate IoT and guest networks on the router.

**Proxmox (.3, N100, PVE 9.2):** LXCs 102 utilities (.13: arr stack, SABnzbd, FileBrowser, NetAlertX), 103 kasm (.15, usually stopped), 104 homepage (.2), 105 speedtest (.14), 106 pihole (.11), 111 guacamole (.6), 112 open-webui (.16), 113 memos (.18), 114 opencode (.19). VM 107 WireGuard. Stopped VMs 100, 101, 109, 110: show them grey, labelled stopped.

**Unraid (.4, i7-7700T, 7.3.2):** array ~4 TB (68% used), 256 GB cache. Arc A310 (Qwen3.5-4B for Ask Qwen and the nightly review, Jellyfin transcoding). Docker: Jellyfin (.17), Minecraft (.25), Audiobookshelf, Glances, Scrutiny, unraid-restic, zurg (stopped on purpose).

**Gaming PC (.12, often off):** RTX 5070 Ti, llama.cpp router on :8080 serving qwen3.8-27b, qwen3.6-35b-moe, gemma-4-26b-a4b to Open WebUI and OpenCode. Wake on LAN and sleep buttons on Homepage.

**Backups:** Proxmox vzdump 01:00 → Unraid share. 02:00 restic from Proxmox → Google Drive (photos + newest vzdumps). 04:00 restic from Unraid (`unraid-restic`) → same Drive repo (appdata, system, Pi-hole backups, flash config). Private share and media are not backed up off-site, by choice.

**Ops:** Homepage (.2) reads every host. Syslog from all hosts → rsyslog on Proxmox `/var/log/remote/` → syslog monitor :8999. ntfy senders are listed in `alerts/SENDERS.md`. Uptime Kuma on the Orange Pi. healthchecks.io dead-man ping from Proxmox. NetAlertX in LXC 102.

## Step 3: draw (about 30 min)

1. **01-network:** router at top; LAN, IoT and guest networks as subgraphs; each physical box with its IP; WireGuard path from phone/laptop → router → VM 107 → LAN; DNS path client → Pi-hole .11 / .10 → upstream.
2. **02-services:** one subgraph per host; every running guest or Docker app with IP and main port; stopped ones grey and dashed.
3. **03-data-backup:** disks and shares; SMB/CIFS mounts (Proxmox `/mnt/UNRAID`, FileBrowser); backup jobs with time and retention; Google Drive as an external node.
4. **04-ops:** Homepage data sources; syslog flow; every ntfy sender → ntfy.sh → phone; AI: Homepage Ask Qwen → A310, Open WebUI/OpenCode → gaming PC.

## Step 4: check before committing (about 10 min)

- Every IP, port and guest on a diagram appears in `inventory.json` with `verified: true`, or is drawn dashed.
- `grep -riE 'token|password|secret|apikey|ntfy\.sh/[a-z0-9]' diagrams/` returns nothing but the word "ntfy.sh" itself.
- `grep -c 'Â' diagrams/*` is 0 (no mojibake).
- Every `.svg` opens and is legible at 100% zoom.
- `diagrams/README.md` lists: collection time, the regenerate command, and every difference found between the starting facts and live data.

Commit as `diagrams: homelab network, services, data and ops diagrams` and push to `n100`.
