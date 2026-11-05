# SPDX-License-Identifier: MIT
"""
tests/test_sky_fast_time.py
===========================
「快转下的一致性」三件套（×60 / ×300 / ×1800 假时钟推进）：

  1. 摄像机锁定：每帧靶位是当帧真 SGP4（独立对拍，不是缓存/上一帧），
     视图中心逐帧贴住目标；帧间中心位移 == 目标真实位移（不抽搐）。
     平滑跟随跟不上时（单帧位移 > LOCK_SMOOTH_MAX_STEP_DEG）必须一次性
     诚实提示「已改为逐帧直贴」，而不是悄悄滞后或抖动。
  2. 卫星轨迹时效：带锚点的「未来 10 分钟」轨迹在快转下按当前模拟时刻
     重算（旧锚点必红）；无 TLE 可重算时撤掉过期曲线；1x 语义不变。
  3. 过境面板与天空同一模拟时刻：面板 peak_unix / max_alt 与天空画出的
     卫星位置（同一坐标链、同一时刻）在容差内一致；已过去的峰值不再
     冒充「未来过境」；快转下按模拟时间差重算面板。

时间推进全部用显式假时钟（eng.set_unix + eng.tick(0.033)），不睡真实时间。
运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_fast_time.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view as rsv  # noqa: E402
from rf_sky_view import RFSkyView, angular_separation_deg  # noqa: E402
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402

# 测试锚点站址（SGP4 地面站夹具坐标，不代表任何个人位置）
LAT, LON = 43.9, 125.3
ISS = "ISS (ZARYA)"
NOAA19 = "NOAA 19"
LINE1_ISS, LINE2_ISS = BUILTIN_TLE[ISS]
LINE1_N19, LINE2_N19 = BUILTIN_TLE[NOAA19]
TLE = {ISS: (LINE1_ISS, LINE2_ISS), NOAA19: (LINE1_N19, LINE2_N19)}

# 探测得到的锚点：ISS 仰角≈55°，且 t0+220s 内始终保持 >9°。
# 三种倍率共用同一锚点，便于比较「单帧位移随倍率放大」。
_FAST_UNIX = 1790532300.0

# 帧数与采样跨度（保证测试覆盖 1-3 分钟模拟时间，且卫星不落到地平下）
RATES = [(60.0, 30), (300.0, 12), (1800.0, 3)]


def _jd(unix_s: float) -> float:
    return unix_s / 86400.0 + 2440587.5


def _azalt_at(name: str, unix_s: float):
    """与生产视图同一坐标链（真实 TLE + sgp4）的独立对拍值。"""
    from sgp4.api import Satrec
    line1, line2 = TLE[name]
    sat = Satrec.twoline2rv(line1, line2)
    st = orbit._state_from_satrec(sat, name, _jd(unix_s), LAT, LON, 0.0)
    if st is None:
        return None
    return float(st["azimuth"]), float(st["elevation"])


def _catalog() -> list:
    return [
        {"name": ISS, "line1": LINE1_ISS, "line2": LINE2_ISS,
         "freq_mhz": 145.8, "kind": "amateur",
         "tle_source": "Celestrak CATNR 25544（测试夹具）",
         "tle_epoch": orbit.tle_epoch_iso(LINE1_ISS)},
        {"name": NOAA19, "line1": LINE1_N19, "line2": LINE2_N19,
         "freq_mhz": 137.1, "kind": "weather",
         "tle_source": "Celestrak CATNR 33591（测试夹具）",
         "tle_epoch": orbit.tle_epoch_iso(LINE1_N19)},
    ]


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON)
    v.set_satellite_catalog(_catalog())
    return v


@pytest.fixture
def engine():
    eng = get_time_engine()
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


# ---------------------------------------------------------------------------
# 1. 摄像机锁定：当帧真 SGP4 + 贴中心 + 帧间连续 + 诚实提示
# ---------------------------------------------------------------------------
class TestFastRateCameraLock:
    @pytest.mark.parametrize("rate,frames", RATES)
    def test_center_tracks_true_sgp4_every_frame(self, engine, rate, frames):
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(rate)
        v = _new_view()
        assert v.set_camera_lock(ISS) is True

        prev_center = None
        prev_true = None
        max_step = 0.0
        for _ in range(frames):
            engine.tick(0.033)
            v._on_refresh_tick()
            now = engine.now_unix()
            true = _azalt_at(ISS, now)
            assert true is not None and true[1] > 0.0, "锚点内 ISS 应在地平线上"
            az, el = true

            # 靶位是当帧真 SGP4（不是缓存/上一帧）：中心与它贴合
            err = angular_separation_deg(
                v.view_state.center_az, v.view_state.center_alt, az, el)
            assert err < 0.05, f"×{rate:g} 第 {frames} 帧中心误差 {err:.3f}°"

            if prev_true is not None:
                disp = angular_separation_deg(
                    prev_center[0], prev_center[1],
                    v.view_state.center_az, v.view_state.center_alt)
                true_disp = angular_separation_deg(
                    prev_true[0], prev_true[1], az, el)
                # 帧间位移 == 目标真实位移：不抽搐、不追赶振荡
                assert abs(disp - true_disp) < 0.02, (
                    f"×{rate:g} 帧间位移 {disp:.3f}° ≠ 真位移 {true_disp:.3f}°")
                max_step = max(max_step, disp)

            prev_center = (v.view_state.center_az, v.view_state.center_alt)
            prev_true = true

        # 诚实提示：说清楚「跟不上 → 逐帧直贴」，不静默改手感
        assert "逐帧直贴" in v._camera_notice
        assert f"×{rate:g}" in v._camera_notice
        # 位移大到直贴也只能按帧跳进时，提示里必须说明（×1800 约 46°/帧）
        if max_step > v.LOCK_FAST_SLIDE_STEP_DEG:
            assert "按帧跳进" in v._camera_notice

    def test_normal_rate_keeps_exponential_smoothing(self, engine, monkeypatch):
        """1x（此处暂停）不触发快转直贴：仍是 25% 指数趋近，语义不回退。"""
        engine.set_rate(0.0)
        v = _new_view()
        v.view_state.center_az = 100.0
        v.view_state.center_alt = 30.0
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda name: (100.0, 33.0))
        v._camera_lock = ISS
        v._apply_camera_lock()
        assert abs(v.view_state.center_alt - 30.75) < 1e-6
        assert abs(v.view_state.center_az - 100.0) < 1e-6
        assert v._lock_snap_rate is None

    def test_smoothing_limit_still_smooth_below_threshold(self, engine,
                                                          monkeypatch):
        """单帧位移小于阈值（即使倍率>1）仍走平滑，不因倍率高就一律直贴。"""
        engine.set_rate(2.0)
        v = _new_view()
        v.view_state.center_az = 100.0
        v.view_state.center_alt = 30.0
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda name: (100.0, 30.1))
        v._camera_lock = ISS
        v._apply_camera_lock()
        assert abs(v.view_state.center_alt - 30.025) < 1e-6
        assert v._lock_snap_rate is None
        assert "逐帧直贴" not in v._camera_notice


# ---------------------------------------------------------------------------
# 2. 卫星轨迹时效：快转按当前模拟时刻重算；过期曲线不画；1x 语义不变
# ---------------------------------------------------------------------------
class TestFastRateTrajectory:
    def test_stale_trajectory_recomputed_at_current_sim_time(self, engine):
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(300.0)
        v = _new_view()
        stale = [_azalt_at(ISS, _FAST_UNIX + k * 60.0) for k in range(11)]
        v.set_trajectory(ISS, stale, anchor_unix=_FAST_UNIX, span_s=600.0)

        for _ in range(2):                 # 2 帧 ≈ 20 模拟秒 > 19.8s 过期阈
            engine.tick(0.033)
            v._on_refresh_tick()

        now = v._sat_positions_unix
        assert now is not None and now > _FAST_UNIX + 18.0
        # 锚点已跟到当前模拟时刻，曲线首点 = 当帧真 SGP4
        assert abs(v._traj_anchor[ISS] - now) < 1e-9
        az, el = _azalt_at(ISS, now)
        pts = v._trajectories[ISS]
        assert len(pts) == 11
        err = angular_separation_deg(pts[0][0], pts[0][1], az, el)
        assert err < 1e-6, f"轨迹首点偏离当帧位置 {err:.3f}°"
        # 旧锚点曲线确实已明显过期（证明是重算而不是原样画旧线）
        old = angular_separation_deg(stale[0][0], stale[0][1], az, el)
        assert old > 3.0, f"旧曲线只差 {old:.2f}°，用例没踩到过期区"

    def test_stale_trajectory_without_tle_dropped(self, engine):
        """无法重算 → 撤掉过期曲线（宁可不画，不画过期线）。"""
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(300.0)
        v = _new_view()
        v.set_trajectory("GHOST SAT", [(10.0, 20.0)],
                         anchor_unix=_FAST_UNIX, span_s=600.0)
        for _ in range(2):
            engine.tick(0.033)
            v._on_refresh_tick()
        assert "GHOST SAT" not in v._trajectories
        assert "GHOST SAT" not in v._traj_anchor

    def test_trajectory_untouched_at_normal_rate(self, engine):
        """1x：外部喂入即画，视图不自行重算（保持既有语义）。"""
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(1.0)
        v = _new_view()
        pts = [(1.0, 2.0), (3.0, 4.0)]
        v.set_trajectory(ISS, pts, anchor_unix=_FAST_UNIX)
        for _ in range(3):
            engine.tick(0.033)
            v._on_refresh_tick()
        assert v._trajectories[ISS] == pts
        assert v._traj_anchor[ISS] == _FAST_UNIX

    def test_trajectory_without_anchor_untouched_under_fast_time(self, engine):
        """旧调用（不给锚点）不被改写：视图不接管外部数据。"""
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(300.0)
        v = _new_view()
        pts = [(1.0, 2.0), (3.0, 4.0)]
        v.set_trajectory(ISS, pts)          # 无 anchor_unix
        for _ in range(2):
            engine.tick(0.033)
            v._on_refresh_tick()
        assert v._trajectories[ISS] == pts
        assert ISS not in v._traj_anchor


# ---------------------------------------------------------------------------
# 3. 过境面板与天空视图：同一模拟时刻、同一坐标链、容差内一致
# ---------------------------------------------------------------------------


class TestAngularSeparationConditioning:
    """角距函数的 float64 条件数守卫（1e-6° 级断言的前提）。

    旧实现 acos(v1·v2)：重复点也会因点积 1 位舍入返回 sqrt(2·eps)=
    2.107e-8 rad = 1.207e-6° 的虚假角距，使「同一时刻同一坐标链」
    在 1e-6° 阈值上随机翻红。这不是时刻抖动，容差也不是靠放宽修好的。
    """

    def test_identical_points_yield_exact_zero(self):
        # 第一组是实测翻红的坐标（旧实现给 8.538e-7°）
        for az, el in [(122.10841370007972, 55.75637724015688),
                       (0.0, 90.0), (0.0, -90.0), (359.999, -89.999),
                       (180.0, 0.0), (45.0, 44.9)]:
            assert angular_separation_deg(az, el, az, el) == 0.0

    def test_small_separation_is_not_floored(self):
        # 1e-6° 级小角距必须真被分辨出来，不能被条件数噪声吃掉
        for d in (1e-6, 5e-6, 1e-4, 1e-3, 0.01):
            s = angular_separation_deg(10.0, 20.0, 10.0, 20.0 + d)
            assert abs(s - d) <= d * 1e-6 + 1e-12, f"d={d} 得 {s}"

    def test_known_separations_unchanged(self):
        assert abs(angular_separation_deg(0.0, 0.0, 180.0, 0.0)
                   - 180.0) < 1e-9
        assert abs(angular_separation_deg(0.0, 0.0, 0.0, 90.0)
                   - 90.0) < 1e-9
        assert abs(angular_separation_deg(0.0, 0.0, 90.0, 0.0)
                   - 90.0) < 1e-9


@pytest.fixture
def tracker(engine, monkeypatch):
    """真实 SatelliteTracker（TLE 加载器替身，避免网络/用户目录依赖）。"""
    monkeypatch.setattr(rsv, "_load_user_tle_catalog", lambda: [])
    monkeypatch.setattr(rsv, "_load_celestrak_tle_catalog", lambda: _catalog())
    engine.set_unix(_FAST_UNIX)
    engine.set_rate(300.0)
    v = _new_view()
    trk = rsv.SatelliteTracker(v, LAT, LON, 0.0, interval_ms=10 ** 9)
    return v, trk


class TestFastRatePassPanel:
    def test_panel_peak_matches_sky_at_same_instant(self, engine, tracker):
        v, trk = tracker
        trk.refresh()
        rows = v._upcoming_passes
        assert rows, "真实 TLE 夹具在 24h 内应至少有一次过境"
        row = rows[0]
        assert "peak_unix" in row and "rise" in row
        peak = float(row["peak_unix"])

        # 拨到面板给出的峰值时刻：视图按当前模拟时刻重算天空位置
        engine.set_unix(peak)
        engine.set_rate(300.0)
        # 不钉时钟：obj 与 _azalt_at 都用当帧同一个 _sat_positions_unix。
        # 这条断言原先偶发红（1.207e-6° vs 1e-6°），根因不是取时刻抖动，而是
        # 角距函数的 float64 条件数下限：旧实现用 acos(v1·v2)，重复点也会
        # 得到 sqrt(2·eps)=2.107e-8 rad = **1.207e-6°** 的虚假角距（与观测值
        # 逐位吻合；实测同一组 az/el 重复算 8.5e-7~1.207e-6°）。角距已改为
        # atan2(|v1×v2|, v1·v2)，重复点精确返回 0；阈值维持 1e-6° 不放宽。
        v._on_refresh_tick()
        obj = next(o for o in v._objects if o.name == row["name"])
        t_sky = v._sat_positions_unix
        az, el = _azalt_at(row["name"], t_sky)
        # 天空画出的位置 = 独立 SGP4（同一坐标链、同一时刻）
        err = angular_separation_deg(obj.azimuth_deg, obj.elevation_deg, az, el)
        assert err < 1e-6, f"天空卫星位置偏离独立 SGP4 {err:.3f}°"
        # 面板 max_alt 与天空实际仰角在同一时刻一致（容差 0.05°）
        assert abs(obj.elevation_deg - float(row["max_alt"])) < 0.05

    def test_panel_time_display_is_second_resolution(self, tracker):
        """时刻给到秒：分钟取整会让近天顶过境与天空差出数度（真实缺陷）。"""
        v, trk = tracker
        trk.refresh()
        row = v._upcoming_passes[0]
        peak = float(row["peak_unix"])
        shown = datetime.fromtimestamp(peak, tz=timezone.utc).strftime("%H:%M:%S")
        assert row["rise"] == shown, f"显示 {row['rise']} ≠ 峰值 {shown}"
        assert len(row["rise"]) == 8

    def test_expired_peaks_not_shown_as_upcoming(self, engine):
        """已过去的峰值不再冒充「未来过境」（快转下 5s 墙钟=25 分钟模拟时间）。"""
        engine.set_unix(_FAST_UNIX)
        engine.set_rate(300.0)
        v = _new_view()
        v.set_upcoming_passes([
            {"name": ISS, "rise": "00:00:00", "max_alt": 40.0,
             "peak_unix": _FAST_UNIX - 60.0},
            {"name": NOAA19, "rise": "00:00:00", "max_alt": 20.0,
             "peak_unix": _FAST_UNIX + 600.0},
        ])
        v._on_refresh_tick()
        names = [p["name"] for p in v._upcoming_passes]
        assert ISS not in names
        assert NOAA19 in names

    def test_panel_recomputed_on_simulated_time(self, tracker, engine):
        """快转下按模拟时间差重算（≥PASS_REFRESH_SIM_S），不是 5s 墙钟。"""
        import time
        v, trk = tracker
        t0 = float(trk._passes_anchor_unix)
        trk._passes_wall = time.monotonic()   # 墙钟下限内，只测模拟时间判据
        trk._maybe_refresh_passes(_jd(t0 + 100.0), t0 + 100.0)
        assert abs(trk._passes_anchor_unix - t0) < 1e-4, "未到阈值不该重算"
        trk._passes_wall = time.monotonic()
        trk._maybe_refresh_passes(_jd(t0 + 400.0), t0 + 400.0)
        assert abs(trk._passes_anchor_unix - (t0 + 400.0)) < 1e-4

    def test_tracker_feeds_trajectory_with_anchor(self, tracker, engine):
        """控制器喂入轨迹时必须带锚点，否则视图无从判断过期。"""
        v, trk = tracker
        trk.refresh()
        assert ISS in v._traj_anchor
        assert abs(v._traj_anchor[ISS] - engine.now_unix()) < 1e-3
        assert v._traj_span_s[ISS] == 600.0

    def test_empty_catalog_clears_stale_panel_and_trajectory(self, tracker):
        """TLE 目录丢失后不得留下过期轨迹/过境（宁可空，不冒充现行数据）。"""
        v, trk = tracker
        trk.refresh()
        assert v._trajectories
        assert v._upcoming_passes
        trk._catalog = []
        trk.refresh()
        assert not v._trajectories
        assert not v._upcoming_passes
        assert not v._sat_catalog


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
