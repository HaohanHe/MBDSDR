# SPDX-License-Identifier: MIT
"""K12 门限边缘掠射过境：固定 TLE + 固定时刻的可复跑用例。

本文件有两组场景，门限都是 5.0°、二分容差都是 0.5 s，一个字都没动：

1) **ISS 机制场**（TLE 2026-297，站 45.0°N/122.60°E/200 m，24 h 窗口）：
   峰值 5.0062°，过 5° 门限只有 13 s；60 s 粗扫网格两端 4.949°/4.749°，
   峰距单元中点 10.9 s、中点仰角 4.998°。旧实现只探中点 → 整场漏检。

2) **NOAA-19 替代场**（TLE epoch 26268.8504，站 45.0°N/120.0°E/200 m，
   与原 K12 同站点，24 h 窗口）：峰值 5.0027°、过门限约 11 s；60 s 端点
   与 30 s 中点全部 < 5.0°（最大 4.9896°），旧实现同样 0 检出。
   **这是替代场，不是原场**：原 K12 场的 TLE 与观测时刻已不可得
   （R5 脚本每次按墙钟现取 TLE 且不落盘，参见 known-red-register K12）。

反证：把擦边补点改回「只探单元中点」，两组检出用例都会红；把门限降到 5.0
以下才检到同样会红（两组都带 ref_peak+0.005 的负向对照）。
"""
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.sat_passes import (  # noqa: E402
    PASS_FINE_STEP_S,
    GroundStation,
    make_timescale,
    predict_passes,
    sample_trail,
)

ISS_NAME = "ISS (ZARYA)"
ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
LINES = [ISS_NAME, ISS_TLE[0], ISS_TLE[1]]
WHEN = datetime(2026, 10, 24, 6, 0, 0, tzinfo=timezone.utc).timestamp()
GS = GroundStation(lat_deg=45.0, lon_deg=122.60, alt_m=200.0)
GATE_DEG = 5.0


@pytest.fixture(scope="module")
def reference():
    """1 s 参照：掠射场的峰值与 5° 越门时刻（不经过 predict_passes 的分段）。"""
    ts = make_timescale()
    center = ts.utc(datetime(2026, 10, 24, 16, 3, 19, tzinfo=timezone.utc))
    samples = sample_trail(LINES, GS, center, before_s=400.0, after_s=400.0,
                           step_s=1.0)
    above = [s for s in samples if s[2] >= GATE_DEG]
    assert above, "参照网格必须找到 5° 以上采样"
    peak_t, _az, peak_el = max(samples, key=lambda s: s[2])
    return {
        "peak_unix": peak_t.utc_datetime().timestamp(),
        "peak_el": peak_el,
        "aos_unix": above[0][0].utc_datetime().timestamp(),
        "los_unix": above[-1][0].utc_datetime().timestamp(),
        "n_above": len(above),
    }


def test_grazing_case_is_real_and_shorter_than_one_coarse_cell(reference):
    assert GATE_DEG < reference["peak_el"] < GATE_DEG + 0.02, reference
    span_s = reference["los_unix"] - reference["aos_unix"]
    assert 0.0 < span_s < 60.0, (
        f"过门限时长 {span_s:.0f}s，不足以证明 60s 粗扫会整段吞掉")


def test_predict_passes_detects_grazing_pass(reference):
    got = predict_passes(LINES, GS, start_unix=WHEN, hours=24.0,
                         min_alt=GATE_DEG)
    hit = [p for p in got
           if p.rise_time.utc_datetime().timestamp() <= reference["peak_unix"]
           <= p.set_time.utc_datetime().timestamp()]
    assert len(hit) == 1, (
        f"掠射场应恰好被检出 1 次，实际 {len(hit)} 次；"
        f"峰值 {reference['peak_el']:.4f}° / 门限 {GATE_DEG}°")
    p = hit[0]
    assert abs(p.max_alt - reference["peak_el"]) <= 0.01, (
        f"峰值 {p.max_alt:.4f}° vs 参照 {reference['peak_el']:.4f}°")
    aos = p.rise_time.utc_datetime().timestamp()
    los = p.set_time.utc_datetime().timestamp()
    assert abs(aos - reference["aos_unix"]) <= 1.5, f"AOS 偏差 {aos - reference['aos_unix']:+.2f}s"
    assert abs(los - reference["los_unix"]) <= 1.5, f"LOS 偏差 {los - reference['los_unix']:+.2f}s"
    # 命中是「细分找峰」，不是把细扫步长或二分容差改宽
    assert PASS_FINE_STEP_S <= 2.0


def test_gate_not_relaxed(reference):
    """门限抬到峰值之上后必须检不到：证明上一条不是靠放宽门限过关。"""
    stricter = reference["peak_el"] + 0.005
    got = predict_passes(LINES, GS, start_unix=WHEN, hours=24.0,
                         min_alt=stricter)
    hit = [p for p in got
           if p.rise_time.utc_datetime().timestamp() <= reference["peak_unix"]
           <= p.set_time.utc_datetime().timestamp()]
    assert not hit, (
        f"门限 {stricter:.4f}° 高于峰值 {reference['peak_el']:.4f}°，"
        "却仍返回该场，说明门限被放低了")


# ---------------------------------------------------------------------------
# K12 原场替代：NOAA-19 真实 TLE + 原站点（45°N/120°E/200 m）
#
# 原 K12 场（NOAA-19 峰值 5.025°）的 TLE 与观测时刻**已不可得**：
#   - `scripts/hw_acceptance/ring5_satellite_passes.py` 每次 `fetch_tle_record`
#     现取 TLE、窗口起点取 `time.time()`，运行产物不落库；
#   - `git log --all -S "5.025" / -S "31991" / -S "1 33591U"` 只命中台账文字与
#     真实 TLE 数据文件，没有任何带 TLE+时刻的 R5 输出；已删文件历史同样为空。
# 因此本组用仓库历史里真实出现过的 NOAA-19 TLE（epoch 26268.8504）在同一站点
# 造一个**等效掠射场**。**这是替代场，不是原场**，不宣称还原了原场。
# 门限仍 5.0°、二分容差仍 0.5 s。
NOAA_NAME = "NOAA 19"
NOAA_TLE = (
    "1 33591U 09005A   26268.85039999  .00000028  00000+0  38838-4 0  9994",
    "2 33591  98.9431 339.7518 0014811 104.9296 255.3517 14.13486977908701",
)
NOAA_LINES = [NOAA_NAME, NOAA_TLE[0], NOAA_TLE[1]]
NOAA_GS = GroundStation(lat_deg=45.0, lon_deg=120.0, alt_m=200.0)
NOAA_WHEN = datetime(2026, 10, 4, 0, 0, 0, tzinfo=timezone.utc).timestamp()
# 参照中心取 05:26:30：sample_trail 从 center-300s 起步，落在整 30 s 上，
# 30 s 采样点正好是 60 s 粗扫端点(:00)与单元中点(:30)。
NOAA_CENTER = (2026, 10, 4, 5, 26, 30)


@pytest.fixture(scope="module")
def noaa_reference():
    """1 s 参照：替代场的峰值与 5° 越门时刻（不走 predict_passes 的分段）。"""
    ts = make_timescale()
    center = ts.utc(datetime(*NOAA_CENTER, tzinfo=timezone.utc))
    samples = sample_trail(NOAA_LINES, NOAA_GS, center,
                           before_s=300.0, after_s=300.0, step_s=1.0)
    above = [s for s in samples if s[2] >= GATE_DEG]
    assert above, "参照网格必须找到 5° 以上采样"
    peak_t, _az, peak_el = max(samples, key=lambda s: s[2])
    return {
        "peak_unix": peak_t.utc_datetime().timestamp(),
        "peak_el": peak_el,
        "aos_unix": above[0][0].utc_datetime().timestamp(),
        "los_unix": above[-1][0].utc_datetime().timestamp(),
        "n_above": len(above),
    }


def test_noaa19_substitute_case_is_real_and_narrower_than_one_coarse_cell(
        noaa_reference):
    """替代场必须自身成立：真掠射、峰过门限、过门限时长 < 一个 60 s 单元。"""
    assert GATE_DEG < noaa_reference["peak_el"] < GATE_DEG + 0.02, noaa_reference
    span_s = noaa_reference["los_unix"] - noaa_reference["aos_unix"]
    assert 0.0 < span_s < 60.0, (
        f"过门限时长 {span_s:.0f}s，不足以证明 60s 粗扫会整段吞掉")


def test_noaa19_coarse_grid_and_midpoints_stay_below_gate(noaa_reference):
    """修复前的证据：60 s 端点 + 30 s 中点全部 < 5°，旧「只探中点」必 0 检出。"""
    ts = make_timescale()
    center = ts.utc(datetime(*NOAA_CENTER, tzinfo=timezone.utc))
    grid = sample_trail(NOAA_LINES, NOAA_GS, center,
                        before_s=300.0, after_s=300.0, step_s=30.0)
    # 起点对齐到整 30 s，因此这些点正是 60 s 网格端点(:00)与中点(:30)
    vals = [s[2] for s in grid]
    assert len(vals) >= 15, f"网格点太少：{len(vals)}"
    assert max(vals) < GATE_DEG, (
        f"网格最大仰角 {max(vals):.4f}° ≥ 门限 {GATE_DEG}°，"
        "本场就不能用来复现「粗扫整段吞掉」")
    assert noaa_reference["peak_el"] >= GATE_DEG


def test_predict_passes_detects_noaa19_substitute_grazing_pass(noaa_reference):
    """主判据：同一站点、同一 24 h 窗口，替代场必须被检出 1 次且精度达标。"""
    got = predict_passes(NOAA_LINES, NOAA_GS, start_unix=NOAA_WHEN,
                         hours=24.0, min_alt=GATE_DEG)
    hit = [p for p in got
           if p.rise_time.utc_datetime().timestamp() <= noaa_reference["peak_unix"]
           <= p.set_time.utc_datetime().timestamp()]
    assert len(hit) == 1, (
        f"NOAA-19 替代掠射场应恰好检出 1 次，实际 {len(hit)} 次；"
        f"峰值 {noaa_reference['peak_el']:.4f}° / 门限 {GATE_DEG}°")
    p = hit[0]
    assert abs(p.max_alt - noaa_reference["peak_el"]) <= 0.01, (
        f"峰值 {p.max_alt:.4f}° vs 参照 {noaa_reference['peak_el']:.4f}°")
    aos = p.rise_time.utc_datetime().timestamp()
    los = p.set_time.utc_datetime().timestamp()
    assert abs(aos - noaa_reference["aos_unix"]) <= 1.5, (
        f"AOS 偏差 {aos - noaa_reference['aos_unix']:+.2f}s")
    assert abs(los - noaa_reference["los_unix"]) <= 1.5, (
        f"LOS 偏差 {los - noaa_reference['los_unix']:+.2f}s")


def test_noaa19_gate_not_relaxed(noaa_reference):
    """门限抬到替代场峰值之上后必须 0 检出：证明确实没放低 5° 门限。"""
    stricter = noaa_reference["peak_el"] + 0.005
    got = predict_passes(NOAA_LINES, NOAA_GS, start_unix=NOAA_WHEN,
                         hours=24.0, min_alt=stricter)
    hit = [p for p in got
           if p.rise_time.utc_datetime().timestamp() <= noaa_reference["peak_unix"]
           <= p.set_time.utc_datetime().timestamp()]
    assert not hit, (
        f"门限 {stricter:.4f}° 高于峰值 {noaa_reference['peak_el']:.4f}°，"
        "却仍返回该场，说明门限被放低了")
