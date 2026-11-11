# SPDX-License-Identifier: MIT
"""时间基准不可用：轨道/过境必须显式失败，不得退回墙钟。

红线：位置与过境都是时间的函数。TimeEngine 取不到时基时若退回
``time.time()``，算出来的数字看起来完全正常，却对应另一个时刻；
尤其时间穿梭/快转之后，「下一颗什么时候过境」会得到一个假答案。
"""
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mbdsdr_ai.new_spacetime as new_spacetime  # noqa: E402
import mbdsdr_ai.orbit as orbit  # noqa: E402


def _break_engine(monkeypatch):
    def _boom():
        raise RuntimeError("engine offline")

    monkeypatch.setattr(new_spacetime, "get_time_engine", _boom)


def test_engine_helper_raises_instead_of_wall_clock(monkeypatch):
    _break_engine(monkeypatch)
    with pytest.raises(orbit.TimeBasisUnavailable) as ei:
        orbit._engine_now_unix()
    assert "不退回系统时钟" in str(ei.value)


def test_compute_state_without_when_raises(monkeypatch):
    _break_engine(monkeypatch)
    with pytest.raises(orbit.TimeBasisUnavailable):
        orbit.compute_satellite_state("NOAA 19", 45.0, 120.0)


def test_predict_passes_without_start_raises(monkeypatch):
    _break_engine(monkeypatch)
    with pytest.raises(orbit.TimeBasisUnavailable):
        orbit.predict_passes(45.0, 120.0, hours=1.0, min_elevation=10.0)


def test_predict_report_marks_unavailable_not_zero_passes(monkeypatch):
    _break_engine(monkeypatch)
    rep = orbit.predict_passes_report(45.0, 120.0, hours=1.0, min_elevation=10.0)
    assert rep["n_passes"] == 0
    assert rep["passes"] == []
    reasons = [d["reason"] for d in rep["degraded"]]
    assert "time_basis_unavailable" in reasons


def test_explicit_when_never_touches_engine(monkeypatch):
    """显式时刻走旁路：引擎坏了也不该被调用。"""
    def _must_not_call():
        raise AssertionError("显式 when 时不应取 TimeEngine")

    monkeypatch.setattr(orbit, "_engine_now_unix", _must_not_call)
    st = orbit.compute_satellite_state("NOAA 19", 45.0, 120.0, when=1.76e9)
    assert st is None or st["satellite"] == "NOAA 19"


@pytest.mark.usefixtures("isolated_profile")
def test_agent_answers_unavailable_without_concrete_times(monkeypatch):
    _break_engine(monkeypatch)
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.tool_registry import ToolRegistry

    agent = MBDSDRAgent.__new__(MBDSDRAgent)
    agent.tool_registry = ToolRegistry()
    agent._register_orbit_tools()
    agent._register_pointing_tools()
    agent._register_doppler_tools()

    station = {"observer_lat": 45.0, "observer_lon": 120.0}
    passes = agent.tool_registry.call(
        "predict_satellite_passes", dict(station, hours=6))
    assert passes.success is False
    assert "时间基准不可用，无法计算过境" in passes.content
    assert not re.search(r"\d{1,2}:\d{2}", passes.content), \
        f"不可用时仍给出了时刻: {passes.content}"

    pointing = agent.tool_registry.call(
        "satellite_now_pointing", dict(station, satellite="NOAA 19"))
    assert pointing.success is False
    assert "时间基准不可用" in pointing.content
    assert not re.search(r"方位\s*\d|仰角\s*-?\d", pointing.content), \
        f"不可用时仍给出了指向数字: {pointing.content}"

    doppler = agent.tool_registry.call(
        "doppler_tune_frequency", dict(station, satellite="NOAA 19"))
    assert doppler.success is False
    assert "时间基准不可用" in doppler.content
    assert not re.search(r"\d+\.\d+\s*MHz", doppler.content), \
        f"不可用时仍给出了频率: {doppler.content}"


def test_sdr_physics_now_raises(monkeypatch):
    from mbdsdr_ai import sdr_tools

    _break_engine(monkeypatch)
    with pytest.raises(orbit.TimeBasisUnavailable) as ei:
        sdr_tools._physics_now({})
    assert "时间基准不可用" in str(ei.value)


def test_sdr_satellite_handlers_do_not_fabricate(monkeypatch):
    """三条入口统一向上抛时基故障；不允许返回看似正常的数字。"""
    from mbdsdr_ai import sdr_tools

    _break_engine(monkeypatch)
    station = {"latitude": 45.0, "longitude": 120.0}
    cases = [
        (sdr_tools._satellite_sky_view, dict(station)),
        (sdr_tools._satellite_passes, dict(station, hours=6.0)),
        (sdr_tools._satellite_doppler,
         dict(station, satellite_name="NOAA 19", frequency_hz=137.62e6)),
    ]
    for fn, args in cases:
        with pytest.raises(orbit.TimeBasisUnavailable) as ei:
            fn(args)
        msg = str(ei.value)
        assert "时间基准不可用" in msg
        assert not re.search(r"\d{1,2}:\d{2}|MHz|\d+(\.\d+)?°", msg), \
            f"{fn.__name__} 不可用时仍给出了数字: {msg}"


def test_recording_refuses_without_time_basis(monkeypatch):
    """录制是控制动作：时基不可用必须显式拒绝，不能按墙钟判「已升起」。"""
    from mbdsdr_ai import sdr_tools

    _break_engine(monkeypatch)

    class _Backend:
        class status:  # noqa: N801 - 只模拟需要的字段
            connected = True

        def get_doppler_compensation_status(self):
            return {"enabled": False}

        def set_doppler_compensation(self, _v):
            return True

        def set_frequency(self, _hz):
            return True

    monkeypatch.setattr(sdr_tools, "_get_backend", lambda _mgr: _Backend())
    out = sdr_tools._satellite_doppler_track(None, {
        "satellite_name": "NOAA 19", "frequency_mhz": 137.62,
        "latitude": 45.0, "longitude": 120.0, "duration_s": 1.0,
    })
    assert out.success is False
    assert "时间基准不可用" in out.content
    assert "拒绝录制" in out.content
    assert not re.search(r"\d{1,2}:\d{2}|MHz", out.content)
