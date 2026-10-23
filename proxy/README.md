# Homelab Proxy (*.lan)

| Service | URL | Backend |
| --- | --- | --- |
| proxy | https://proxy.lan | 192.168.0.20 (LXC 115, Caddy) |
| memos | https://memos.lan | 192.168.0.18:5230 |
| openwebui | https://ai.lan | 192.168.0.16:3000 |
| opencode | https://opencode.lan | 192.168.0.19:4096 |
| pve | https://proxmox.lan | 192.168.0.3:8006 |
| unraid | https://nas.lan | 192.168.0.4:80 |
| scrutiny | https://scrutiny.lan | 192.168.0.4:8080 |
| syslog | https://logs.lan | 192.168.0.3:8999 |
| pihole11 | https://pihole.lan/admin | 192.168.0.11:80 |
| pihole10 | https://pihole2.lan/admin | 192.168.0.10:80 |
| openspeedtest | https://lanspeed.lan | 192.168.0.13:3000 |
| speedtest | https://speedtest.lan | 192.168.0.14:80 |
| netalertx | https://netalertx.lan | 192.168.0.13:20211 |
| kuma | https://kuma.lan | 192.168.0.10:3001 |
| jellyfin | https://jellyfin.lan | 192.168.0.17:8096 |
| uhf | https://tv.lan | 192.168.0.13:8080 |
| sonarr | https://sonarr.lan | 192.168.0.13:8989 |
| readarr | https://readarr.lan | 192.168.0.13:8787 |
| prowlarr | https://prowlarr.lan | 192.168.0.13:9696 |
| sabnzbd | https://sab.lan | 192.168.0.13:8085 |
| rdt | https://rdt.lan | 192.168.0.13:6500 |
| audiobooks | https://audiobooks.lan | 192.168.0.4:13378 |
| kasm | https://kasm.lan | 192.168.0.15:443 |
| guacamole | https://guac.lan | 192.168.0.6:8080 |
| files | https://files.lan | 192.168.0.13:6501 |
| glances | https://glances.lan | 192.168.0.4:61208 |
## App-side settings

- SABnzbd only answers hostnames on its allow-list: `sab.lan` was added to `host_whitelist` in `/var/lib/sabnzbd/sabnzbd.ini` (LXC 102) on 22 Oct 2026; backup at `sabnzbd.ini.bak-20261022`. Add any new SABnzbd name there too.
- Pi-hole admin pages live at `/admin/` (the root returns 403/404 by design).
- `tv.lan` (UHF) returns 404 at `/`, the same as direct `192.168.0.13:8080`; that is the app, not the proxy.
