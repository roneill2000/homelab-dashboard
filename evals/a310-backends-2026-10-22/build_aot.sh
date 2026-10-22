#!/bin/bash
# Build only libggml-sycl.so for the Arc A310 (DG2-G11) ahead of time, same llama.cpp revision and flags as
# ghcr.io/ggml-org/llama.cpp:server-intel b11804, then layer it onto that image.
set -euo pipefail
REV=c7fc5094179bf8a9c73c18593db714f8da41a402
BASE=ghcr.io/ggml-org/llama.cpp@sha256:2d162b8160ea2e023c51fd7abff0b3d39ad273c2350bbd39902e422b8132c7e9
W=/mnt/cache/build/llama-aot
cd $W
docker run --rm -v $W:/work -w /work docker.io/intel/deep-learning-essentials:2026.1.1-devel-ubuntu24.04 bash -c "
set -e
wget -q https://registrationcenter-download.intel.com/akdlm/IRC_NAS/94c3dbac-0852-45be-a57d-21c204cada3e/intel-onednn-2026.0.2.46_offline.sh -O /tmp/onednn.sh
bash /tmp/onednn.sh -s -a --silent --eula accept >/dev/null
apt-get update -qq && apt-get install -y -qq git libssl-dev wget ca-certificates >/dev/null
cd /tmp && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero_1.28.2%2Bu24.04_amd64.deb -O lz.deb \
  && wget -q https://github.com/oneapi-src/level-zero/releases/download/v1.28.2/level-zero-devel_1.28.2%2Bu24.04_amd64.deb -O lzd.deb \
  && apt-get -o Dpkg::Options::=--force-overwrite install -y -qq ./lz.deb ./lzd.deb >/dev/null
R=https://github.com/intel/compute-runtime/releases/download/26.18.38308.1; G=https://github.com/intel/intel-graphics-compiler/releases/download/v2.34.4
cd /tmp && wget -q \$R/intel-ocloc_26.18.38308.1-0_amd64.deb \$R/libigdgmm12_22.10.0_amd64.deb \$G/intel-igc-core-2_2.34.4+21428_amd64.deb \$G/intel-igc-opencl-2_2.34.4+21428_amd64.deb \
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
docker build -q -t homelab/llama-sycl-a310:b11804 -f Dockerfile . 
echo BUILD-DONE
