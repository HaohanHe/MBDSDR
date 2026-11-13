# SPDX-License-Identifier: MIT
"""两套过境采样步长收敛到单一事实源（sat_passes）+ 两面板对拍。

修前红：
- `rf_sky_view.SatelliteTracker.PASS_COARSE_STEP_S=300` / `PASS_FINE_STEP_S=30`
  ≠ `sat_passes.PASS_COARSE_STEP_S=60` / `PASS_FINE_STEP_S=2`；
- 同 TLE/站点/时刻，5 分钟粗扫把 6 分钟 LEO 过境的峰值时刻最多推偏 15s、
  峰值仰角按 0.2–0.5°/s 可差 3–7.5°；AOS/LOS 差数十秒。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import sat_passes  # noqa: E402
from mbdsdr_ai.sat_passes import GroundStation, predict_passes  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
FIXED_UNIX = FIXED_UTC.timestamp()
GS = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=0.0)
TLE = [ISS_NAME, ISS_L1, ISS_L2]


def _skyfield_alt(lat, lon, alt_m, unix_ts):
    from skyfield.api import EarthSatellite, Topos

    ts = sat_passes.make_timescale()
    sat = EarthSatellite(ISS_L1, ISS_L2, ISS_NAME, ts)
    topo = Topos(latitude_degrees=lat, longitude_degrees=lon,
                 elevation_m=alt_m)
    t = ts.utc(datetime.fromtimestamp(unix_ts, tz=timezone.utc))
    alt, _az, _d = (sat - topo).at(t).altaz()
    return float(alt.degrees)


def _peak_elevation_rate(peak_unix):
    """峰值附近实测仰角速率（°/s），用于把「步长×速率」写成可验证的界。"""
    samples = [_skyfield_alt(39.9, 116.4, 0.0, peak_unix + ds)
               for ds in range(-60, 61, 1)]
    return max(abs(b - a) for a, b in zip(samples, samples[1:]))


def _ref_passes(hours=6.0):
    return predict_passes(TLE, GS, start_unix=FIXED_UNIX, hours=hours,
                          min_alt=5.0)


def _nearest(ref, alt, unix):
    return min(ref, key=lambda p: abs((p.max_alt_time.utc_datetime().timestamp()
                                       - unix)) + abs(p.max_alt - alt))


def test_step_constants_single_source():
    """rf_sky_view 不再私藏 300/30，直接引用 sat_passes 的公开常量。"""
    from desktop import rf_sky_view

    assert rf_sky_view.SatelliteTracker.PASS_COARSE_STEP_S == \
        sat_passes.PASS_COARSE_STEP_S
    assert rf_sky_view.SatelliteTracker.PASS_FINE_STEP_S == \
        sat_passes.PASS_FINE_STEP_S
    assert sat_passes.PASS_COARSE_STEP_S == 60.0
    assert sat_passes.PASS_FINE_STEP_S == 2.0


def test_orbit_step_constants_come_from_single_source():
    """orbit 的粗扫/细扫/二分容差与主链逐值相等（反证：任改一个必红）。

    旧实现 orbit 自写 bisect=0.25 s 而 sat_passes=0.5 s，同一个
    ``PASS_ALGORITHM`` 口径在两个面板里各说一个值。
    """
    from mbdsdr_ai import orbit

    assert orbit.PASS_COARSE_STEP_S == sat_passes.PASS_COARSE_STEP_S
    assert orbit.PASS_FINE_STEP_S == sat_passes.PASS_FINE_STEP_S
    assert orbit.PASS_BISECT_TOL_S == sat_passes.PASS_BISECT_TOL_S
    assert orbit.PASS_ALGORITHM == (
        f"sgp4-coarse{sat_passes.PASS_COARSE_STEP_S:g}"
        f"-fine{sat_passes.PASS_FINE_STEP_S:g}"
        f"-bisect{sat_passes.PASS_BISECT_TOL_S:g}")
    assert "bisect0.5" in orbit.PASS_ALGORITHM


def test_rf_sky_peak_matches_single_source():
    """rf 天空图峰值 vs sat_passes 参考：差 ≤ 粗步长×实测仰角速率。"""
    from desktop.rf_sky_view import compute_pass_peaks

    ref = _ref_passes(hours=6.0)
    assert ref, "参考预测不应为空"
    best_ref = max(ref, key=lambda p: p.max_alt)
    rows = compute_pass_peaks(
        [{"name": ISS_NAME, "line1": ISS_L1, "line2": ISS_L2}],
        39.9, 116.4, 0.0, FIXED_UNIX,
        min_peak_deg=5.0, win_s=6 * 3600.0)
    assert rows, "rf 天空图应至少给出一条过境"

    peak_unix = _peak_unix(best_ref)
    rate = _peak_elevation_rate(peak_unix)
    bound = sat_passes.PASS_COARSE_STEP_S * rate

    assert abs(rows[0]["peak_unix"] - peak_unix) <= \
        sat_passes.PASS_COARSE_STEP_S, "峰值时刻偏离超过一个粗扫步长"
    assert abs(rows[0]["max_alt"] - best_ref.max_alt) <= bound, (
        f"峰值仰角差 {abs(rows[0]['max_alt'] - best_ref.max_alt):.2f}° "
        f"超过 步长×速率={bound:.2f}°")


def test_timeline_aos_los_peak_within_step():
    """新时空面板（TimelineEngine）vs 参考：AOS/LOS ≤ 1 粗步长，峰值仰角 ≤ 步长×速率。"""
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    ref = _ref_passes(hours=6.0)
    assert ref

    # 时间基准走 TimeEngine；这里显式钉死预测起点，只比较网格差异。
    got = TimelineEngine().compute_passes(
        39.9, 116.4, 0.0,
        [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=6.0, min_elevation=5.0,
        step_sec=sat_passes.PASS_COARSE_STEP_S, now_unix=FIXED_UNIX)

    used = set()
    for p in got:
        unix_r = p.rise_time.timestamp()
        unix_s = p.set_time.timestamp()
        r = min((x for i, x in enumerate(ref) if i not in used),
                key=lambda x: abs(x.max_alt_time.utc_datetime().timestamp()
                                  - p.max_time.timestamp()))
        used.add(ref.index(r))
        rate = _peak_elevation_rate(r.max_alt_time.utc_datetime().timestamp())
        bound = sat_passes.PASS_COARSE_STEP_S * rate
        assert abs(unix_r - r.rise_time.utc_datetime().timestamp()) \
            <= sat_passes.PASS_COARSE_STEP_S, "AOS 偏离超过一个粗扫步长"
        assert abs(unix_s - r.set_time.utc_datetime().timestamp()) \
            <= sat_passes.PASS_COARSE_STEP_S, "LOS 偏离超过一个粗扫步长"
        assert abs(p.max_elevation - r.max_alt) <= bound, (
            f"峰值仰角差 {abs(p.max_elevation - r.max_alt):.2f}° "
            f"超过 步长×速率={bound:.2f}°")


def test_rf_gate_and_low_elevation_flag():
    """rf 天空图开关：10° 门不列 6.78° 过境；5° 门列出并标 low_elevation。"""
    from desktop.rf_sky_view import compute_pass_peaks

    # 49.9°N 的 10:00–12:00Z 窗口内 ISS 只有一次 6.78° 过境
    t0 = datetime(2008, 9, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    base = compute_pass_peaks(
        [{"name": ISS_NAME, "line1": ISS_L1, "line2": ISS_L2}],
        49.9, 116.4, 0.0, t0,
        min_peak_deg=10.0, low_peak_deg=None, win_s=2 * 3600.0)
    on = compute_pass_peaks(
        [{"name": ISS_NAME, "line1": ISS_L1, "line2": ISS_L2}],
        49.9, 116.4, 0.0, t0,
        min_peak_deg=5.0, low_peak_deg=10.0, win_s=2 * 3600.0)

    assert base == [], f"10° 门不应列出 6.78° 过境，实际 {base}"
    assert len(on) == 1, f"开关打开后应多出 1 条低仰角过境，实际 {on}"
    assert abs(on[0]["max_alt"] - 6.78) <= 1.0, f"低仰角峰值异常：{on[0]}"
    assert on[0]["low_elevation"] is True, f"未标注低仰角：{on[0]}"


def _peak_unix(p):
    return p.max_alt_time.utc_datetime().timestamp()
