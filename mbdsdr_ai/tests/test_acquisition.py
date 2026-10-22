# SPDX-License-Identifier: MIT
"""射频前端/指向规划积木的行为测试。

覆盖：
- 物理公式与公开培训材料数值一致（天线增益/波束宽度、罐子馈源 TE11 截止与
  波导波长、探针位置建议值）
- 同步轨道指向解析几何（方位/仰角/斜距/时延）
- 链路预算判据（指向超差、SNR 不足、馈线过长 → 不通过 + 整改建议）
- 症状→排查步骤映射、未知卫星/症状的诚实失败
"""
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.acquisition import (RX_PROFILES, beamwidth_deg, dish_gain_db,
                                   guide_wavelength_m, te11_cutoff_hz,
                                   register_acquisition_tools)
from mbdsdr_ai.tool_registry import ToolRegistry

C = 299792458.0


@pytest.fixture()
def reg():
    r = ToolRegistry()
    register_acquisition_tools(r)
    return r


# ── 物理公式：与材料数值对照 ──────────────────────────────────
def test_dish_gain_matches_material():
    """1.8 m 抛物面 @1.69 GHz：材料给 25–30 dBi，公式算 27.8 dBi。"""
    g = dish_gain_db(1.8, 1690.0, 0.6)
    assert 25.0 <= g <= 30.0


def test_beamwidth_matches_material():
    """1.8 m @1.69 GHz 波束：材料给 5–8°。"""
    bw = beamwidth_deg(1.8, 1690.0)
    assert 5.0 <= bw <= 8.0


def test_cantenna_1690_geometry_matches_material():
    """1.69 GHz 罐子：内径 126 mm、罐长 230、探针入 44、离底 78.5（材料值）。"""
    fc = te11_cutoff_hz(0.126)
    assert fc / 1e6 == pytest.approx(1394.4, abs=1.0)
    assert fc < 1.69e9, "工作频率必须高于 TE11 截止"
    lg = guide_wavelength_m(1.69e9, fc)
    assert lg * 1000 == pytest.approx(314.0, abs=3.0)
    assert 0.75 * lg * 1000 == pytest.approx(235.0, abs=8.0)   # 罐长≈230
    assert 0.25 * (C / 1.69e9) * 1000 == pytest.approx(44.0, abs=2.0)  # 探针入罐 λ/4
    assert 0.25 * lg * 1000 == pytest.approx(78.5, abs=3.0)     # 离底 0.25λg


def test_cantenna_2200_geometry_matches_material():
    """2.2 GHz 罐子：内径 100 mm、罐长 165、探针入 33.7、离底 55（材料值）。"""
    fc = te11_cutoff_hz(0.100)
    assert fc / 1e6 == pytest.approx(1757.0, abs=1.0)
    assert fc < 2.2e9
    lg = guide_wavelength_m(2.2e9, fc)
    assert 0.75 * lg * 1000 == pytest.approx(170.0, abs=8.0)
    assert 0.25 * (C / 2.2e9) * 1000 == pytest.approx(34.0, abs=2.0)
    assert 0.25 * lg * 1000 == pytest.approx(56.6, abs=4.0)


def test_guide_wavelength_rejects_below_cutoff():
    """工作频率低于截止频率必须报错（不静默给错值）。"""
    fc = te11_cutoff_hz(0.126)
    with pytest.raises(ValueError):
        guide_wavelength_m(1.0e9, fc)


# ── 工具：星表查询 ───────────────────────────────────────────
def test_sat_rx_profile_astu1(reg):
    res = reg.call("sat_rx_profile", {"name": "ASTU-1"})
    assert res.success is True
    assert res.data["downlink_mhz"] == 435.400
    assert res.data["uplink_mhz"] == 145.000
    assert res.data["tune_margin_hz"] == 10_000.0
    assert "帧级协议" in res.data["protocol"]
    assert "未在教程给出" in res.data["notes"]


def test_sat_rx_profile_weather_sats(reg):
    for name, lon in (("GK-2A", 128.2), ("FY-4A", 104.7), ("FY-4B", 133.0)):
        res = reg.call("sat_rx_profile", {"name": name})
        assert res.success is True, name
        assert res.data["geo_longitude_deg"] == lon
        assert res.data["orbit"] == "GEO"


def test_sat_rx_profile_unknown_fails_honestly(reg):
    res = reg.call("sat_rx_profile", {"name": "NOT-A-SAT"})
    assert res.success is False
    assert "已收录" in res.content


def test_sat_rx_profile_ambiguous_lists_matches(reg):
    res = reg.call("sat_rx_profile", {"name": "FY-4"})
    assert res.success is True
    assert set(res.data["matches"]) == {"FY-4A", "FY-4B"}


# ── 工具：同步轨道指向 ───────────────────────────────────────
def test_geo_pointing_from_china_to_gk2a(reg):
    """长春观测 GK-2A（128.2°E）：方位在南半球、仰角显著。"""
    res = reg.call("geo_pointing", {
        "observer_lat_deg": 43.88, "observer_lon_deg": 125.32,
        "satellite": "GK-2A"})
    assert res.success is True
    az = res.data["azimuth_deg"]
    el = res.data["elevation_deg"]
    assert 150.0 < az < 220.0, az          # 南向
    assert el > 20.0, el                    # 同步轨道仰角可观
    assert 35000e3 < res.data["range_m"] < 42000e3
    # 单程时延约 120 ms 量级
    assert 100.0 < res.data["one_way_delay_ms"] < 160.0


def test_geo_pointing_rejects_leo_profile(reg):
    """ASTU-1 非同步轨道 → 诚实拒绝并指引改用 TLE 传播。"""
    res = reg.call("geo_pointing", {
        "observer_lat_deg": 43.88, "observer_lon_deg": 125.32,
        "satellite": "ASTU-1"})
    assert res.success is False
    assert "sat_pass_predict" in res.content


def test_geo_pointing_explicit_longitude(reg):
    res = reg.call("geo_pointing", {
        "observer_lat_deg": 0.0, "observer_lon_deg": 100.0,
        "sat_longitude_deg": 100.0})
    assert res.success is True
    assert res.data["elevation_deg"] == pytest.approx(90.0, abs=0.01)


# ── 工具：天线/指向/预算 ──────────────────────────────────────
def test_dish_gain_tool(reg):
    res = reg.call("dish_gain", {"diameter_m": 1.8, "frequency_mhz": 1690.0})
    assert res.success is True
    assert 25.0 <= res.data["gain_dbi"] <= 30.0
    assert 5.0 <= res.data["beamwidth_deg"] <= 8.0
    assert res.data["focal_length_m"] == pytest.approx(0.35 * 1.8)


def test_pointing_tolerance_flags_over(reg):
    res = reg.call("pointing_tolerance", {"beamwidth_deg": 6.9, "offset_deg": 3.0})
    assert res.success is True
    assert res.data["within_tolerance"] is False
    assert res.data["gain_loss_db"] > 0
    assert "超差" in res.content


def test_pointing_tolerance_margin(reg):
    res = reg.call("pointing_tolerance", {
        "beamwidth_deg": 6.9, "offset_deg": 0.5, "margin_db": 1.0})
    assert res.success is True
    assert res.data["max_offset_deg_for_margin"] == pytest.approx(
        6.9 * math.sqrt(1.0 / 12.0), rel=1e-6)


def test_link_budget_pass_and_fail(reg):
    good = reg.call("fe_link_budget", {
        "diameter_m": 1.8, "frequency_mhz": 1690.0, "offset_deg": 0.5,
        "lna_gain_db": 22.0, "lna_nf_db": 0.8, "feedline_length_m": 3.0,
        "measured_snr_db": 18.0})
    assert good.success is True
    assert good.data["verdict_ok"] is True
    assert good.data["system_noise_figure_db"] < 2.0

    bad = reg.call("fe_link_budget", {
        "diameter_m": 1.8, "frequency_mhz": 1690.0, "offset_deg": 4.0,
        "lna_gain_db": 10.0, "feedline_length_m": 12.0,
        "measured_snr_db": 6.0})
    assert bad.success is True
    assert bad.data["verdict_ok"] is False
    joined = " ".join(bad.data["findings"])
    assert "指向偏差" in joined
    assert "LNA 增益" in joined
    assert "馈线" in joined
    assert "不可解" in joined


def test_link_budget_snr_margin_uses_pointing_loss(reg):
    """实测 SNR 扣掉指向损失后才与要求比较。"""
    res = reg.call("fe_link_budget", {
        "diameter_m": 1.8, "frequency_mhz": 1690.0, "offset_deg": 2.0,
        "measured_snr_db": 6.0, "required_snr_db": 5.0})
    assert res.success is True
    assert res.data["verdict_ok"] is False
    assert "指向损失" in " ".join(res.data["findings"])


# ── 工具：诊断 / 馈源 / 遥测链 ───────────────────────────────
def test_fe_diagnose_known_symptom(reg):
    res = reg.call("fe_diagnose", {"symptom": "图像有条纹"})
    assert res.success is True
    assert res.data["symptom"] == "图像有条纹或缺损"
    assert len(res.data["steps"]) >= 3


def test_fe_diagnose_unknown_fails_honestly(reg):
    res = reg.call("fe_diagnose", {"symptom": "外星人入侵"})
    assert res.success is False
    assert "已知症状" in res.content


def test_cantenna_design_tool(reg):
    res = reg.call("cantenna_design", {
        "frequency_mhz": 1690.0, "tank_inner_diameter_mm": 126.0,
        "tank_length_mm": 230.0, "probe_length_mm": 44.0,
        "probe_offset_from_bottom_mm": 78.5})
    assert res.success is True
    assert res.data["cutoff_margin_mhz"] > 0
    assert res.data["findings"] == []   # 与材料一致 → 无告警


def test_cantenna_design_flags_deviation(reg):
    res = reg.call("cantenna_design", {
        "frequency_mhz": 1690.0, "tank_inner_diameter_mm": 126.0,
        "probe_length_mm": 60.0})
    assert res.success is True
    assert res.data["findings"], "探针偏差 >5mm 应告警"


def test_telemetry_rx_chain_exposes_params_and_missing(reg):
    res = reg.call("telemetry_rx_chain", {})
    assert res.success is True
    d = res.data
    assert d["downlink_mhz"] == 435.400
    assert d["prescribed_downlink_mhz"] == 435.390
    assert d["resampler"]["interpolation"] == 24
    assert d["resampler"]["decimation"] == 125
    assert d["resampler"]["out_rate_hz"] == 49152
    assert d["fll"]["samples_per_symbol"] == 5
    assert d["clock_sync"]["output_sps"] == 2
    assert d["doppler"]["length_rate"] == 112
    assert "CRC" in d["frame_protocol_missing"]


def test_telemetry_rx_chain_part(reg):
    res = reg.call("telemetry_rx_chain", {"part": "resampler"})
    assert res.success is True
    assert res.data["resampler"]["decimation"] == 125


def test_telemetry_rx_chain_bad_part_fails(reg):
    res = reg.call("telemetry_rx_chain", {"part": "nope"})
    assert res.success is False
    assert "可用" in res.content


def test_profiles_cover_materials_sats():
    """材料涉及的五类卫星必须都在剖面表内（能力完整性）。"""
    for name in ("ASTU-1", "GK-2A", "FY-4A", "FY-4B", "FY-3D", "LRO"):
        assert name in RX_PROFILES, name
    assert RX_PROFILES["LRO"].needs_tle is False      # 月球轨道不用 TLE
    assert RX_PROFILES["LRO"].needs_ephemeris_note
    assert RX_PROFILES["ASTU-1"].tune_margin_hz == 10_000.0
