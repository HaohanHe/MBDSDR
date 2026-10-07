#!/bin/bash
# One-shot: build + full ctest within a single sandbox instance so the fast
# local /tmp survives the whole run (persistent /home/user is a slow hpvs_fs;
# /tmp is fast but private to this process). Key results go to STDOUT so the
# harness captures them; verbose build logs stay in the private /tmp and are
# only dumped on failure.
# Repo root = parent of this script's tools/ directory (no hardcoded path).
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W=/tmp/MBDSWRUN
# Qt location may be overridden via QT6_HOME; default to the cloud SDK path.
export LD_LIBRARY_PATH="${QT6_HOME:-/home/user/Qt/6.8.2/gcc_64}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
QT_PREFIX="${QT6_HOME:-/home/user/Qt/6.8.2/gcc_64}"

rm -rf "$W"
mkdir -p "$W"
echo "=== rsync cpp/ (build's only dependency) to fast local disk ==="
# Only the cpp/ tree is needed to configure+build+test. Copying the whole
# ~22MB repo (docs/flutter/mobile/hardware/paper, tens of thousands of small
# files) over the slow hpvs_fs costs 10-20min of FUSE stat latency and can
# exceed the sandbox per-task lifetime; cpp/ source is ~4MB / a few hundred
# files, so this rsync completes in seconds.
# NOTE: source is "$SRC/cpp/" (trailing slash), so exclude patterns are
# relative to the cpp/ tree root: use 'build' (NOT 'cpp/build', which would
# not match and would drag in the 7.3GB stale build tree).
rsync -a --exclude='build' "$SRC/cpp/" "$W/cpp/"
cd "$W/cpp"

echo "=== cmake configure ==="
if ! cmake -S . -B build -DCMAKE_PREFIX_PATH="$QT_PREFIX" > /tmp/cfgdet.log 2>&1; then
    echo "CONFIGURE FAILED -- log:"; cat /tmp/cfgdet.log; exit 10
fi

echo "=== full build -j4 ==="
bstart=$(date +%s)
cmake --build build -j4 > /tmp/builddet.log 2>&1
brc=$?
bend=$(date +%s)
echo "BUILD rc=$brc elapsed=$((bend-bstart))s"
if [ "$brc" -ne 0 ]; then
    echo "BUILD FAILED -- errors:"; grep -n "error:" /tmp/builddet.log | head -30
    exit 11
fi

echo "=== full ctest -j4 (offscreen, parallel) ==="
# Serial full ctest would add ~15min and push the whole run past the sandbox
# per-task lifetime. These tests are independent binaries; -j4 is safe and
# cuts ctest to a few minutes. Any inter-test resource clash shows as a real
# failure and is handled honestly.
cd build
export QT_QPA_PLATFORM=offscreen
cstart=$(date +%s)
ctest --output-on-failure -j4 2>&1 | tee /tmp/ctestdet.log
crc=${PIPESTATUS[0]}
cend=$(date +%s)
echo "CTEST rc=$crc elapsed=$((cend-cstart))s"
echo "=== DONE brc=$brc crc=$crc build=$((bend-bstart))s ctest=$((cend-cstart))s ==="
