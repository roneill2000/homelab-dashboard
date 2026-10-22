#!/bin/sh
# Install the SMART status/scheduler on Unraid (via Proxmox). Run from the repo root on the Mac.
set -e
scp -q smart/homelab_smart.py root@192.168.0.3:/tmp/homelab_smart.py
ssh -o BatchMode=yes root@192.168.0.3 'scp -q /tmp/homelab_smart.py root@192.168.0.4:/tmp/ && rm /tmp/homelab_smart.py && ssh root@192.168.0.4 "
mkdir -p /mnt/disk1/.homelab-smart && install -m 700 /tmp/homelab_smart.py /mnt/disk1/.homelab-smart/homelab_smart.py && rm /tmp/homelab_smart.py
printf \"# SMART self-test scheduler (homelab repo smart/); skips Sundays and parity checks\n10 5 * * * test ! -f /mnt/disk1/.homelab-smart/homelab_smart.py || /usr/bin/python3 /mnt/disk1/.homelab-smart/homelab_smart.py schedule >/dev/null 2>&1\n\" > /boot/config/plugins/dynamix/homelab-smart.cron
/usr/local/sbin/update_cron && grep -h homelab_smart /etc/cron.d/root | head -1"'
