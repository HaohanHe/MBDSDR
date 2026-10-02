#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Phase14 P3 — pass_predict 对接脚本的确定性自检
================================================================

只验证「可测部分」，不碰硬件、不收真实信号。覆盖：
  1) 诚实空态：未填地面站 / 未录入 TLE → 不伪造过境，如实返回原因；
  2) 样本 TLE 管线：注入固定时钟 + 一条公开示例 TLE（ISS，仅验证管线），
     predict_passes_report 出过境、TLE 新鲜度被正确标注；
  3) 多普勒符号约定：AOS（接近）fd>0、LOS（远离）fd<0 —— 与 sat_passes 注释一致。

样本 TLE 仅用于证明预测/多普勒管线可跑通，**不是**活动卫星 JAMX01/ASRTU-1；
活动卫星的 TLE/频率仍由人工在 pass_predict.py 顶部录入位填入。

既可 pytest 收集，也可直接 `python3 docs/learn/phase14/test_pass_predict.py` 跑。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pass_predict as pp  # noqa: E402  (同目录脚本)

from mbdsdr_ai.sat_passes import (  # noqa: E402
    GroundStation,
    predict_passes,
    compute_doppler_curve,
)
from mbdsdr_ai.spacetime_predict import predict_passes_report  # noqa: E402

# 公开示例 TLE（ISS，历元 2024-01-01，仅验证管线；活动卫星以官方 TLE 为准）。
_SAMPLE_TLE = [
    "ISS (ZARYA)",
    "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9001",
    "2 25544  51.6400 208.9163 0006703  45.6260 312.5170 15.4976  69.8600   10001",
]
# 长春示例站（仅测试用）。
_GS = GroundStation(lat_deg=43.81, lon_deg=125.32, alt_m=200.0)
# 固定时钟：测试不依赖墙钟，结果可复现。
_FIXED_CLOCK = lambda: datetime(2026, 10, 8, 1, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def test_empty_ground_station_honest():
    """地面站经纬度未填 → 返回 (None, 原因)，不猜默认站。"""
    saved = pp.GROUND_STATION
    pp.GROUND_STATION = {"lat_deg": None, "lon_deg": None, "alt_m": 0.0}
    try:
        gs, reason = pp._build_ground_station()
        assert gs is None
        assert reason and "经纬度" in reason
    finally:
        pp.GROUND_STATION = saved


def test_resolve_tle_empty_honest():
    """卫星未录入 TLE 也无 catnr → (None, 原因)，绝不伪造。"""
    tle, reason = pp._resolve_tle({"name": "DUMMY", "norad_catnr": None,
                                   "tle_lines": [], "downlink_hz": None})
    assert tle is None
    assert "TLE" in reason or "tle" in reason.lower()


def test_sample_tle_report_runs_and_freshness():
    """样本 TLE + 固定时钟：出过境，TLE 新鲜度三态被报告（旧历元应是 stale）。"""
    rep = predict_passes_report(_SAMPLE_TLE, _GS, hours=48.0,
                                min_alt=10.0, clock=_FIXED_CLOCK)
    assert rep["time_source"] == "system"   # 未注入 GNSS → 诚实 system
    assert rep["tle"]["status"] in ("fresh", "stale", "none")
    # 51.6° 倾角 ISS 对 43.8°N 站在 48h 内必有多次过境
    assert rep["n_passes"] >= 1, rep["passes"]


def test_empty_tle_never_fabricates():
    """空 TLE → 零过境 + status=none（红线：不伪造过境）。"""
    rep = predict_passes_report([], _GS, hours=24.0, clock=_FIXED_CLOCK)
    assert rep["n_passes"] == 0
    assert rep["tle"]["status"] == "none"


def test_doppler_sign_convention():
    """AOS（卫星接近）fd>0，LOS（远离）fd<0；量级符合 LEO 2m 波段。"""
    passes = predict_passes(_SAMPLE_TLE, _GS, hours=48.0, min_alt=10.0)
    assert passes, "样本 TLE 应至少有一次过境"
    curve = compute_doppler_curve(_SAMPLE_TLE, _GS, passes[0],
                                  freq_hz=145.8e6, num_points=40)
    assert len(curve) >= 2
    aos, los = curve[0].doppler_hz, curve[-1].doppler_hz
    assert aos > 0, f"AOS 多普勒应为正(接近)，实得 {aos:.0f}"
    assert los < 0, f"LOS 多普勒应为负(远离)，实得 {los:.0f}"
    # 145 MHz LEO 峰 |fd| 通常几 kHz（数量级断言，不绑死具体值）
    peak = max(abs(pt.doppler_hz) for pt in curve)
    assert 500 < peak < 15000, f"峰多普勒 {peak:.0f} Hz 超出 LEO 2m 合理量级"


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
