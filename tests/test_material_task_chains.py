# -*- coding: utf-8 -*-
"""材料任务链门禁：五份材料的每一步都要有模型可调用的工具，且链路端到端跑得通。

这份测试把"模型能操作完成材料里的任务"变成可执行判据：
1. 存在性：材料任务步 → 工具，缺一个即红；
2. 链式：以上游工具的真实输出当下游输入，跑通并核对物理量
   （比特不丢、帧载荷还原、多普勒往返、坐标还原…）。

材料清单对应 docs/learn/ 下的五份材料：
· 气象卫星图片接收赛前培训
· 定轨 Demo / 赛前培训-定轨
· ASTU-1 遥测接收
· 通信对抗（learnSDR 路线）
· （第五份：赛前培训-总览/综述类，已在能力地图登记为"赛训总览"，
  其可操作步骤由下面 气象/定轨/ASTU/对抗 四族覆盖）
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402

# 仓库夹具同款合法 TLE（IRIDIUM 107，行 2 为 69 字符"带平均近点角"变体）
TLE1 = ("1 42960U 17061F   25117.46489195  .00000241  00000+0  78821-4 0  9994")
TLE2 = ("2 42960  86.4033 198.9703 0001974 107.2578 252.8834 "
        "14.34218356395236")
T0 = 1745764814.0

# (材料, 任务步, 需要的工具)
TASK_STEPS = [
    ("气象", "接收剖面", ["sat_rx_profile"]),
    ("气象", "同步轨道指向", ["geo_pointing"]),
    ("气象", "抛物面增益与波束", ["dish_gain"]),
    ("气象", "指向容差与增益损失", ["pointing_tolerance"]),
    ("气象", "前端链路预算", ["fe_link_budget"]),
    ("气象", "接收验收判据", ["rx_acceptance"]),
    ("气象", "罐子馈源几何", ["cantenna_design"]),
    ("气象", "症状→环节诊断", ["fe_diagnose"]),
    ("气象", "LRIT 图像解码", ["gk2a_lrit_decode"]),
    ("气象", "FY-4 LRIT 解码", ["fy4_lrit_decode"]),
    ("气象", "FY-4 IQ 直达图", ["fy4_lrit_decode_iq"]),
    ("气象", "Meteor LRPT 解调", ["meteor_lrpt_demod"]),
    ("气象", "Meteor LRPT IQ 直达图", ["meteor_lrpt_decode_iq"]),
    ("气象", "图像增强", ["sat_image_enhance"]),
    ("气象", "过境预报", ["sat_pass_predict"]),
    ("定轨", "频偏→伪距率", ["doppler_from_frequency"]),
    ("定轨", "伪距率→频偏", ["doppler_to_frequency"]),
    ("定轨", "TLE→初值", ["tle_state_ecef"]),
    ("定轨", "状态传播+STM", ["propagate_state_stm"]),
    ("定轨", "观测模型+雅可比", ["pseudorange_rate_model"]),
    ("定轨", "RLS 单步", ["rls_update"]),
    ("定轨", "校时", ["calibrate_time_offset"]),
    ("定轨", "端到端定轨", ["doppler_orbit_determine"]),
    ("定轨", "坐标变换", ["coord_ecef_to_eci", "coord_eci_to_ecef",
                          "coord_ecef_to_enu", "coord_enu_to_az_el",
                          "coord_ecef_to_geodetic", "coord_geodetic_to_ecef"]),
    ("定轨", "深空参考轨迹", ["lro_track"]),
    ("ASTU", "接收剖面", ["sat_rx_profile"]),
    ("ASTU", "比特级接收链", ["telemetry_rx_chain"]),
    ("ASTU", "多普勒调谐", ["doppler_tune_frequency"]),
    ("ASTU", "CCSDS 帧构造", ["ccsds_encode_frame"]),
    ("ASTU", "CCSDS 比特解码", ["ccsds_decode_bits"]),
    ("ASTU", "CCSDS IQ 解码", ["ccsds_decode_iq"]),
    ("ASTU", "遥测文本还原(IQ→字符流)", ["astu_telemetry_decode"]),
    ("对抗", "统一调制", ["modulate_symbols"]),
    ("对抗", "统一解调", ["demodulate_symbols"]),
    ("对抗", "星座质量", ["constellation_metrics"]),
    ("对抗", "眼图与抖动", ["eye_diagram"]),
    ("对抗", "误码实测/理论", ["link_ber_test", "measure_ber"]),
    ("对抗", "成形抽头", ["pulse_shape_taps"]),
    ("对抗", "载波同步", ["fll_track"]),
    ("对抗", "符号定时恢复", ["symbol_sync_recover"]),
    ("对抗", "GMSK", ["gmsk_modulate", "gmsk_demodulate"]),
    ("对抗", "FreeDV", ["freedv_modulate", "freedv_demodulate"]),
    ("对抗", "M17 4FSK", ["m17_4fsk_roundtrip"]),
]


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_every_material_task_step_has_a_tool(reg):
    names = set(reg.get_tool_names())
    missing = [(mat, step, t) for mat, step, tools in TASK_STEPS
               for t in tools if t not in names]
    assert not missing, "材料任务步缺少可调用工具: " + "; ".join(
        f"[{m}] {s}: {t}" for m, s, t in missing)


def test_all_four_families_are_covered():
    mats = {m for m, _, _ in TASK_STEPS}
    assert mats == {"气象", "定轨", "ASTU", "对抗"}, mats


# ── 对抗链：调制 → 解调 → 星座/眼图 ──────────────────────────────
BITS = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1]


def test_modulation_chain_round_trips_bits(reg):
    mod = reg.call("modulate_symbols", {"bits": BITS, "scheme": "bpsk", "sps": 4})
    assert mod.success, mod.content[:120]
    demod = reg.call("demodulate_symbols",
                     {"iq": mod.data["waveform"], "scheme": "bpsk", "sps": 4})
    assert demod.success, demod.content[:120]
    assert demod.data["bits"] == BITS, "调制→解调比特应完全还原"


def test_constellation_metrics_on_chain_symbols(reg):
    mod = reg.call("modulate_symbols", {"bits": BITS, "scheme": "bpsk", "sps": 4})
    demod = reg.call("demodulate_symbols",
                     {"iq": mod.data["waveform"], "scheme": "bpsk", "sps": 4})
    con = reg.call("constellation_metrics",
                   {"symbols": demod.data["symbols"], "scheme": "bpsk"})
    assert con.success, con.content[:120]
    assert con.data["symbol_count"] == len(BITS)
    assert con.data["evm_rms_pct"] < 1.0, con.data["evm_rms_pct"]


def test_eye_diagram_on_chain_waveform(reg):
    mod = reg.call("modulate_symbols", {"bits": BITS, "scheme": "bpsk", "sps": 4})
    eye = reg.call("eye_diagram", {"samples": mod.data["waveform"], "sps": 4})
    assert eye.success, eye.content[:120]
    assert eye.data["eye_height"] > 0


def test_ber_measurement_agrees_with_truth(reg):
    b = reg.call("measure_ber", {"bits_tx": BITS, "bits_rx": BITS})
    assert b.success, b.content[:120]
    assert b.data.get("ber") == 0.0


# ── ASTU 链：CCSDS 帧构造 → 解码 ────────────────────────────────
def test_ccsds_frame_round_trip(reg):
    payload = [65, 66, 67, 68]
    enc = reg.call("ccsds_encode_frame", {"data": payload})
    assert enc.success, enc.content[:120]
    dec = reg.call("ccsds_decode_bits",
                   {"bits": enc.data["bits"],
                    "frame_bits_after_asm": enc.data["frame_bits_after_asm"]})
    assert dec.success, dec.content[:120]
    assert dec.data["count"] == 1, dec.data
    assert dec.data["frames"][0]["hex"] == "41424344"


def test_ccsds_zero_frames_reports_cause_not_silence(reg):
    """0 帧必须给出可执行诊断（不能只回 count=0）。"""
    enc = reg.call("ccsds_encode_frame", {"data": [65, 66, 67, 68]})
    dec = reg.call("ccsds_decode_bits", {"bits": enc.data["bits"]})
    assert dec.success
    assert dec.data["count"] == 0
    diag = dec.data.get("diagnostic")
    assert diag, "0 帧时必须带 diagnostic"
    assert diag["asm_found"] is True
    assert diag["cause"] == "frame_bits_after_asm 与输入不匹配"
    assert str(enc.data["frame_bits_after_asm"]) in diag["hint"]


# ── 气象链：指向 → 增益 → 容差 → 链路预算 → 验收 ───────────────
def test_weather_reception_chain(reg):
    gp = reg.call("geo_pointing", {"observer_lat_deg": 46.6,
                                   "observer_lon_deg": 127.0,
                                   "satellite": "GK-2A"})
    assert gp.success, gp.content[:120]
    assert 0.0 < gp.data["elevation_deg"] < 90.0
    assert gp.data["range_km"] > 30000.0

    gain = reg.call("dish_gain", {"diameter_m": 1.8, "frequency_mhz": 1690.0})
    assert gain.success, gain.content[:120]
    assert 20.0 < gain.data["gain_dbi"] < 40.0

    tol = reg.call("pointing_tolerance", {"beamwidth_deg": 6.9, "offset_deg": 1.0})
    assert tol.success, tol.content[:120]
    assert tol.data["within_tolerance"] is True
    assert tol.data["tolerance_deg"] == 2.0

    budget = reg.call("fe_link_budget", {"diameter_m": 1.8,
                                         "frequency_mhz": 1690.0,
                                         "measured_snr_db": 12.0})
    assert budget.success, budget.content[:120]
    assert budget.data["system_noise_figure_db"] > 0.0

    acc = reg.call("rx_acceptance", {"pointing_error_deg": 1.0, "snr_db": 12.0,
                                     "decode_attempts": 100, "decode_successes": 99,
                                     "lna_nf_db": 0.7, "feedline_m": 2.0,
                                     "system_nf_db": 1.2, "dish_offset_cm": 1.0})
    assert acc.success, acc.content[:120]
    assert acc.data["verdict"] == "通过"

    cant = reg.call("cantenna_design", {"frequency_mhz": 1690.0,
                                       "tank_inner_diameter_mm": 126.0})
    assert cant.success, cant.content[:120]
    assert cant.data["cutoff_margin_mhz"] > 0.0

    diag = reg.call("fe_diagnose", {"symptom": "图像有条纹"})
    assert diag.success, diag.content[:120]
    assert diag.data["steps"]


# ── 定轨链：TLE → 传播 → 观测模型；多普勒往返；坐标往返 ─────────
def test_orbit_chain_tle_to_observation_model(reg):
    tle = reg.call("tle_state_ecef", {"tle_line1": TLE1, "tle_line2": TLE2,
                                      "timestamp": T0})
    assert tle.success, tle.content[:120]
    st = tle.data["state_ecef"]
    assert len(st) == 7

    prop = reg.call("propagate_state_stm", {"state_ecef": st, "t_from": T0,
                                            "t_to": T0 + 60.0})
    assert prop.success, prop.content[:120]
    assert len(prop.data["stm"]) == 7 and len(prop.data["stm"][0]) == 7

    obs = reg.call("pseudorange_rate_model",
                   {"sat_state_ecef": st,
                    "receiver_ecef": [-2637.85, 3505.12, 4614.7]})
    assert obs.success, obs.content[:120]
    assert len(obs.data["jacobian"]) == 7
    assert abs(obs.data["range_rate_mps"]) < 8000.0


def test_doppler_round_trip(reg):
    f, fc = 1626303262.0, 1626270833.0
    d1 = reg.call("doppler_from_frequency", {"frequency_hz": f,
                                              "center_frequency_hz": fc})
    assert d1.success, d1.content[:120]
    d2 = reg.call("doppler_to_frequency",
                  {"range_rate_mps": d1.data["range_rate_mps"],
                   "center_frequency_hz": fc})
    assert d2.success, d2.content[:120]
    assert d2.data["frequency_hz"] == pytest.approx(f, rel=1e-12)


def test_coordinate_chain_round_trips(reg):
    pos = [-21172.643577322124, -22651.07580238663, -28725.206028710905]
    vel = [1.0053192260089316, 0.5866519247996413, -1.2034799680983272]

    eci = reg.call("coord_ecef_to_eci", {"pos_ecef": pos, "vel_ecef": vel,
                                         "timestamp": T0})
    assert eci.success, eci.content[:120]
    back = reg.call("coord_eci_to_ecef", {"pos_eci": eci.data["pos_eci"],
                                          "vel_eci": eci.data["vel_eci"],
                                          "timestamp": T0})
    assert back.success, back.content[:120]
    for a, b in zip(back.data["pos_ecef"], pos):
        assert a == pytest.approx(b, abs=1e-6)

    ground = [-2178656.37160527, 4388874.80101017, 4069504.67455964]
    geo = reg.call("coord_ecef_to_geodetic", {"ecef": ground})
    assert geo.success, geo.content[:120]
    assert geo.data["lat_deg"] == pytest.approx(39.9, abs=1e-6)
    assert geo.data["lon_deg"] == pytest.approx(116.4, abs=1e-6)
    assert geo.data["alt_m"] == pytest.approx(50.0, abs=1e-3)
    ecef2 = reg.call("coord_geodetic_to_ecef",
                     {"lat_deg": geo.data["lat_deg"],
                      "lon_deg": geo.data["lon_deg"],
                      "alt_m": geo.data["alt_m"]})
    assert ecef2.success, ecef2.content[:120]
    for a, b in zip(ecef2.data["ecef"], ground):
        assert a == pytest.approx(b, abs=1e-3)

    enu = reg.call("coord_ecef_to_enu", {"target_ecef": pos,
                                          "receiver_ecef": [-2637.85, 3505.12, 4614.7]})
    assert enu.success, enu.content[:120]
    azel = reg.call("coord_enu_to_az_el", {"enu": enu.data["enu"]})
    assert azel.success, azel.content[:120]
    assert "azimuth_deg" in azel.data and "elevation_deg" in azel.data
