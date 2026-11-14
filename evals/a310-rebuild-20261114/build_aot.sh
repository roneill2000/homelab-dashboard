#!/bin/bash
# Build only libggml-sycl.so for the Arc A310 (DG2-G11) ahead of time, same llama.cpp revision and flags as
# ghcr.io/ggml-org/llama.cpp:server-intel v1.0.0, then layer it onto that image.
# Derived from evals/a310-backends-2026-10-22/build_aot.sh (b11804). Changes vs that script, with reasons:
#  - REV/BASE: llama.cpp v1.0.0 (tag commit b1b770d9..., published server-intel index digest 90e701ba...;
#    verified by the image's org.opencontainers.image.revision annotation).
#  - Build image: intel/deep-learning-essentials 2026.1.1 -> 2026.2.0-devel-ubuntu24.04. The v1.0.0 base
#    image ships oneAPI 2026.2 (MKL 2026.2, compiler 2026.2); building against a matching toolkit keeps the
#    AOT .so's runtime links in step with the base.
#  - ocloc/IGC/igdgmm pins: 26.18.38308.1 / 2.34.4+21428 / 22.10.0 -> 26.40.40196.8 / 2.42.6+23070 /
#    22.10.2, exactly the versions the v1.0.0 base image installs, so the AOT kernels are compiled by the
#    same IGC/runtime combination the container runs. Old pins' URLs still resolve (checked 200 on
#    2026-11-14); they were bumped only for this version match.
#  - level-zero and oneDNN pins unchanged (URLs verified to still resolve).
#  - Output tag: homelab/llama-sycl-a310:v1.0.0 (old b11804 image and build folder kept untouched).
set -euo pipefail
REV=b1b770d90d94cad9e9ea5b993bdbc383a522fb28
BASE=ghcr.io/ggml-org/llama.cpp@sha256:90e701ba5bd2ae503e4618f74b5bd05381614fc43f6c23a209796a5d680d5371
TAG=homelab/llama-sycl-a310:v1.0.0
W=/mnt/cache/build/llama-aot-20261114
# Run against a temporary dockerd (data-root on cache) so the main 40 GB docker.img is not touched:
# export DOCKER_HOST=unix:///run/docker-build.sock  (see REPORT.md; docker.img has 7 GB free, build needs ~30)
mkdir -p $W
cd $W
docker run --rm --network host -v $W:/work -w /work docker.io/intel/deep-learning-essentials:2026.2.0-devel-ubuntu24.04 bash -c "
set -e
wget -q https://registrationcenter-download.intel.com/akdlm/IRC_NAS/94c3dbac-0852-45be-a57d-21c204cada3e/intel-onednn-2026.0.2.46_offline.sh -O /tmp/onednn.sh
bash /tmp/onednn.sh -s -a --silent --eula accept >/dev/null
apt-get update -qq && apt-get install -y -qq git libssl-dev wget ca-certificates >/dev/null
cd /tmp && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero_1.28.2%2Bu24.04_amd64.deb -O lz.deb \
  && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero-devel_1.28.2%2Bu24.04_amd64.deb -O lzd.deb \
  && apt-get -o Dpkg::Options::=--force-overwrite install -y -qq ./lz.deb ./lzd.deb >/dev/null
R=https://github.com/intel/compute-runtime/releases/download/26.40.40196.8; G=https://github.com/intel/intel-graphics-compiler/releases/download/v2.42.6
cd /tmp && wget -q \$R/intel-ocloc_26.40.40196.8-0_amd64.deb \$R/libigdgmm12_22.10.2_amd64.deb \$G/intel-igc-core-2_2.42.6+23070_amd64.deb \$G/intel-igc-opencl-2_2.42.6+23070_amd64.deb \
  && apt-get -o Dpkg::Options::=--force-overwrite install -y -qq --allow-downgrades ./intel-ocloc_*.deb >/dev/null; which ocloc || ls /usr/bin/ocloc*; ocloc compile --help 2>&1 | head -1
cd /work
[ -d llama.cpp ] || git clone -q https://github.com/ggml-org/llama.cpp
cd llama.cpp && git checkout -q $REV
export SYCL_PROGRAM_COMPILE_OPTIONS=-cl-fp32-correctly-rounded-divide-sqrt
cmake -B build -DGGML_NATIVE=OFF -DGGML_SYCL=ON -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx -DGGML_BACKEND_DL=ON \
  -DGGML_CPU_ALL_VARIANTS=ON -DLLAMA_BUILD_TESTS=OFF -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=acm-g11 >/work/cmake.log
cmake --build build --config Release --target ggml-sycl -j3
ls -la build/bin/libggml-sycl.so
"
cp llama.cpp/build/bin/libggml-sycl.so ./libggml-sycl.so
printf 'FROM %s\nCOPY libggml-sycl.so /app/libggml-sycl.so\n' "$BASE" > Dockerfile
docker build -q -t $TAG -f Dockerfile .
echo BUILD-DONE
