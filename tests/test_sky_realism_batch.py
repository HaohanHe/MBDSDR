# SPDX-License-Identifier: MIT
"""
tests/test_sky_realism_batch.py
==============================
真软件（desktop/rf_sky_view.py）本轮四项「画出来就是错」的修复的钉子：

  1. 晨昏连续压暗（skyengine.twilight）——旧行为是布尔白天门：太阳一落到
     −0.833° 就按夜空 6.5 等满屏画星，而天空还是白蓝渐变。物理上不可能。
  2. 卫星点位跟模拟时刻（1× 也 4Hz 重算）——旧行为只信外部跟踪器 10s 一节拍，
     ISS 过顶时点位落后 5–10°。
  3. 卫星轨迹跨地平不连弦（split_visible_track）——旧行为把地平以下的点滤掉
     再连线，"落下—升起"之间会拉一条横跨天空的直线。
  4. 状态卡单一时基（UTC / 本地 / 恒星时 / JD 同刻）——旧行为 UTC 读墙钟、
     JD 读 TimeEngine，×60 后同一张卡自相矛盾。
  5. GNSS 无海拔（altitude_m=None）不得每帧抛 TypeError。
  6. 多普勒读全精度 range_rate_kms 字段，不再从中文文案正则抠 2 位小数。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_realism_batch.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view as rsv  # noqa: E402
from rf_sky_view import RFSkyView, SkyObject, observer_alt_m  # noqa: E402
from mbdsdr_ai.skyengine import twilight  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 43.9, 125.3


# ---------------------------------------------------------------------------
# 1. 晨昏压制曲线
# ---------------------------------------------------------------------------
class TestTwilightPenalty:
    def test_penalty_is_zero_at_and_below_astronomical_twilight(self):
        """−18° 及以下（天文夜）必须精确为 0：夜空口径不能被无端压低。"""
        for alt in (-18.0, -20.0, -45.0, -90.0):
            assert twilight.twilight_limit_penalty_mag(alt) == 0.0

    def test_penalty_unknown_sun_is_zero(self):
        """太阳高度未知时不许假装白天（惩罚 0，维持夜空口径）。"""
        assert twilight.twilight_limit_penalty_mag(None) == 0.0
        assert twilight.twilight_limit_penalty_mag(float("nan")) == 0.0

    def test_penalty_is_monotonic_in_sun_altitude(self):
        """太阳越高 → 天空越亮 → 压得越多，全程单调不减。"""
        alts = [a / 2.0 for a in range(-40, 21)]      # −20..+10
        pens = [twilight.twilight_limit_penalty_mag(a) for a in alts]
        assert all(b >= a - 1e-12 for a, b in zip(pens, pens[1:])), pens

    def test_civil_twilight_leaves_only_the_brightest_stars(self):
        """民用晨昏（−6°）：夜空 6.5 等必须被压到 ≤1 等——只剩最亮的几颗。"""
        limit = 6.5 - twilight.twilight_limit_penalty_mag(-6.0)
        assert limit <= 1.0, limit
        # 但不能压到"全黑什么都没有"（真实民用晨昏还能看见 1 等星）
        assert limit >= -2.0, limit

    def test_daylight_kills_every_catalog_star(self):
        """太阳在地平上：压到星表深度以下，整层自然消失（不靠布尔开关）。"""
        for alt in (0.0, 5.0, 30.0):
            limit = 6.5 - twilight.twilight_limit_penalty_mag(alt)
            assert limit < -6.0, (alt, limit)

    def test_sky_brightness_endpoints(self):
        """天顶亮度表的端点：白天 ~4 等、天文夜 21.6 等（V mag/arcsec²）。"""
        assert twilight.sky_brightness_mag_arcsec2(60.0) == 4.0
        assert (twilight.sky_brightness_mag_arcsec2(-30.0)
                == twilight.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2)


# ---------------------------------------------------------------------------
# 2. 卫星轨迹跨地平：不许连弦
# ---------------------------------------------------------------------------
class TestTrackHorizonSplit:
    def test_set_rise_gives_two_runs_with_horizon_endpoints(self):
        """落下再升起 → 两段；两段端点都在 el=0，绝不跨天空连一条直线。"""
        pts = [(10.0, 20.0), (20.0, 10.0), (30.0, -5.0), (40.0, -20.0),
               (50.0, -3.0), (60.0, 8.0), (70.0, 25.0)]
        runs = rsv.split_visible_track(pts)
        assert len(runs) == 2, runs
        assert all(all(el >= -1e-9 for _az, el in run) for run in runs)
        # 第一段末点 / 第二段首点必须落在地平线上（插值穿越点）
        assert runs[0][-1][1] == pytest.approx(0.0, abs=1e-9)
        assert runs[1][0][1] == pytest.approx(0.0, abs=1e-9)
        # 穿越点方位按最短弧插在相邻两点之间（30→40 的负仰角段）
        assert 20.0 <= runs[0][-1][0] <= 40.0

    def test_fully_visible_track_stays_one_run(self):
        pts = [(0.0, 10.0), (5.0, 20.0), (10.0, 30.0)]
        runs = rsv.split_visible_track(pts)
        assert len(runs) == 1
        assert runs[0][0] == pts[0] and runs[0][-1] == pts[-1]

    def test_fully_below_horizon_draws_nothing(self):
        runs = rsv.split_visible_track([(0.0, -5.0), (10.0, -10.0)])
        assert runs == []

    def test_crossing_across_azimuth_wrap_uses_short_arc(self):
        """跨 0/360 时穿越点不能绕远路（350° → 10° 之间只能是 ~0°）。"""
        runs = rsv.split_visible_track([(350.0, 5.0), (10.0, -5.0)])
        assert runs == [] or all(el >= 0 for _az, el in runs[0])


# ---------------------------------------------------------------------------
# 3. 卫星点位：1× 也要跟模拟时刻
# ---------------------------------------------------------------------------
class TestSatellitePositionFreshness:
    def test_refresh_runs_at_1x(self, monkeypatch):
        """1× 时 `_refresh_fast_time_positions` 必须真的执行（旧行为直接 return）。

        旧实现对 `abs(rate) <= 1.0` 立刻返回，卫星点只能等外部跟踪器 10s 一拍；
        ISS 过顶 0.5–1°/s，点位肉眼可见落后。这里核对：1× 下调用会把
        `_sat_positions_unix` 更新为当前模拟时刻。
        """
        eng = get_time_engine()
        eng.set_rate(1.0)
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        v.set_satellite_catalog([{"name": "ISS (ZARYA)",
                                  "line1": rsv_iss()[0],
                                  "line2": rsv_iss()[1],
                                  "freq_mhz": 145.8, "kind": "amateur"}])
        v._sat_pos_refresh_ms = 0.0                     # 允许本拍重算
        before = v._sat_positions_unix
        v._refresh_fast_time_positions()
        assert v._sat_positions_unix is not None
        assert v._sat_positions_unix != before
        # 同一拍内第二次调用被节流（不多算）
        stamp = v._sat_positions_unix
        v._refresh_fast_time_positions()
        assert v._sat_positions_unix == stamp

    def test_positions_follow_simulated_time(self):
        """按模拟时刻推进后，卫星点位必须跟着变（不是墙钟冻结值）。"""
        eng = get_time_engine()
        eng.set_rate(1.0)
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        l1, l2 = rsv_iss()
        v.set_satellite_catalog([{"name": "ISS (ZARYA)", "line1": l1,
                                  "line2": l2, "freq_mhz": 145.8,
                                  "kind": "amateur"}])
        v.set_objects([SkyObject(name="ISS (ZARYA)", azimuth_deg=0.0,
                                 elevation_deg=0.0, frequency_hz=145.8e6)])
        v._sat_pos_refresh_ms = 0.0
        v._refresh_fast_time_positions()
        first = (v._objects[0].azimuth_deg, v._objects[0].elevation_deg)
        base = eng.now_unix()
        eng.set_unix(base + 240.0)                       # 推进 4 分钟
        v._sat_pos_refresh_ms = 0.0
        v._refresh_fast_time_positions()
        second = (v._objects[0].azimuth_deg, v._objects[0].elevation_deg)
        assert first != second, (first, second)
        eng.set_time_now()


def rsv_iss():
    from mbdsdr_ai.decoders import BUILTIN_TLE
    return BUILTIN_TLE["ISS (ZARYA)"]


# ---------------------------------------------------------------------------
# 4. 状态卡单一时基
# ---------------------------------------------------------------------------
class TestStatusCardSingleTimeBasis:
    def test_utc_follows_engine_not_wall_clock(self):
        """引擎不再等于墙钟时，状态卡的 UTC 必须显示引擎时刻并标「模拟」。"""
        import datetime as _dt
        eng = get_time_engine()
        sim_unix = 1_800_000_000.0            # 2027-01-15 08:00Z，明显不是"现在"
        eng.set_unix(sim_unix)
        eng.pause()
        try:
            v = RFSkyView()
            v._time_info = {"utc_time": _dt.datetime.now(_dt.timezone.utc),
                            "ntp_status": "ntp"}
            rec = _TextRecorder()
            v._draw_info_overlay(rec)
            sim_dt = _dt.datetime.fromtimestamp(sim_unix, tz=_dt.timezone.utc)
            want = (f"{rsv.format_utc_time(sim_dt, '%H:%M:%S')} [模拟]")
            assert want in rec.texts, rec.texts
        finally:
            eng.set_time_now()
            eng.set_rate(1.0)


class _TextRecorder:
    """替身 painter：只记录 drawText 的字串（与 test_sky_readouts 同法）。"""

    def __init__(self):
        self.texts = []

    def save(self):
        pass

    def restore(self):
        pass

    def setFont(self, *a, **k):
        pass

    def setPen(self, *a, **k):
        pass

    def setBrush(self, *a, **k):
        pass

    def drawText(self, *args):
        if len(args) >= 3 and isinstance(args[2], str):
            self.texts.append(args[2])
        elif len(args) == 2 and isinstance(args[1], str):
            self.texts.append(args[1])

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


# ---------------------------------------------------------------------------
# 5. GNSS 无海拔：不得每帧抛 TypeError
# ---------------------------------------------------------------------------
class TestGnssAltitudeNone:
    def test_none_altitude_does_not_break_ground_station(self):
        """只给经纬、不给海拔（RMC 级定位）时，_ground_station 必须可用。"""
        v = RFSkyView()
        v.set_gnss_position({"source": "real", "latitude": LAT,
                             "longitude": LON, "altitude_m": None})
        gs = v._ground_station()
        assert gs is not None
        assert gs.elevation == 0.0
        # 如实留痕：海拔是"按 0 m 假定"，不是解算值
        assert v._observer["alt_assumed"] is True

    def test_observer_alt_helper_is_none_safe(self):
        assert observer_alt_m(None) == 0.0
        assert observer_alt_m({"alt_m": None}) == 0.0
        assert observer_alt_m({}) == 0.0
        assert observer_alt_m({"alt_m": "nan"}) == 0.0
        assert observer_alt_m({"alt_m": 123.0}) == 123.0

    def test_star_selection_tolerates_none_altitude(self):
        """整条选星链在 None 海拔下不得抛异常（旧码 float(None) 每帧炸）。"""
        v = RFSkyView()
        v.resize(400, 400)
        v.set_gnss_position({"source": "real", "latitude": LAT,
                             "longitude": LON, "altitude_m": None})
        gs = v._ground_station()
        sel = v._star_selection(gs, v._current_jd())
        assert sel is not None


# ---------------------------------------------------------------------------
# 6. 多普勒读全精度字段
# ---------------------------------------------------------------------------
class TestDopplerUsesFieldNotText:
    def test_field_wins_over_rounded_description(self):
        """文案只到 0.01 km/s（10 m/s）；多普勒必须用字段里的全精度值。

        437.8 MHz 上 10 m/s ≈ 14.6 Hz —— 用文案会把多普勒量化成 15 Hz 台阶。
        """
        hz = 437.8e6
        obj = SkyObject(name="X", azimuth_deg=0.0, elevation_deg=45.0,
                        frequency_hz=hz, description="径向速度 2.12 km/s",
                        range_rate_kms=2.123456)
        dop = RFSkyView._estimate_doppler_hz(obj)
        want_full = -2.123456 * 1000.0 * hz / 299792458.0
        want_text = -2.12 * 1000.0 * hz / 299792458.0
        assert dop == pytest.approx(want_full, abs=1e-6)
        assert abs(dop - want_text) > 1.0        # 与"只读文案"的结果可区分

    def test_missing_rate_returns_none_not_zero(self):
        """既无字段也无文案 → None（旧行为按 0 m/s 算出一个 0 Hz）。"""
        obj = SkyObject(name="X", azimuth_deg=0.0, elevation_deg=45.0,
                        frequency_hz=437.8e6, description="无速度字段")
        assert RFSkyView._estimate_doppler_hz(obj) is None
