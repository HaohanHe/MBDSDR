# -*- coding: utf-8 -*-
"""协议真值线长跑（无人值守流水线第四段：链路健壮性与长跑）。

循环跑协议真值电池（GPS/LRPT/ADS-B/FT8/DVB-S2 的金样与跨实现对拍），
每轮结果落 JSONL，供事后判定 flaky / 回归 / 资源漂移。

用法：
    python scripts/soak_truth_battery.py [--rounds N] [--timeout SEC]
        [--out PATH] [--stop-on-fail]

默认 20 轮、每轮超时 900s、结果写 output/soak/soak_<时间戳>.jsonl。
前置条件（协调纪律）：工作区无其他会话在途编辑协议真值线文件时再启动；
长跑期间勿动 tests/golden/** 与 mbdsdr_ai/{gps_ca,lrpt_*,dvbs2_*}。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 协议真值电池：金样钉扎 + 跨实现对拍（刻意不含 UI 线在途文件）
BATTERY = [
    "tests/test_gps_prn.py",
    "mbdsdr_ai/tests/test_gps_tracking.py",
    "mbdsdr_ai/tests/test_gps_bit_sync.py",
    "mbdsdr_ai/tests/test_gps_nav.py",
    "tests/test_fengyun_dvbs2.py",
    "tests/test_meteor_lrpt.py",
    "tests/test_lrpt_iq_chain.py",
    "tests/test_lrpt_cross_impl_fixture.py",
    "tests/test_lrpt_meteor_real_golden.py",
    "tests/test_adsb_consistency.py",
    "tests/test_adsb_cross_impl_parity.py",
    "tests/test_adsb_dump1090_golden.py",
    "tests/test_ft8_cross_impl_fixture.py",
    "tests/test_ft8_wsjtx_golden.py",
    "tests/test_ft8_modes_benchmark.py",
]


def run_round(idx: int, timeout: int) -> dict:
    t0 = time.time()
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-x",
           "--disable-warnings", *BATTERY]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    return {
        "round": idx,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "rc": proc.returncode,
        "duration_s": round(time.time() - t0, 1),
        "summary": summary,
        "tail": tail[-8:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=None)
    ap.add_argument("--stop-on-fail", action="store_true")
    args = ap.parse_args()

    out = args.out or os.path.join(
        ROOT, "output", "soak",
        "soak_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    fails = 0
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"event": "start", "battery": BATTERY,
                            "rounds": args.rounds,
                            "ts": dt.datetime.now().isoformat(timespec="seconds")},
                           ensure_ascii=False) + "\n")
        for i in range(1, args.rounds + 1):
            try:
                rec = run_round(i, args.timeout)
            except subprocess.TimeoutExpired:
                rec = {"round": i, "rc": -9, "duration_s": args.timeout,
                       "summary": "TIMEOUT",
                       "ts": dt.datetime.now().isoformat(timespec="seconds")}
            rec["event"] = "round"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            ok = rec["rc"] == 0
            fails += 0 if ok else 1
            print(f"[{i}/{args.rounds}] rc={rec['rc']} {rec['duration_s']}s "
                  f"{rec['summary']}", flush=True)
            if not ok and args.stop_on_fail:
                break
        f.write(json.dumps({"event": "done", "rounds_run": i,
                            "failures": fails,
                            "ts": dt.datetime.now().isoformat(timespec="seconds")},
                           ensure_ascii=False) + "\n")
    print(f"done: {i} rounds, {fails} failures -> {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
