# SPDX-License-Identifier: MIT
"""地面轨迹 / 天空轨迹的步长误差界（以 1 s 采样为参照的实测判据）。

口径
----
- 地面轨迹：把相邻 ``DEFAULT_TRACK_STEP_S`` 秒的星下点按大圆短弧连线，
  每个 1 s 真位置到所在弦的最大大圆距离（km）。
- 天空轨迹：把相邻 ``TRAIL_STEP_S`` 秒的 (az, alt) 线性插值，
  每个 1 s 真方向到插值方向的最大角偏差（°）。

反证：把 ``DEFAULT_TRACK_STEP_S`` 改成 60 s，或把 ``TRAIL_STEP_S`` 改成
60 s，实测偏差会超过声明的上限，对应用例必红。
"""
import math
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.ground_track import (  # noqa: E402
    DEFAULT_TRACK_REF_STEP_S,
    DEFAULT_TRACK_STEP_MAX_CHORD_KM,
    DEFAULT_TRACK_STEP_S,
    ground_track,
)
from mbdsdr_ai.sat_passes import (  # noqa: E402
    TRAIL_STEP_MAX_ERR_DEG,
    TRAIL_STEP_S,
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
WHEN = datetime(2026, 10, 24, 6, 0, 0, tzinfo=timezone.utc).timestamp()
GS = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=0.0)
R_KM = 6371.0088


def _vec(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo),
            math.cos(la) * math.sin(lo), math.sin(la))


def _ang_deg(a, b):
    return math.degrees(math.acos(max(-1.0, min(
        1.0, sum(x * y for x, y in zip(a, b))))))


def _mid_vec(a, b):
    q = [(a[i] + b[i]) / 2.0 for i in range(3)]
    n = math.sqrt(sum(x * x for x in q))
    return (q[0] / n, q[1] / n, q[2] / n)


def _chord_error_km(fine, step_s):
    """1 s 参照点到相邻 step_s 弦的最大大圆距离（km）。"""
    step = int(step_s)
    worst, worst_i = 0.0, 0
    for k in range(len(fine) // step):
        i0, i1 = k * step, min((k + 1) * step, len(fine) - 1)
        a = _vec(fine[i0].lat_deg, fine[i0].lon_deg)
        b = _vec(fine[i1].lat_deg, fine[i1].lon_deg)
        mid = (i0 + i1) // 2
        p = _vec(fine[mid].lat_deg, fine[mid].lon_deg)
        km = math.radians(_ang_deg(p, _mid_vec(a, b))) * R_KM
        if km > worst:
            worst, worst_i = km, mid
    return worst, worst_i


def _sky_vec(az, alt):
    a, e = math.radians(az), math.radians(alt)
    return (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a),
            math.sin(e))


def _trail_error_deg(fine, coarse, step_s):
    step = int(step_s)
    worst = 0.0
    for i in range(len(fine)):
        k = min(i // step, len(coarse) - 2)
        frac = (i - k * step) / step
        _t0, az0, al0 = coarse[k]
        _t1, az1, al1 = coarse[k + 1]
        daz = az1 - az0
        if daz > 180.0:
            daz -= 360.0
        elif daz < -180.0:
            daz += 360.0
        q = _sky_vec(az0 + frac * daz, al0 + frac * (al1 - al0))
        _t, az, alt = fine[i]
        worst = max(worst, _ang_deg(_sky_vec(az, alt), q))
    return worst


@pytest.fixture(scope="module")
def fine_track():
    return ground_track(ISS_NAME, when_unix=WHEN, minutes=90.0,
                        step_s=DEFAULT_TRACK_REF_STEP_S, tle=ISS_TLE)["points"]


@pytest.fixture(scope="module")
def trail_samples():
    ts = make_timescale()
    passes = predict_passes([ISS_NAME, ISS_TLE[0], ISS_TLE[1]], GS,
                            start_unix=WHEN, hours=24.0, min_alt=10.0)
    assert passes, "参考窗口内应有 ISS 过境"
    peak = max(passes, key=lambda p: p.max_alt)
    center = ts.utc(peak.max_alt_time.utc_datetime())
    fine = sample_trail([ISS_NAME, ISS_TLE[0], ISS_TLE[1]], GS, center,
                        before_s=600.0, after_s=600.0, step_s=1.0)
    return center, fine


def test_default_track_step_chord_error_within_declared_bound(fine_track):
    err, at = _chord_error_km(fine_track, DEFAULT_TRACK_STEP_S)
    assert DEFAULT_TRACK_STEP_MAX_CHORD_KM * 0.8 <= err <= \
        DEFAULT_TRACK_STEP_MAX_CHORD_KM, (
            f"{DEFAULT_TRACK_STEP_S:g}s 折线最大偏差 {err:.4f} km（i={at}）"
            f" 超出实测界 [{DEFAULT_TRACK_STEP_MAX_CHORD_KM * 0.8:.3f}, "
            f"{DEFAULT_TRACK_STEP_MAX_CHORD_KM:.3f}] km")


def test_60s_track_step_exceeds_declared_bound(fine_track):
    """界必须绑定默认步长：60 s 的实测偏差要明显超过 30 s 的上限。"""
    err, _at = _chord_error_km(fine_track, 60.0)
    assert err > DEFAULT_TRACK_STEP_MAX_CHORD_KM, (
        f"60 s 偏差 {err:.4f} km 未超过 {DEFAULT_TRACK_STEP_MAX_CHORD_KM} km，"
        "说明上限没有绑定默认步长")


def test_trail_default_step_error_within_declared_bound(trail_samples):
    center, fine = trail_samples
    coarse = sample_trail([ISS_NAME, ISS_TLE[0], ISS_TLE[1]], GS, center,
                          before_s=600.0, after_s=600.0, step_s=TRAIL_STEP_S)
    err = _trail_error_deg(fine, coarse, TRAIL_STEP_S)
    assert TRAIL_STEP_MAX_ERR_DEG * 0.8 <= err <= TRAIL_STEP_MAX_ERR_DEG, (
        f"{TRAIL_STEP_S:g}s trail 最大角偏差 {err:.4f}° 超出实测界 "
        f"[{TRAIL_STEP_MAX_ERR_DEG * 0.8:.2f}, {TRAIL_STEP_MAX_ERR_DEG:.2f}]°")


def test_60s_trail_step_exceeds_declared_bound(trail_samples):
    center, fine = trail_samples
    coarse = sample_trail([ISS_NAME, ISS_TLE[0], ISS_TLE[1]], GS, center,
                          before_s=600.0, after_s=600.0, step_s=60.0)
    err = _trail_error_deg(fine, coarse, 60.0)
    assert err > TRAIL_STEP_MAX_ERR_DEG, (
        f"60 s trail 偏差 {err:.3f}° 未超过 {TRAIL_STEP_MAX_ERR_DEG}°，"
        "说明上限没有绑定默认步长")
