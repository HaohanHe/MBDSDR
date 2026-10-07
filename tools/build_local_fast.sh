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
echo "=== rsync source to fast local disk ==="
rsync -a --exclude='cpp/build' "$SRC/" "$W/"
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

echo "=== full ctest (offscreen) ==="
cd build
export QT_QPA_PLATFORM=offscreen
cstart=$(date +%s)
ctest --output-on-failure 2>&1 | tee /tmp/ctestdet.log
crc=${PIPESTATUS[0]}
cend=$(date +%s)
echo "CTEST rc=$crc elapsed=$((cend-cstart))s"
echo "=== DONE brc=$brc crc=$crc build=$((bend-bstart))s ctest=$((cend-cstart))s ==="
