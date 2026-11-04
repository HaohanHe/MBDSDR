# SPDX-License-Identifier: MIT
"""P11 时间基准：UTC/本地时区、TLE 历元折叠、闰秒、时钟跳变。

运行：python -m pytest mbdsdr_ai/tests/test_time_basis.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.time_basis import (  # noqa: E402
    ClockGuard,
    format_local,
    gps_seconds_to_utc,
    leap_seconds_at,
    local_to_utc,
    tle_epoch_report,
    tle_epoch_utc,
    utc_to_gps_seconds,
    validate_pass_window,
)

UTC = timezone.utc
BASE_L1 = "1 25544U 98067A   26250.50000000  .00000050  00000-0  10000-3 0  9996"


def _line1_with_epoch(epoch: str) -> str:
    """把 TLE line1 的固定历元列 [18:32] 替换为给定 YYDDD.DDDDDDDD。"""
    assert len(epoch) == 14
    return BASE_L1[:18] + epoch + BASE_L1[32:]


class TestLocalTimeDisplay:
    def test_fixed_offset_roundtrip_is_exact(self):
        tz = timezone(timedelta(hours=8))
        utc = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
        shown = format_local(utc, tz)
        assert shown == "2026-11-04T20:00:00+08:00"
        back = local_to_utc(datetime(2026, 11, 4, 20, 0, 0, tzinfo=tz))
        assert back == utc
        assert format_local(utc, timezone(timedelta(hours=-5))) == \
            "2026-11-04T07:00:00-05:00"

    def test_naive_local_is_interpreted_in_given_tz_not_utc(self):
        tz = timezone(timedelta(hours=8))
        naive = datetime(2026, 11, 4, 20, 0, 0)
        assert local_to_utc(naive, tz) == datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


class TestTleEpoch:
    def test_two_digit_year_pivot_57(self):
        # YY=57 → 1957（≥57 归 1900s）
        e57 = tle_epoch_utc(_line1_with_epoch("57001.50000000"))
        assert e57 == datetime(1957, 1, 1, 12, 0, 0, tzinfo=UTC)
        # YY=56 → 2056（<57 归 2000s）
        e56 = tle_epoch_utc(_line1_with_epoch("56001.50000000"))
        assert e56 == datetime(2056, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_normal_epoch_and_year_rule_report(self):
        line1 = _line1_with_epoch("26250.50000000")
        ep = tle_epoch_utc(line1)
        assert ep == datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
        rep = tle_epoch_report(line1, now=datetime(2026, 9, 8, tzinfo=UTC))
        assert rep["ok"] is True
        assert rep["year"] == 2026
        assert rep["two_digit_year"] == 26
        assert "20YY" in rep["year_rule"]
        assert rep["plausible"] is True

    def test_invalid_day_of_year_is_none_never_rolls_into_next_year(self):
        # 2026 非闰年：DDD=366/367 都是非法历元（旧实现会顺延到 2027-01-01）
        assert tle_epoch_utc(_line1_with_epoch("26366.00000000")) is None
        assert tle_epoch_utc(_line1_with_epoch("26367.00000000")) is None
        # 2024 是闰年：DDD=366 合法
        assert tle_epoch_utc(_line1_with_epoch("24366.50000000")) == \
            datetime(2024, 12, 31, 12, 0, 0, tzinfo=UTC)

    def test_far_future_epoch_is_flagged_not_trusted(self):
        line1 = _line1_with_epoch("27001.00000000")   # 2027-01-01
        rep = tle_epoch_report(line1, now=datetime(2026, 11, 4, tzinfo=UTC))
        assert rep["ok"] is True
        assert rep["epoch_in_future"] is True
        assert rep["plausible"] is False
        assert "将来" in rep["reason"]


class TestLeapSeconds:
    def test_known_boundaries(self):
        assert leap_seconds_at(datetime(1980, 1, 6, tzinfo=UTC)) == 0
        assert leap_seconds_at(datetime(2016, 12, 31, 23, 59, 59, tzinfo=UTC)) == 17
        assert leap_seconds_at(datetime(2017, 1, 1, tzinfo=UTC)) == 18
        assert leap_seconds_at(datetime(2026, 11, 4, tzinfo=UTC)) == 18

    def test_gps_seconds_roundtrip_across_leap_table(self):
        for dt in (
            datetime(2016, 12, 31, 23, 59, 59, tzinfo=UTC),
            datetime(2017, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC),
        ):
            sec = utc_to_gps_seconds(dt)
            back = gps_seconds_to_utc(sec)
            assert abs((back - dt).total_seconds()) <= 1.0


class TestClockGuard:
    def test_backward_wall_jump_is_detected(self):
        g = ClockGuard()
        g.observe(wall=1000.0, mono=1000.0)
        ev = g.observe(wall=970.0, mono=1010.0)   # 墙钟回跳 30s，单调钟前进 10s
        assert ev["type"] == "backward_jump"
        assert ev["jump_s"] == -30.0
        st = g.status()
        assert st["clock_jump"] is True
        assert "回跳" in st["warning"]

    def test_forward_ntp_correction_is_detected(self):
        g = ClockGuard()
        g.observe(wall=1000.0, mono=1000.0)
        ev = g.observe(wall=1012.0, mono=1001.0)  # 墙钟前跳 12s
        assert ev["type"] == "forward_jump"
        assert ev["jump_s"] == 11.0

    def test_normal_passage_and_explicit_reset(self):
        g = ClockGuard()
        g.observe(wall=1000.0, mono=1000.0)
        assert g.observe(wall=1001.0, mono=1001.0)["type"] == "ok"
        assert g.status()["clock_jump"] is False
        g.reset()
        assert g.status()["clock_jump"] is False


class TestPassWindowGuard:
    def test_negative_duration_is_reported(self):
        rep = validate_pass_window(rise_unix=1000.0, set_unix=900.0)
        assert rep["ok"] is False
        assert rep["duration_s"] == -100.0
        assert any("set_before_rise" in s for s in rep["issues"])

    def test_normal_duration_ok(self):
        rep = validate_pass_window(rise_unix=1000.0, set_unix=1300.0)
        assert rep["ok"] is True
        assert rep["duration_s"] == 300.0
