# SPDX-License-Identifier: MIT
"""P11 可见性三入口同一时间基准回归（L23 缺陷）。

同一模拟时刻、同一 TLE/站址下：
  * ``sky_view_visible``（decoders/TimeEngine 口径）
  * ``sdr_satellite_sky_view``（orbit.visible_satellites，须接受显式时刻）
  * 过境预测（sat_passes，显式 start_time）
三者对 NOAA 19 的「是否可见」必须一致；工具缺省时刻也必须取 TimeEngine，
而不是墙钟。

运行：python -m pytest mbdsdr_ai/tests/test_visibility_time_basis.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import orbit  # noqa: E402
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.sat_passes import GroundStation, make_timescale, predict_passes  # noqa: E402

NAME = "NOAA 19"
LINES = list(BUILTIN_TLE[NAME][:2])
GS = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0)
START = make_timescale().utc(2026, 9, 25, 0, 0, 0)


def _pass_and_time():
    passes = predict_passes(LINES, GS, start_time=START, hours=24.0,
                            min_alt=10.0)
    assert passes
    p = passes[1]   # 2026-09-25 11:32:34Z，最大仰角 ~33°
    return p, p.max_alt_time.utc_datetime().timestamp()


@pytest.fixture()
def same_tle_for_orbit(monkeypatch):
    """让 orbit 的 NOAA 19 使用与 decoders/过境预测相同的 TLE，
    隔离 TLE 源差异，单独钉住时间基准。"""
    catnr = orbit.BUILTIN_SATS[NAME]

    def fake_fetch_tle(n):
        if int(n) == int(catnr):
            return LINES[0], LINES[1]
        raise RuntimeError("test: 其他卫星不参与本次断言")

    monkeypatch.setattr(orbit, "fetch_tle", fake_fetch_tle)
    return catnr


def test_three_entries_agree_at_same_instant(same_tle_for_orbit):
    from mbdsdr_ai.sdr_tools import _satellite_sky_view, _sky_view_visible

    p, t_mid = _pass_and_time()

    # 1) 过境预测：该时刻位于过境窗口内，最大仰角 >= 10°
    assert p.max_alt >= 10.0
    assert p.rise_time.utc_datetime().timestamp() <= t_mid <= \
        p.set_time.utc_datetime().timestamp()

    # 2) sky_view_visible（decoders 口径，显式 timestamp）
    txt = _sky_view_visible({"observer_lat": GS.lat_deg,
                             "observer_lon": GS.lon_deg,
                             "min_elevation": 10.0,
                             "timestamp": t_mid})
    assert NAME in txt
    assert "时刻:" in txt and "本地" in txt   # UTC 与本地偏移都可见

    # 3) sdr_satellite_sky_view（orbit sgp4 口径，显式 timestamp）
    txt2 = _satellite_sky_view({"latitude": GS.lat_deg,
                                "longitude": GS.lon_deg,
                                "altitude": GS.alt_m,
                                "min_elevation": 10.0,
                                "satellite_type": "weather",
                                "timestamp": t_mid})
    assert NAME in txt2
    assert "时刻:" in txt2 and "本地" in txt2

    # 4) orbit.visible_satellites 直接结论
    vis = orbit.visible_satellites(GS.lat_deg, GS.lon_deg, GS.alt_m / 1000.0,
                                   10.0, "weather", when=t_mid)
    names = [v["satellite"] for v in vis]
    assert NAME in names
    el = next(v["elevation"] for v in vis if v["satellite"] == NAME)
    assert abs(el - p.max_alt) < 1.0   # 同一 TLE/时刻下仰角应贴合


def test_tool_default_uses_time_engine_not_wall_clock(same_tle_for_orbit, monkeypatch):
    """不传 timestamp 时，sdr 工具必须用 TimeEngine 仿真时刻（修复 L23）。"""
    from mbdsdr_ai.new_spacetime import get_time_engine
    from mbdsdr_ai.sdr_tools import _satellite_sky_view

    _, t_mid = _pass_and_time()
    eng = get_time_engine()
    eng.set_utc(datetime.fromtimestamp(t_mid, tz=timezone.utc))
    eng.set_rate(0.0)
    try:
        txt = _satellite_sky_view({"latitude": GS.lat_deg,
                                   "longitude": GS.lon_deg,
                                   "altitude": GS.alt_m,
                                   "min_elevation": 10.0,
                                   "satellite_type": "weather"})
    finally:
        eng.set_time_now()
        eng.set_rate(1.0)
    assert NAME in txt


def test_predict_pass_accepts_explicit_start_unix():
    """同类过境入口（new_spacetime/sdr 工具）也须接受显式时刻。"""
    from mbdsdr_ai.new_spacetime import predict_satellite_pass
    from mbdsdr_ai.sdr_tools import _satellite_predict_pass

    start = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc).timestamp()
    p = predict_satellite_pass(NAME, GS.lat_deg, GS.lon_deg, GS.alt_m,
                               24.0, 5.0, start_unix=start)
    assert p is not None
    rise = p.rise_time.timestamp()
    assert rise >= start                       # 起点不回退
    assert p.set_time.timestamp() > rise       # 不允许负时长

    # 同一 TLE 换起点，首次过境必须重算（不沿用墙钟/上一时刻）
    p2 = predict_satellite_pass(NAME, GS.lat_deg, GS.lon_deg, GS.alt_m,
                                24.0, 5.0, start_unix=start + 6 * 3600.0)
    assert p2 is not None
    assert p2.rise_time.timestamp() >= start + 6 * 3600.0

    txt = _satellite_predict_pass({"satellite_name": NAME,
                                   "observer_lat": GS.lat_deg,
                                   "observer_lon": GS.lon_deg,
                                   "observer_alt": GS.alt_m,
                                   "hours_ahead": 24.0,
                                   "timestamp": start})
    assert isinstance(txt, str) and txt


def test_predict_pass_default_matches_time_engine():
    """缺省时刻 = TimeEngine 仿真时刻（不是墙钟）。"""
    from mbdsdr_ai.new_spacetime import get_time_engine, predict_satellite_pass

    start = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc).timestamp()
    eng = get_time_engine()
    eng.set_utc(datetime.fromtimestamp(start, tz=timezone.utc))
    eng.set_rate(0.0)
    try:
        p_default = predict_satellite_pass(NAME, GS.lat_deg, GS.lon_deg,
                                           GS.alt_m, 24.0, 5.0)
    finally:
        eng.set_time_now()
        eng.set_rate(1.0)
    p_explicit = predict_satellite_pass(NAME, GS.lat_deg, GS.lon_deg,
                                        GS.alt_m, 24.0, 5.0, start_unix=start)
    assert p_default is not None and p_explicit is not None
    assert abs(p_default.rise_time.timestamp()
               - p_explicit.rise_time.timestamp()) <= 60.0


def test_visible_satellites_honours_explicit_when(same_tle_for_orbit):
    """显式时刻不能退化为墙钟；两个不同时刻结论可以不同。"""
    _, t_mid = _pass_and_time()
    later = t_mid + 6 * 3600.0
    a = orbit.visible_satellites(GS.lat_deg, GS.lon_deg, GS.alt_m / 1000.0,
                                 10.0, "weather", when=t_mid)
    b = orbit.visible_satellites(GS.lat_deg, GS.lon_deg, GS.alt_m / 1000.0,
                                 10.0, "weather", when=later)
    assert any(v["satellite"] == NAME for v in a)
    # b 的结论来自 later 时刻，不应与 a 相同列表（同 TLE 下 6h 后几何已变）
    assert [v["satellite"] for v in a] != [v["satellite"] for v in b] or \
        any(abs(v["elevation"] - w["elevation"]) > 1.0
            for v, w in zip(a, b))
