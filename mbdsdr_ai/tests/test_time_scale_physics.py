# SPDX-License-Identifier: MIT
"""P11 时间倍率纪律：×60/×300/×1800 只推进显示，物理计算按显式 UTC 口径。

运行：python -m pytest mbdsdr_ai/tests/test_time_scale_physics.py -v
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.new_spacetime import TimeEngine  # noqa: E402


class TestEngineAdvancement:
    @pytest.mark.parametrize("rate", [60.0, 300.0, 1800.0])
    def test_rate_scales_display_advance_only(self, rate):
        eng = TimeEngine()
        eng.set_utc(datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc))
        t0 = eng.now_unix()
        eng.set_rate(rate)
        eng.tick(1.0)   # 1 真实秒
        assert abs((eng.now_unix() - t0) - rate) < 1e-3

    def test_pause_does_not_advance(self):
        eng = TimeEngine()
        eng.set_utc(datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc))
        t0 = eng.now_unix()
        eng.set_rate(0.0)
        eng.tick(10.0)
        assert eng.now_unix() == pytest.approx(t0)

    def test_backward_wall_clock_advances_by_monotonic_and_warns(self, monkeypatch):
        eng = TimeEngine()
        eng.set_utc(datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(60.0)
        t0 = eng.now_unix()
        # 墙钟回跳 60s，但单调钟前进 10s：只按 10s 推进，且要告警
        eng._last_mono = 1000.0
        monkeypatch.setattr(time, "time", lambda: 900.0)
        monkeypatch.setattr(time, "monotonic", lambda: 1010.0)
        eng.tick()
        assert abs((eng.now_unix() - t0) - 10.0 * 60.0) < 1e-3
        st = eng.clock_status()
        assert st["clock_jump"] is True
        assert "回跳" in st["warning"]
        assert st["last_event"]["jump_s"] < 0

    def test_auto_tick_never_negative_after_clock_rollback(self, monkeypatch):
        eng = TimeEngine()
        eng.set_utc(datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(1800.0)
        t0 = eng.now_unix()
        eng._last_mono = 5000.0
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        monkeypatch.setattr(time, "monotonic", lambda: 4990.0)  # 单调钟也回退（异常）
        eng.tick()
        assert eng.now_unix() >= t0   # 不允许负时长/倒退推进


class TestPhysicsIsRateIndependent:
    def test_pass_prediction_identical_across_rates(self):
        """同一显式 start_time，×1/×60/×1800 结果逐字段一致。"""
        from mbdsdr_ai.decoders import BUILTIN_TLE
        from mbdsdr_ai.sat_passes import (GroundStation, make_timescale,
                                          predict_passes)

        line1, line2 = BUILTIN_TLE["NOAA 15"][:2]
        start = make_timescale().utc(2026, 9, 26, 0, 0, 0)
        gs = GroundStation(lat_deg=45.0, lon_deg=120.0, alt_m=0.0)

        results = []
        for rate in (1.0, 60.0, 300.0, 1800.0):
            eng = TimeEngine()
            eng.set_rate(rate)
            results.append(predict_passes([line1, line2], gs, start_time=start,
                                           hours=6.0, min_alt=5.0))
        base = results[0]
        assert base, "测试卫星应在 6h 内有过境（固定 TLE/时刻）"
        for other in results[1:]:
            assert len(other) == len(base)
            for a, b in zip(base, other):
                assert a.rise_time.utc_datetime() == b.rise_time.utc_datetime()
                assert a.duration == pytest.approx(b.duration)
                assert a.rise_az == pytest.approx(b.rise_az)
                assert a.max_alt == pytest.approx(b.max_alt)

    def test_sky_position_same_explicit_timestamp(self):
        """天空视图位置：同一 Unix 时刻，倍率改变不改变 az/el。"""
        from mbdsdr_ai.decoders import compute_satellite_position

        when = 1793000000.0   # 固定 UTC 时刻
        outs = []
        for rate in (1.0, 1800.0):
            eng = TimeEngine()
            eng.set_rate(rate)
            outs.append(compute_satellite_position("NOAA 15", 45.0, 120.0, 0.0, when))
        assert outs[0].elevation == pytest.approx(outs[1].elevation)
        assert outs[0].azimuth == pytest.approx(outs[1].azimuth)


class TestLegacyPassStep:
    def test_predict_satellite_pass_uses_fixed_real_step(self, monkeypatch):
        """new_spacetime 的过境预测按 30s 真实步进，不随倍率放大。"""
        import mbdsdr_ai.new_spacetime as ns
        import mbdsdr_ai.decoders as dec

        calls = []
        real_pos = dec.compute_satellite_position

        def spy(name, lat, lon, alt, timestamp):
            calls.append(timestamp)
            return real_pos(name, lat, lon, alt, timestamp)

        # 函数内部是 from .decoders import ...，必须打在源模块上
        monkeypatch.setattr(dec, "compute_satellite_position", spy)
        eng = ns.get_time_engine()   # 函数读全局引擎，倍率只能影响起点时刻
        eng.set_utc(datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(1800.0)
        try:
            p = ns.predict_satellite_pass("NOAA 15", 45.0, 120.0, 0.0,
                                          hours_ahead=0.2, min_elevation=5.0)
        finally:
            eng.set_time_now()
            eng.set_rate(1.0)
        # 0.2h = 720s → 24 步，间隔必须为 30s（倍率不参与）
        assert len(calls) == 24
        assert all(b - a == pytest.approx(30.0) for a, b in zip(calls, calls[1:]))
        if p is not None and p.rise_time and p.set_time:
            assert p.duration_sec > 0
