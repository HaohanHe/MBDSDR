# SPDX-License-Identifier: MIT
"""P11 三路径 az/el 一致性守卫（过境 / 天空视图 / 多普勒）。

运行：python -m pytest mbdsdr_ai/tests/test_sat_consistency.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import orbit  # noqa: E402
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.sat_consistency import (  # noqa: E402
    check_next_pass_consistency,
    check_pass_object,
    check_time_consistency,
)
from mbdsdr_ai.sat_passes import GroundStation, make_timescale, predict_passes  # noqa: E402

GS = GroundStation(lat_deg=45.0, lon_deg=120.0, alt_m=0.0)
LINES = list(BUILTIN_TLE["NOAA 15"][:2])
START = make_timescale().utc(2026, 9, 26, 0, 0, 0)


def _first_pass(min_alt: float = 5.0):
    return predict_passes(LINES, GS, start_time=START, hours=24.0,
                          min_alt=min_alt)


class TestThreePathsAgree:
    def test_pass_at_aos_matches_within_tolerance(self):
        p = _first_pass()[0]
        rep = check_pass_object(p, LINES, GS, tol_deg=0.5,
                                threshold_alt_deg=5.0)
        assert rep["checked"] is True
        assert rep["consistent"] is True
        assert rep["issues"] == []
        for d in rep["deltas"].values():
            assert d["daz_deg"] <= 0.5
            assert d["del_deg"] <= 0.5

    def test_arbitrary_time_two_independent_paths_agree(self):
        at = START.utc_datetime().timestamp() + 3600.0
        rep = check_time_consistency(LINES, GS, at, tol_deg=0.5)
        assert rep["checked"] is True
        assert rep["consistent"] is True
        assert set(rep["paths"]) == {"sky_view", "doppler"}

    def test_wrapper_predicts_and_checks(self):
        # 钉死时钟：不许 wrapper 默认取墙钟（当前时刻若正逢过境中段，
        # 存储的 rise 时刻与实算仰角会随日期随机不一致）
        rep = check_next_pass_consistency(
            LINES, GS, hours=24.0, min_alt=5.0,
            start_unix=START.utc_datetime().timestamp())
        assert rep["checked"] is True
        assert rep["consistent"] is True
        assert rep["satellite"]


NAME19 = "NOAA 19"
LINES19 = list(BUILTIN_TLE[NAME19][:2])


@pytest.fixture()
def same_tle_for_orbit(monkeypatch):
    """钉住 L23：orbit 与过境/天空可见工具使用同一份 TLE，只留时间基准差异。"""
    catnr = orbit.BUILTIN_SATS[NAME19]

    def fake_fetch_tle(n):
        if int(n) == int(catnr):
            return LINES19[0], LINES19[1]
        raise RuntimeError("test: 其他卫星不参与本次断言")

    monkeypatch.setattr(orbit, "fetch_tle", fake_fetch_tle)
    return catnr


class TestToolEntriesAgreeAtSameInstant:
    """同一时刻同一卫星：两个天空可见工具与过境预测结论必须一致（L23）。"""

    def test_sky_view_visible_sdr_sky_view_and_pass_agree(self, same_tle_for_orbit):
        from mbdsdr_ai.sdr_tools import _satellite_sky_view, _sky_view_visible

        passes = predict_passes(LINES19, GS, start_time=START, hours=24.0,
                                min_alt=10.0)
        assert passes
        p = passes[0]
        t_mid = p.max_alt_time.utc_datetime().timestamp()

        # 路径 1：过境预测——该时刻在过境窗口内，最大仰角达标
        assert p.max_alt >= 10.0
        assert p.rise_time.utc_datetime().timestamp() <= t_mid <= \
            p.set_time.utc_datetime().timestamp()

        # 路径 2：sky_view_visible（decoders/TimeEngine 口径）
        txt = _sky_view_visible({"observer_lat": GS.lat_deg,
                                 "observer_lon": GS.lon_deg,
                                 "min_elevation": 10.0,
                                 "timestamp": t_mid})
        assert NAME19 in txt

        # 路径 3：sdr_satellite_sky_view（orbit/sgp4 口径）
        txt2 = _satellite_sky_view({"latitude": GS.lat_deg,
                                    "longitude": GS.lon_deg,
                                    "altitude": GS.alt_m,
                                    "min_elevation": 10.0,
                                    "satellite_type": "weather",
                                    "timestamp": t_mid})
        assert NAME19 in txt2
        assert "时刻:" in txt2

        # 结论量化对齐：天空视图仰角贴着过境最大仰角
        vis = orbit.visible_satellites(GS.lat_deg, GS.lon_deg,
                                       GS.alt_m / 1000.0, 10.0, "weather",
                                       when=t_mid)
        el = next(v["elevation"] for v in vis if v["satellite"] == NAME19)
        assert abs(el - p.max_alt) < 1.0


class TestMismatchIsReported:
    def test_injected_bad_azimuth_is_flagged(self):
        p = _first_pass()[0]
        p.rise_az = (float(p.rise_az) + 5.0) % 360.0   # 注入 5° 偏差
        rep = check_pass_object(p, LINES, GS, tol_deg=0.5,
                                threshold_alt_deg=5.0)
        assert rep["checked"] is True
        assert rep["consistent"] is False
        assert rep["issues"]
        assert "pass" in rep["warning"]
        assert rep["deltas"]["pass_vs_sky_view"]["daz_deg"] >= 4.0

    def test_invalid_tle_is_not_silently_checked(self):
        rep = check_time_consistency(["bad line", "bad line"], GS, 1793000000.0)
        assert rep["checked"] is False
        assert rep["consistent"] is None
        assert rep["reason"]
