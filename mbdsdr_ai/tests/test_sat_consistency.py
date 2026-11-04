# SPDX-License-Identifier: MIT
"""P11 三路径 az/el 一致性守卫（过境 / 天空视图 / 多普勒）。

运行：python -m pytest mbdsdr_ai/tests/test_sat_consistency.py -v
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

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
        rep = check_next_pass_consistency(LINES, GS, hours=24.0, min_alt=5.0)
        assert rep["checked"] is True
        assert rep["consistent"] is True
        assert rep["satellite"]


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
