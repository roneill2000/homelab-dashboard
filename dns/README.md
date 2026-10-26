# High-Availability Pi-hole + Unbound DNS (`192.168.0.8`)

- **Floating VRRP VIP:** `192.168.0.8/24` (VRRP ID `53`, unicast `keepalived`, `--vrrp` only)
- **Primary (`192.168.0.11`, LXC 106):** `keepalived-11.conf` (`eth0`, priority `150`, `preempt_delay 30`), `unbound-11.conf` (`/etc/unbound/unbound.conf.d/pi-hole.conf`, `127.0.0.1#5335`)
- **Replica (`192.168.0.10`, Orange Pi Zero 2W):** `keepalived-10.conf` (`end0`, priority `100`), `unbound-10.conf` (`/etc/unbound/unbound.conf.d/pi-hole.conf`, `127.0.0.1#5335`)
- **Health Check (`/etc/keepalived/check_dns.sh`):** Queries local `pi.hole` and `unbound-control status` every 2s (`weight -60`).
- **Router DHCP Option 6:** `6,192.168.0.8,192.168.0.10`
- **Proxmox DNS Probe:** `dns-health-check` + `dns-health-check.timer` probes `.11`, `.10`, and `.8` every minute and logs `DNS-HEALTH FAIL` to `/var/log/remote/dns-health.log` for `syslog-monitor.py`.
