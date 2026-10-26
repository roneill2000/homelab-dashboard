# Host & Router Kernel / CPU Tuning

- **Proxmox (`192.168.0.3`, Intel N100):**
  - `proxmox-99-pve-tuning.conf` -> `/etc/sysctl.d/99-pve-tuning.conf` (`vm.swappiness=1`, `kernel.sched_autogroup_enabled=0`, `kernel.nmi_watchdog=0`, 16 MB TCP buffers)
  - `proxmox-99-epp.conf` -> `/etc/tmpfiles.d/99-epp.conf` (`balance_performance` EPP, `hwp_dynamic_boost=1`, unused radio soft-block)
  - 1:1 `xps_cpus` (`2, 4, 8, 1`) on `enp2s0` TX queues in `/etc/network/interfaces` (with software `rps_cpus` disabled)
- **GL-BE9300 Router (`192.168.0.1`):**
  - `router-rc.local` -> `/etc/rc.local` (`performance` CPU governor locked at `1.5 GHz`, bridge hairpinning, IRQ SMP affinity, IGMP snooping)
- **Orange Pi Zero 2W (`192.168.0.10`):**
  - `orangepi-cpufrequtils` -> `/etc/default/cpufrequtils` (`schedutil` governor `480-1512 MHz`), `mq-deadline` on `mmcblk1`, 15s hardware watchdog
- **Unraid (`192.168.0.4`, Core i7-7700T):**
  - `hwp_dynamic_boost=1` and `vm.swappiness=10` in `/boot/config/go`
