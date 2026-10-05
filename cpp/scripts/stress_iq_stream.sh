#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Phase51 block4: 10-minute headless IQ-stream soak.
#
# Boots the REAL desktop app (cpp/build/mbdsdr) offscreen on the honest offline
# synthetic test source (MBDSDR_TEST_SOURCE=1) -- no hardware, no network -- and
# watches it for 10 minutes. Samples resident set (RSS) and liveness every 10s.
# The point is to prove continuous IQ streaming does not (a) crash, (b) leak RSS
# monotonically, or (c) stop producing frames. It is NOT a ctest target (the
# 129-test baseline must stay fast); run this by hand for a real long-soak.
#
# Output: a CSV "t_sec,rss_kb,alive" on stdout (redirect to a file).
set -u
BUILD_DIR="$(cd "$(dirname "$0")/../build" 2>/dev/null && pwd)"
if [ -n "${BUILD_DIR:-}" ] && [ -x "${BUILD_DIR}/mbdsdr" ]; then cd "$BUILD_DIR"; fi
export PATH=/home/user/Qt/6.8.2/gcc_64/bin:$PATH
export LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib:${LD_LIBRARY_PATH:-}
export QT_QPA_PLATFORM=offscreen
export MBDSDR_TEST_SOURCE=1

DUR_SEC="${SOAK_DUR_SEC:-600}"      # 10 minutes
STEP_SEC=10

./mbdsdr > /tmp/mbdsdr_soak.log 2>&1 &
APP_PID=$!
echo "t_sec,rss_kb,alive"
# Let the app come up before sampling.
sleep 5
t=0
peak=0
while [ "$t" -le "$DUR_SEC" ]; do
  if kill -0 "$APP_PID" 2>/dev/null; then
    alive=1
    rss=$(ps -o rss= -p "$APP_PID" 2>/dev/null | tr -d ' ')
    [ -z "$rss" ] && rss=0
    [ "$rss" -gt "$peak" ] && peak=$rss
  else
    alive=0
    rss=0
  fi
  echo "$t,$rss,$alive"
  if [ "$alive" = "0" ]; then
    echo "# app EXITED at t=${t}s -- aborting soak" >&2
    break
  fi
  sleep "$STEP_SEC"
  t=$((t + STEP_SEC))
done
echo "# peak_rss_kb=$peak" >&2
# Tear down.
kill "$APP_PID" 2>/dev/null
sleep 1
pkill -f "build/mbdsdr" 2>/dev/null
exit 0
