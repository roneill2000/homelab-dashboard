#!/bin/bash
# Temporary build daemon pair on Unraid (host namespace, isolated from main daemon by socket/data-root/bridge-none).
set -x
B=/mnt/docker-build
if ! mountpoint -q $B; then
  [ -f /mnt/cache/docker-build.img ] || truncate -s 40G /mnt/cache/docker-build.img
  LOOP=$(losetup --find --show /mnt/cache/docker-build.img)
  blkid $LOOP >/dev/null 2>&1 || mkfs.btrfs -f -q $LOOP
  mkdir -p $B
  mount $LOOP $B
fi
CONF=$B/containerd.toml
cat > $CONF <<'EOF'
version = 2
root = '/mnt/docker-build/docker/containerd/daemon'
state = '/mnt/docker-build/docker/containerd-daemon'
temp = ''
disabled_plugins = ['io.containerd.grpc.v1.cri']
required_plugins = []
oom_score = 0
imports = []

[grpc]
address = '/run/docker-build/containerd/containerd.sock'
tcp_address = ''
tcp_tls_ca = ''
tcp_tls_cert = ''
tcp_tls_key = ''
uid = 0
gid = 0
max_recv_message_size = 16777216
max_send_message_size = 16777216
tcp_tls_common_name = ''

[ttrpc]
address = ''
uid = 0
gid = 0

[debug]
address = '/run/docker-build/containerd/containerd-debug.sock'
uid = 0
gid = 0
level = 'info'
format = 'text'

[metrics]
address = ''
grpc_histogram = false

[cgroup]
path = ''

[plugins."io.containerd.runtime.v2.task"]
  runtime = "/mnt/docker-build/bin/runc"
EOF
mkdir -p $B/docker
echo '{}' > $B/docker/daemon.json
for p in $(pgrep -f 'dockerd.*docker-build[.]sock'); do kill "$p"; done
sleep 2
for p in $(pgrep -f 'containerd --config /mnt/docker-build/containerd[.]toml'); do kill "$p"; done
sleep 2
rm -rf $B/docker/containerd-daemon $B/docker-exec
setsid containerd --config $CONF </dev/null >>/mnt/cache/build/llama-aot-20261114/containerd.log 2>&1 &
sleep 4
setsid dockerd --host unix:///run/docker-build.sock --containerd /run/docker-build/containerd/containerd.sock --data-root $B/docker --storage-driver btrfs --iptables=false --bridge none --log-level info </dev/null >>/mnt/cache/build/llama-aot-20261114/dockerd.log 2>&1 &
sleep 12
DOCKER_HOST=unix:///run/docker-build.sock docker info --format '{{.Driver}} {{.DockerRootDir}}'
DOCKER_HOST=unix:///run/docker-build.sock docker run --rm --network none docker.io/library/busybox:latest echo CONTAINER-OK | tail -1
echo DAEMONS-UP
