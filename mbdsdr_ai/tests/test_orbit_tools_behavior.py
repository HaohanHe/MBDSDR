# SPDX-License-Identifier: MIT
"""
orbit_tools.py（轨道原子工具适配层）行为测试
==============================================

零硬件、零网络：sgp4 在解释器内（缺失则诚实 skip）；TLE 用内置合成/公开
标准 TLE 常量。oracle 全部来自 orbit_dynamics 真实语义（读代码定）：

  - 全链口径是 **km / km/s**（MU_EARTH 用 km^3/s^2；tle_state_ecef 保留
    SGP4 原始 km；calibrate_time_offset 边界处 ×1000 才转 m/s）。
    故 state_ecef 半径应是 ~6.7e3 量级（km），伪距率是 ~1e0 量级（km/s）。
  - 多普勒换算是精确互逆：doppler_hz == f - fc。
  - 错误诚实：缺参/坏 TLE 必须 success=False + 中文原因，不得返回残缺数据。

运行：python -m pytest mbdsdr_ai/tests/test_orbit_tools_behavior.py -v
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.orbit_tools import (  # noqa: E402
    _calibrate_time_offset,
    _coord_ecef_to_eci,
    _coord_ecef_to_enu,
    _coord_ecef_to_geodetic,
    _coord_eci_to_ecef,
    _coord_enu_to_az_el,
    _coord_geodetic_to_ecef,
    _doppler_from_frequency,
    _doppler_to_frequency,
    _propagate_state_stm,
    _pseudorange_rate_model,
    _rls_update,
    _tle_state,
    register_orbit_brick_tools,
)

sgp4 = pytest.importorskip("sgp4", reason="TLE→状态需要 sgp4（诚实 skip，不 mock）")

# 内置标准 TLE（公开 ISS 例，历元 2008-09-20 附近）
TLE1 = "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"
TLE2 = ("2 25544  51.6416 247.4627 0006703 130.5360 325.0288 "
        "15.72125391563537")
T0 = 1221916800.0  # 2008-09-20 12:00:00 UTC，TLE 历元附近

C_LIGHT_KMS = 299792.458  # orbit_dynamics 内部光速口径


# ── 1. 多普勒换算：数值 oracle + 精确互逆 ─────────────────────
def test_doppler_from_frequency_value_and_units():
    fc = 150e6
    f = fc + 1000.0
    r = _doppler_from_frequency(
        {"frequency_hz": f, "center_frequency_hz": fc})
    assert r.success, r.content
    # oracle：range_rate = -c*(f-fc)/fc，内部口径 km/s
    assert r.data["doppler_hz"] == pytest.approx(1000.0, rel=1e-12)
    assert r.data["range_rate_mps"] == pytest.approx(
        -C_LIGHT_KMS * 1000.0 / fc, rel=1e-12)
    # 单位口径必须是 km/s（1 kHz@150MHz 物理量 ~2 km/s，不是 ~2 m/s）
    assert abs(r.data["range_rate_mps"]) == pytest.approx(1.9986, abs=1e-3)
    assert "km/s" in r.content, f"文案单位必须如实标注 km/s: {r.content}"


def test_doppler_round_trip_is_exact():
    fc, f = 1626270833.0, 1626303262.0
    d1 = _doppler_from_frequency(
        {"frequency_hz": f, "center_frequency_hz": fc})
    assert d1.success
    d2 = _doppler_to_frequency(
        {"range_rate_mps": d1.data["range_rate_mps"],
         "center_frequency_hz": fc})
    assert d2.success
    assert d2.data["frequency_hz"] == pytest.approx(f, rel=1e-12)
    assert d2.data["doppler_hz"] == pytest.approx(d1.data["doppler_hz"],
                                                 rel=1e-12)


# ── 2. 错误诚实：缺参/坏参必须显式失败 ────────────────────────
def test_missing_key_is_honest_failure():
    r = _doppler_from_frequency({"frequency_hz": 1.0})
    assert r.success is False
    assert "缺少必需参数" in r.content, r.content
    assert "center_frequency_hz" in r.content
    assert r.data.get("error"), "失败结果必须带 error"


def test_non_numeric_param_is_honest_failure():
    r = _doppler_from_frequency(
        {"frequency_hz": "abc", "center_frequency_hz": 1.0})
    assert r.success is False
    assert r.content.strip(), "失败 content 不得为空"


def test_bad_tle_is_honest_failure_no_fake_state():
    r = _tle_state({"tle_line1": "bad", "tle_line2": "bad",
                    "timestamp": T0})
    assert r.success is False
    assert "TLE" in r.content or "字段" in r.content
    assert not (r.data or {}).get("state_ecef"), "失败不得返回残缺状态"


# ── 3. TLE→状态：真实 sgp4，口径必须是 km ─────────────────────
def test_tle_state_returns_km_units():
    r = _tle_state({"tle_line1": TLE1, "tle_line2": TLE2,
                    "timestamp": T0})
    assert r.success, r.content[:200]
    st = r.data["state_ecef"]
    assert len(st) == 7
    radius = math.sqrt(st[0] ** 2 + st[1] ** 2 + st[2] ** 2)
    # km 口径：LEO 半径 6.7e3 km 量级。若是 m 会到 6.7e6，断言必炸。
    assert 6.0e3 < radius < 8.0e3, f"半径 {radius} 不是 km 口径"
    speed = math.sqrt(st[3] ** 2 + st[4] ** 2 + st[5] ** 2)
    assert 6.0 < speed < 8.5, f"速度 {speed} 不是 km/s 口径"
    assert st[6] == 0.0, "先验无钟漂，第 7 维必须为 0"


# ── 4. 坐标转换往返 + 已知几何 ─────────────────────────────────
def test_coord_round_trip_and_known_point():
    r = _coord_geodetic_to_ecef({"lat_deg": 0.0, "lon_deg": 0.0, "alt_m": 0.0})
    assert r.success
    ecef = r.data["ecef"]
    assert ecef[0] == pytest.approx(6378135.0, abs=1e-6)  # WGS-72 赤道半径
    assert ecef[1] == pytest.approx(0.0, abs=1e-9)

    back = _coord_ecef_to_geodetic({"ecef": [6378135.0, 0.0, 0.0]})
    assert back.success
    assert back.data["lat_deg"] == pytest.approx(0.0, abs=1e-6)
    assert back.data["lon_deg"] == pytest.approx(0.0, abs=1e-6)
    assert back.data["alt_m"] == pytest.approx(0.0, abs=1e-3)


def test_enu_to_az_el_known_geometry():
    north = _coord_enu_to_az_el({"enu": [0.0, 1000.0, 0.0]})
    assert north.success
    assert north.data["azimuth_deg"] == pytest.approx(0.0, abs=1e-9)
    assert north.data["elevation_deg"] == pytest.approx(0.0, abs=1e-9)

    east = _coord_enu_to_az_el({"enu": [1000.0, 0.0, 0.0]})
    assert east.data["azimuth_deg"] == pytest.approx(90.0, abs=1e-9)

    up = _coord_enu_to_az_el({"enu": [0.0, 0.0, 1000.0]})
    assert up.data["elevation_deg"] == pytest.approx(90.0, abs=1e-9)


# ── 5. ECEF<->ECI 往返（含自转牵连速度）────────────────────────
def test_ecef_eci_round_trip():
    pos = [-21172.64, -22651.08, -28725.21]
    vel = [1.00532, 0.58665, -1.20348]
    fwd = _coord_ecef_to_eci({"pos_ecef": pos, "vel_ecef": vel,
                              "timestamp": T0})
    assert fwd.success
    back = _coord_eci_to_ecef({"pos_eci": fwd.data["pos_eci"],
                               "vel_eci": fwd.data["vel_eci"],
                               "timestamp": T0})
    assert back.success
    for a, b in zip(back.data["pos_ecef"], pos):
        assert a == pytest.approx(b, abs=1e-6)
    for a, b in zip(back.data["vel_ecef"], vel):
        assert a == pytest.approx(b, abs=1e-6)


# ── 6. 传播：零时长 = 恒等 + 单位 STM ──────────────────────────
def test_propagate_zero_dt_is_identity():
    st = [5268.5, 945.2, -4095.2, 1.875, 6.041, 3.810, 0.0]
    r = _propagate_state_stm({"state_ecef": st, "t_from": T0, "t_to": T0})
    assert r.success, r.content
    for a, b in zip(r.data["state_ecef"], st):
        assert a == pytest.approx(b, rel=1e-12, abs=1e-9)
    stm = np.asarray(r.data["stm"])
    assert stm.shape == (7, 7)
    assert stm == pytest.approx(np.eye(7), abs=1e-12)


# ── 7. 观测模型：重合位置必须显式失败，不静默返回垃圾 ──────────
def test_pseudorange_model_coincident_is_honest_failure():
    st = [7000.0, 0.0, 0.0, 0.0, 7.5, 0.0, 0.0]
    r = _pseudorange_rate_model({"sat_state_ecef": st,
                                 "receiver_ecef": [7000.0, 0.0, 0.0]})
    assert r.success is False
    assert "距离" in r.content, r.content
    assert not (r.data or {}).get("jacobian")


def test_pseudorange_model_value_and_jacobian_shape():
    st = [7000.0, 0.0, 0.0, 0.0, 7.5, 0.0, 0.001]
    rec = [6378.135, 0.0, 0.0]  # 赤道地面点（km 口径）
    r = _pseudorange_rate_model({"sat_state_ecef": st, "receiver_ecef": rec})
    assert r.success, r.content
    # oracle：los=(1,0,0)，rate = los·v + b = 0.001 km/s
    assert r.data["range_rate_mps"] == pytest.approx(0.001, rel=1e-9)
    assert len(r.data["jacobian"]) == 7
    assert "km/s" in r.content


# ── 8. RLS / 校时错误诚实（坏输入不得假成功）───────────────────
def test_rls_update_missing_key_is_honest_failure():
    r = _rls_update({"ref_state": [1.0] * 7})
    assert r.success is False
    assert "缺少必需参数" in r.content


def test_calibrate_time_offset_bad_tle_is_honest_failure():
    r = _calibrate_time_offset({
        "observations": [[T0, 150e6], [T0 + 20, 150e6 + 900.0]],
        "receiver_ecef": [6378.135, 0.0, 0.0],
        "center_frequency_hz": 150e6,
        "tle_line1": "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927",
        "tle_line2": "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15",  # 缺平均运动
    })
    assert r.success is False
    assert r.content.strip()
    assert not (r.data or {}).get("time_offset_s")


# ── 9. 注册面：原子积木一个不少、名字唯一 ──────────────────────
def test_register_bricks_surface():
    class _Rec:
        def __init__(self):
            self.tools = {}

        def register(self, name, description, parameters, handler, category="general"):
            self.tools[name] = {"description": description,
                                "parameters": parameters,
                                "handler": handler, "category": category}

    rec = _Rec()
    register_orbit_brick_tools(rec)
    expected = {
        "doppler_from_frequency", "doppler_to_frequency", "tle_state_ecef",
        "propagate_state_stm", "pseudorange_rate_model", "rls_update",
        "calibrate_time_offset",
        "coord_geodetic_to_ecef", "coord_ecef_to_geodetic",
        "coord_ecef_to_enu", "coord_enu_to_az_el",
        "coord_ecef_to_eci", "coord_eci_to_ecef",
    }
    assert set(rec.tools.keys()) == expected
    for name, t in rec.tools.items():
        assert callable(t["handler"]), name
        assert t["parameters"].get("required") is not None, name
        assert t["description"], name
