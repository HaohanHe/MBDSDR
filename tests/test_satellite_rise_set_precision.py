# SPDX-License-Identifier: MIT
"""卫星信息卡「下次升落」：穿越点二分精度 + 起点已在天上的事件顺序。

旧 `next_rise_set` 把 30s 采样点直接当 AOS/LOS（解析曲线下误差 20s），
且扫描起点已在地平线上时，返回的 set 是「本次落」、rise 是「再下一次升」，
两者顺序颠倒却没有任何标记，上层无从如实说明。

用 monkeypatch `orbit._state_from_satrec` 注入解析正弦仰角曲线，解析解
精确可知（rise=100s / set=1900s），不依赖真实 TLE 或网络。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import orbit  # noqa: E402
from mbdsdr_ai.skyengine.satellites import next_rise_set  # noqa: E402

JD0 = 2451545.0          # 任意儒略日起点，测试只看相对秒
PERIOD_S = 3600.0


def _inject(monkeypatch, curve):
    """把传播器换成解析仰角曲线（度），返回调用过的相对秒列表。"""
    calls = []
    state = {"jd0": None}

    def _fake(_satrec, _mode, jd, _lat, _lon, _alt_km):
        if state["jd0"] is None:
            state["jd0"] = float(jd)
        sec = (float(jd) - state["jd0"]) * 86400.0
        calls.append(sec)
        return {"elevation": curve(sec)}

    monkeypatch.setattr(orbit, "_state_from_satrec", _fake)
    return calls


def _sin_curve(shift_s):
    """峰值 40°、过零点在 shift_s 与 shift_s+1800s 的正弦仰角曲线。"""
    import math
    return lambda sec: 40.0 * math.sin(2.0 * math.pi * (sec - shift_s) / PERIOD_S)


def _sec(jd):
    return (jd - JD0) * 86400.0


# ------------------------------------------------------------- 穿越精度
class TestCrossingPrecision:
    def test_rise_and_set_are_bisected_not_snapped_to_30s(self, monkeypatch):
        calls = _inject(monkeypatch, _sin_curve(100.0))
        rs = next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                           step_s=30.0, horizon_hold_s=4000.0)
        assert rs["rise_jd"] is not None and rs["set_jd"] is not None
        rise_s, set_s = _sec(rs["rise_jd"]), _sec(rs["set_jd"])
        # 旧式会报 120s / 1920s（误差 20s）；二分后必须收到 1.5s 内
        assert abs(rise_s - 100.0) < 1.5, f"rise={rise_s:.2f}s"
        assert abs(set_s - 1900.0) < 1.5, f"set={set_s:.2f}s"
        assert rs["event_order"] == ["rise", "set"]
        assert rs["already_above"] is False
        assert rs["max_alt"] >= 39.9
        assert (any(abs(s - 90.0) < 0.01 for s in calls)
                and any(abs(s - 120.0) < 0.01 for s in calls)), \
            "粗扫仍按 30s 网格（JD 浮点有 ~µs 误差）"

    def test_bisection_cost_is_small(self, monkeypatch):
        """二分每个穿越只多十几次传播，不能把整段扫描变成 2s 细扫。"""
        calls = _inject(monkeypatch, _sin_curve(100.0))
        next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                      step_s=30.0, horizon_hold_s=4000.0)
        assert len(calls) < 200, f"传播次数异常：{len(calls)}"

    def test_no_pass_returns_none_not_fabricated_time(self, monkeypatch):
        _inject(monkeypatch, lambda _s: -10.0)
        rs = next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                           step_s=30.0, horizon_hold_s=600.0)
        assert rs["rise_jd"] is None and rs["set_jd"] is None
        assert rs["max_alt"] == -10.0
        assert rs["event_order"] == []


# ------------------------------------------------- 起点已在地平线上
class TestAlreadyAbove:
    def test_event_order_is_set_then_rise(self, monkeypatch):
        # shift=-800 ⇒ sec=0 时仰角约 +39°，第一次落下在 1000s，
        # 再升起在 2800s。
        _inject(monkeypatch, _sin_curve(-800.0))
        rs = next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                           step_s=30.0, horizon_hold_s=4000.0)
        assert rs["already_above"] is True
        assert rs["event_order"] == ["set", "rise"], \
            "当前可见时下一个事件是落下，不是升起"
        assert rs["set_jd"] < rs["rise_jd"]
        assert abs(_sec(rs["set_jd"]) - 1000.0) < 1.5
        assert abs(_sec(rs["rise_jd"]) - 2800.0) < 1.5

    def test_horizon_altitude_gate_is_respected(self, monkeypatch):
        """门限 10° 时 rise/set 应向内收，不能仍按 0° 报。"""
        _inject(monkeypatch, _sin_curve(100.0))
        rs = next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                           step_s=30.0, horizon_alt=10.0,
                           horizon_hold_s=4000.0)
        import math
        # 40*sin(θ)=10 ⇒ θ=asin(0.25)，rise = 100 + θ/(2π)*3600
        expect_rise = 100.0 + math.asin(0.25) / (2 * math.pi) * PERIOD_S
        assert _sec(rs["rise_jd"]) > 100.0
        assert abs(_sec(rs["rise_jd"]) - expect_rise) < 2.0


def test_event_time_error_within_declared_tolerance(monkeypatch):
    """AOS/LOS 误差必须落在声明的二分容差内，而不是只落在「比粗扫步长小」。

    上面几条用 1.5s / 2.0s 的松界（那是 30s 粗扫吸附时代留下的余量）；
    真正的上界是 ``RISE_SET_BISECT_TOL_S``。把它改成常量后，这里直接断言
    误差 ≤ 该值：一旦有人退回到「把采样点当时刻」，20s 级误差会立刻变红。
    """
    from mbdsdr_ai.skyengine.satellites import RISE_SET_BISECT_TOL_S

    _inject(monkeypatch, _sin_curve(100.0))
    rs = next_rise_set(object(), 43.9, 125.3, 0.05, JD0,
                       step_s=30.0, horizon_hold_s=4000.0)
    rise_err = abs(_sec(rs["rise_jd"]) - 100.0)
    set_err = abs(_sec(rs["set_jd"]) - 1900.0)
    assert rise_err <= RISE_SET_BISECT_TOL_S, (
        f"rise 误差 {rise_err:.3f}s > 声明容差 {RISE_SET_BISECT_TOL_S}s")
    assert set_err <= RISE_SET_BISECT_TOL_S, (
        f"set 误差 {set_err:.3f}s > 声明容差 {RISE_SET_BISECT_TOL_S}s")
