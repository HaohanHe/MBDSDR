# SPDX-License-Identifier: MIT
"""K12 门限边缘掠射过境：固定 TLE + 固定时刻的可复跑用例。

真实几何（ISS TLE 2026-297，站 45.0°N/122.60°E/200 m，24 h 窗口）：
峰值仰角 5.0062°，过 5° 门限只有 13 s；60 s 粗扫网格两端为 4.949°/4.749°，
峰落在单元中点之外（距中点 10.9 s，中点仰角 4.998°）。旧实现只探中点，
整场漏检。判据要求：门限仍为 5.0°、二分容差不动，仅靠单元内 2 s 细分找到峰。

反证：把擦边补点改回「只探单元中点」，本文件必红；把门限降到 5.0 以下
才检到同样会红（test_gate_not_relaxed 用 ref_peak+0.005 作反向对照）。
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
