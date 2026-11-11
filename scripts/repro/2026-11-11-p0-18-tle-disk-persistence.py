#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-18 跨进程验收：TLE 刷新后重启不得退回内置 2026-09-25。

用户要求（原话）：
    「更新 TLE → 退出进程 → 重新启动 → 读回 tracker 里的 TLE 纪元，
      必须不是 2026-09-25。」

做法（不依赖真实网络，用注入的 HTTP 响应把「刷新」这一步钉成可控输入）：
  1. 建一个隔离 HOME 的临时目录，两个**独立子进程**依次运行；
  2. 子进程 A：注入假 requests（返回历元 2026-11-11 的 NOAA 15 TLE），
     构造 SatelliteTracker 并调用 ``update_tle_from_celestrak()``，退出；
  3. 子进程 B：不注入网络、不刷新，仅构造 SatelliteTracker，读回目录历元；
  4. 判定：A 的历元 = 写入值；B 的历元 = A 的历元且 != 2026-09-25。

退出码：0 通过 / 1 失败（B 退回内置） / 2 无法判定（A 未刷新成功）。

用法：
    python scripts/repro/2026-11-11-p0-18-tle-disk-persistence.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
BUILTIN_DATE = "2026-09-25"

# 内置 NOAA 15 的真实 TLE，只把历元列换成 2026-11-11。
NEW_L1 = "1 25338U 98030A   26315.50000000  .00000085  00000+0  52080-4 0  9991"
NEW_L2 = "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651"
TLE_TEXT = f"NOAA 15\n{NEW_L1}\n{NEW_L2}\n"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _child(phase: str) -> int:
    """子进程：A 刷新并发；B 只启动读回。结果以 RESULT {json} 一行输出。"""
    sys.path.insert(0, ROOT)
    if phase == "A":
        import types

        class _Resp:
            text = TLE_TEXT

            @staticmethod
            def raise_for_status():
                return None

        sys.modules["requests"] = types.SimpleNamespace(
            get=lambda *a, **k: _Resp())

    from mbdsdr_ai.sat_passes import GroundStation
    from mbdsdr_ai.sat_tracker import SatelliteTracker

    tracker = SatelliteTracker(GroundStation(lat_deg=39.9, lon_deg=116.4))
    updated = tracker.update_tle_from_celestrak() if phase == "A" else 0
    sat = next(s for s in tracker.list_satellites() if s["name"] == "NOAA 15")
    print("RESULT " + json.dumps({
        "phase": phase,
        "updated": updated,
        "tle_date": tracker.tle_date,
        "epoch_utc": sat["tle_epoch_utc"],
        "tier": sat["tle_tier"],
        "line1": sat["tle"][0],
        # 改前版本没有这个属性：留空以便同一脚本能跑「改前」对照
        "persist_error": getattr(tracker, "last_persist_error", ""),
    }, ensure_ascii=False))
    return 0


def _run_phase(phase: str, home: str) -> dict:
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        env[key] = home
    env["PYTHONPATH"] = ROOT
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("MBDSDR_OFFLINE", None)
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child", phase],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
    print(f"[phase {phase}] exit={proc.returncode}")
    print(proc.stdout.strip() or "<无 stdout>")
    if proc.stderr.strip():
        print("[stderr] " + proc.stderr.strip()[-1500:])
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    return {}


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        return _child(sys.argv[2])

    os.makedirs(TMP_ROOT, exist_ok=True)
    home = tempfile.mkdtemp(prefix="tle_persist_", dir=TMP_ROOT)
    try:
        _hr("隔离 HOME（重启等价环境）")
        print(home)
        _hr("阶段 A：更新 TLE（进程 A 结束后退出）")
        a = _run_phase("A", home)
        cache_file = os.path.join(home, ".mbdsdr", "tle_cache", "25338.tle")
        print(f"\n落盘文件：{cache_file} 存在={os.path.isfile(cache_file)}")
        if os.path.isfile(cache_file):
            with open(cache_file, "r", encoding="utf-8") as fh:
                print("文件内容：\n" + fh.read())
        _hr("阶段 B：全新进程启动，只读回（不刷新）")
        b = _run_phase("B", home)
        _hr("判定")
        print("A:", json.dumps(a, ensure_ascii=False))
        print("B:", json.dumps(b, ensure_ascii=False))
        if not a or a.get("updated", 0) < 1:
            print("结论：无法判定——阶段 A 未刷新成功")
            return 2
        if not b:
            print("结论：无法判定——阶段 B 未输出结果")
            return 2
        ok = (b.get("epoch_utc") == a.get("epoch_utc")
              and str(b.get("epoch_utc") or "")[:10] != BUILTIN_DATE)
        print(f"读回历元 {b.get('epoch_utc')} vs 内置 {BUILTIN_DATE} → "
              f"{'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
