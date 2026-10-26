# Syslog on Proxmox

rsyslog on Proxmox receives UDP 514 and writes one file per sender to `/var/log/remote/`.
`syslog-monitor.py` (port 8999) scans those files and Homepage shows the summary.

| File | Source |
| --- | --- |
| `orangepi.log` (alias `pi-ryan.log`) | Orange Pi .10: Pi-hole replica, Uptime Kuma, nebula-sync |
| `pihole-primary.log` | LXC 106 .11, the primary Pi-hole (hostname still `pihole-secondary`) |
| `proxmox.log` | Proxmox's own journal, notice and above (`11-local.conf`) |
| `<hostname>.log` | everything else, by lowercase hostname |

Install: copy both `.conf` files to `/etc/rsyslog.d/`, run `rsyslogd -N1`, then `systemctl restart rsyslog`.
Rotation: `/etc/logrotate.d/` keeps 12 weekly files.

`syslog-monitor.py` is the live copy of Proxmox `/usr/local/bin/syslog-monitor.py` (added to the repo 26 Oct 2026; it was edited live and never tracked).
