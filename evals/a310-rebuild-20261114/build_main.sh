#!/bin/bash
# A310 v1.0.0 AOT build, run against the MAIN Unraid dockerd (docker.img grown to 60G on 2026-11-13).
# Stage-gated so no heavy phase overlaps the blocked windows 02:45-04:00 and 06:45-07:15 (local), and each
# phase takes the nightly run.lock briefly (nightly can always start inside its window: phases never hold
# the lock between stages; a running nightly delays a phase start, max 25 min wait then abort).
# Resumable: every stage is idempotent (skipped when its output exists).
set -uo pipefail
REV=b1b770d90d94cad9e9ea5b993bdbc383a522fb28
BASE=ghcr.io/ggml-org/llama.cpp@sha256:90e701ba5bd2ae503e4618f74b5bd05381614fc43f6c23a209796a5d680d5371
DEVEL=docker.io/intel/deep-learning-essentials:2026.2.0-devel-ubuntu24.04
TAG=homelab/llama-sycl-a310:v1.0.0
W=/mnt/cache/build/llama-aot-20261114
LOCK=/mnt/disk1/.homelab-nightly/run.lock
LOG=$W/build.log
say(){ echo "$(date '+%F %T') $*" | tee -a $LOG; }

in_window(){ # $1=seconds into the future we must protect
  local t d h m
  t=$(( $(date +%s) + $1 ))
  d=$(date -d "@$t" +%H%M); h=${d:0:2}; m=${d:2:2}
  (( (10#$h*60+10#$m >= 165 && 10#$h*60+10#$m < 240) || (10#$h*60+10#$m >= 405 && 10#$h*60+10#$m < 435) ))
}
gate(){ # sleep until $1 seconds from now lands outside both blocked windows
  while in_window "$1"; do say "GATE: blocked window imminent, sleeping 600s"; sleep 600; done
}
take_lock(){ # hold the nightly lock for the duration of "$@"
  python3 - "$LOCK" "$@" <<'PY'
import fcntl,sys,subprocess,time
lock_path=sys.argv[1]; cmd=sys.argv[2:]
for i in range(150):
    f=open(lock_path,'w')
    try:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        f.close(); time.sleep(10); continue
    try:
        sys.exit(subprocess.run(cmd).returncode)
    finally:
        fcntl.flock(f,fcntl.LOCK_UN); f.close()
sys.exit('lock busy 25min')
PY
}

mkdir -p $W && cd $W
say "BUILD-START main-daemon build (docker.img 60G)"

gate 1500
take_lock docker pull -q $DEVEL
take_lock docker pull -q $BASE
say "PULLED devel+base"

STATEDIR=$W/keepsys
mkdir -p $STATEDIR
if [ ! -f $STATEDIR/ocloc-ok ]; then
  gate 1200
  take_lock docker run --rm --network host -v $W:/work -v $STATEDIR:/sysstate -w /work $DEVEL bash -c '
    set -e
    wget -q https://registrationcenter-download.intel.com/akdlm/IRC_NAS/94c3dbac-0852-45be-a57d-21c204cada3e/intel-onednn-2026.0.2.46_offline.sh -O /tmp/onednn.sh
    bash /tmp/onednn.sh -s -a --silent --eula accept >/work/onednn.log 2>&1
    apt-get update -qq && apt-get install -y -qq git libssl-dev wget ca-certificates dpkg >/sysstate/apt.log 2>&1
    cd /tmp && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero_1.28.2%2Bu24.04_amd64.deb -O lz.deb \
      && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero-devel_1.28.2%2Bu24.04_amd64.deb -O lzd.deb \
      && apt-get -o Dpkg::Options::=--force-overwrite install -y -qq ./lz.deb ./lzd.deb >>/sysstate/apt.log 2>&1
    R=https://github.com/intel/compute-runtime/releases/download/26.40.40196.8; G=https://github.com/intel/intel-graphics-compiler/releases/download/v2.42.6
    cd /tmp && wget -q $R/intel-ocloc_26.40.40196.8-0_amd64.deb $R/libigdgmm12_22.10.2_amd64.deb $G/intel-igc-core-2_2.42.6+23070_amd64.deb $G/intel-igc-opencl-2_2.42.6+23070_amd64.deb \
      && apt-get -o Dpkg::Options::=--force-overwrite install -y -qq --allow-downgrades ./intel-ocloc_*.deb ./libigdgmm*.deb ./intel-igc-core-2_*.deb ./intel-igc-opencl-2_*.deb >>/sysstate/apt.log 2>&1
    dpkg -L intel-ocloc-26.40.40196.8 2>/dev/null | grep -m2 ocloc || dpkg -l | grep -m3 "ocloc\|igc-core"
    find / -name "ocloc" -type f 2>/dev/null | head -2 > /sysstate/ocloc-path.txt
    ocloc compile --help >/dev/null 2>&1 || /opt/intel/oneapi/ocloc/latest/bin/ocloc compile --help >/dev/null 2>&1
    [ $? -eq 0 ] && touch /sysstate/ocloc-ok
  '
  say "STAGE-PKGS rc=$?"
  [ -f $STATEDIR/ocloc-ok ] || { say "PKGS-FAIL"; exit 1; }
fi
say "pkgs ready"

cd $W/llama.cpp
git fetch -q origin || true
git checkout -q $REV || { git fetch -q --depth=200 origin refs/tags/v1.0.0 && git checkout -q $REV; }
say "llama.cpp at $REV"
# Fresh configure: a stale CMakeCache from the Oct b11804 build pointed CMAKE_C_COMPILER at the old
# 2026.1 compiler path and killed configure in 1 s (first run on 2026-11-13, caught by .so timestamp).
rm -rf $W/llama.cpp/build
gate 3600
take_lock timeout 3600 docker run --rm --network host -v $W:/work -v $STATEDIR:/sysstate -w /work $DEVEL bash -c '
  set -e
  # Install the same toolkit the v1.0.0 base image ships, inside this container (v1.0.0 cmake requires
  # oneDNN/DNNL and the build-time AOT uses ocloc; a separate pkg container does not persist).
  mkdir -p /work/debs && cd /work/debs
  [ -f onednn.sh ] || wget -q https://registrationcenter-download.intel.com/akdlm/IRC_NAS/94c3dbac-0852-45be-a57d-21c204cada3e/intel-onednn-2026.0.2.46_offline.sh -O onednn.sh
  bash onednn.sh -s -a --silent --eula accept >/work/onednn-build.log 2>&1
  R=https://github.com/intel/compute-runtime/releases/download/26.40.40196.8; G=https://github.com/intel/intel-graphics-compiler/releases/download/v2.42.6; L=https://github.com/oneapi-src/level-zero/releases/download/v1.28.2
  getdeb(){ [ -s "/work/debs/$1" ] && return 0; wget -q "$2/$3" -O "/work/debs/$1"; }
  getdeb level-zero.deb $L 'level-zero_1.28.2%2Bu24.04_amd64.deb'
  getdeb level-zero-devel.deb $L 'level-zero-devel_1.28.2%2Bu24.04_amd64.deb'
  getdeb intel-ocloc.deb $R 'intel-ocloc_26.40.40196.8-0_amd64.deb'
  getdeb libigdgmm12.deb $R 'libigdgmm12_22.10.2_amd64.deb'
  getdeb intel-igc-core-2.deb $G 'intel-igc-core-2_2.42.6+23070_amd64.deb'
  getdeb intel-igc-opencl-2.deb $G 'intel-igc-opencl-2_2.42.6+23070_amd64.deb'
  ls -l /work/debs/ | tail -7
  apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq --no-install-recommends git libssl-dev ca-certificates >/dev/null 2>&1
  apt-get -o Dpkg::Options::=--force-overwrite install -y -qq --allow-downgrades ./*.deb >/dev/null 2>&1
  source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
  export PATH=/opt/intel/oneapi/ocloc/latest/bin:$PATH
  export SYCL_PROGRAM_COMPILE_OPTIONS=-cl-fp32-correctly-rounded-divide-sqrt
  which icx icpx ocloc || true
  cd /work/llama.cpp
  cmake -B build -DGGML_NATIVE=OFF -DGGML_SYCL=ON -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx -DGGML_BACKEND_DL=ON \
    -DGGML_CPU_ALL_VARIANTS=ON -DLLAMA_BUILD_TESTS=OFF -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=acm-g11 >/work/cmake.log 2>&1
  cmake --build build --config Release --target ggml-sycl -j3 >>/work/build-sycl.log 2>&1
  ls -la build/bin/libggml-sycl.so
'
say "STAGE-BUILD rc=$?"
[ -f $W/llama.cpp/build/bin/libggml-sycl.so ] || { say "BUILD-FAIL see $W/build-sycl.log"; exit 1; }

gate 600
cp $W/llama.cpp/build/bin/libggml-sycl.so $W/libggml-sycl.so
printf 'FROM %s\nCOPY libggml-sycl.so /app/libggml-sycl.so\n' "$BASE" > $W/Dockerfile
take_lock docker build -q -t $TAG -f $W/Dockerfile $W
say "IMAGE $TAG rc=$?"
docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}" | grep llama-sycl-a310 | tee -a $LOG
df -h /var/lib/docker | tail -1 | tee -a $LOG
say "BUILD-DONE"
