#!/bin/bash
# Restart the temporary build daemon pair on Unraid (containerd root must sit on /mnt/cache, not /tmp tmpfs).
set -x
for p in $(pgrep -f 'dockerd --host unix:///run/docker-build[.]sock'); do kill "$p"; done
sleep 2
for p in $(pgrep -f 'containerd --address /run/docker-build'); do kill "$p"; done
sleep 2
rm -f /run/docker-build.sock
mkdir -p /mnt/cache/docker-build-data/containerd /mnt/cache/docker-build-data/docker
setsid containerd --address /run/docker-build/containerd/containerd.sock --state /run/docker-build/containerd --root /mnt/cache/docker-build-data/containerd --log-level info </dev/null >>/mnt/cache/build/llama-aot-20261114/containerd.log 2>&1 &
sleep 4
setsid dockerd --host unix:///run/docker-build.sock --containerd /run/docker-build/containerd/containerd.sock --data-root /mnt/cache/docker-build-data/docker --iptables=false --bridge none --log-level warn </dev/null >>/mnt/cache/build/llama-aot-20261114/dockerd.log 2>&1 &
sleep 8
DOCKER_HOST=unix:///run/docker-build.sock docker info | grep -i "root dir\|storage driver"
echo DAEMONS-UP
