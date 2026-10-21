# SPDX-License-Identifier: MIT
"""工具结果诚实语义门禁 + 载荷契约门禁。

两组断言：
 A) 诚实语义
   1) handler 以"错误:"开头的文本 = 失败信号（仓内 50+ 处约定），必须
      success=False——无论 handler 用哪种包装（裸字符串 / dict / 显式 ToolResult）；
   2) 失败时 content 不得为空串：必须回落到 error 文本，模型侧才看得见原因。
 B) 载荷契约
   能成功跑完的"结果型"工具必须把结构化结果放进 data（模型能程序化取用，
   而不是只能读文本）。表内每个条目都带最小可运行参数。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


# ── A) 诚实语义 ────────────────────────────────────────────────
def test_error_prefix_text_is_failure_not_success(reg):
    """缺参/未连接等失败信号不得被读成成功。"""
    r = reg.call("sdr_decode_ax25", {})
    assert r.success is False, r.content[:120]
    assert r.error, "失败结果应带稳定 error 码"
    assert r.content.strip(), "失败结果不得是空串"


def test_error_prefix_text_with_explicit_toolresult_wrapper(reg):
    """handler 显式包 ToolResult(success=True, ...) 也必须被纠正。"""
    r = reg.call("sdr_adsb_decode", {})
    assert r.success is False, r.content[:120]
    assert r.content.strip()


def test_failure_without_content_falls_back_to_error(reg):
    """失败且 content 为空时，用 error 文本兜底（模型侧不能只看到空串）。"""
    r = reg.call("doppler_orbit_determine", {
        "observations": [{"t": 1745764805.0, "fd": 32429.0}],
        "rx_lat": 46.64, "rx_lon": 126.96, "rx_alt": 195.0,
        "init_state": [-3381.19, 4727.70, 4169.14, 0.5, -1.0, 2.0, 0.0],
    })
    assert r.success is False
    assert r.content.strip(), "失败结果 content 不得为空"
    assert "观测不足" in r.content or r.error in r.content


# ── B) 载荷契约（能成功跑完就必须给 data）─────────────────────
PAYLOAD_CASES = [
    ("ax25_encode", {"source": "BA1234", "destination": "APRS", "info": "T"}),
    ("ax25_decode", {"hex": "82A0A4A64040E08482626466686103F0"}),
    ("sdr_aprs_encode", {"source": "BI4MIB", "latitude": 45.0, "longitude": 120.0,
                          "comment": "T"}),
    ("sdr_aprs_decode", {"hex_data": "82A0A4A64040E08482626466686103F0"}),
    ("sdr_kiss_encode", {"ax25_hex": "82A0A4A64040E0848262646668"}),
    ("sdr_kiss_decode", {"hex_data": "F082A0A4A64040E0848262646668F0"}),
    ("morse_encode", {"text": "SOS"}),
    ("morse_decode", {"morse": "... --- ..."}),
    ("psk31_encode", {"text": "CQ TEST"}),
    ("fst4_encode", {"text": "CQ TEST"}),
    ("ft8_encode", {"text": "CQ BI1AAA OM74"}),
    ("js8_encode_message", {"de": "BI4MIB", "to": "BA1234"}),
    ("rds_decode_groups", {"blocks_hex": "0A1B 2C3D 4E5F 6071"}),
    ("hdradio_decode_iq", {"iq": [0.0] * 64}),
    ("adsb_decode_iq", {"iq": [0.0] * 64}),
    ("ccsds_decode_bits", {"bits": [0] * 2048}),
    ("tle_state_ecef", {"tle_line1": "1 25544U 98067A   24001.50000000 "
                                   " .00000000  00000-0  00000+0 0  9995",
                         "tle_line2": "2 25544  51.6400 100.0000 "
                                      "0005000  50.0000  10.0000 1 05",
                         "timestamp": 1704067200.0}),
    ("doppler_from_frequency", {"frequency_hz": 1626303262.0,
                                "center_frequency_hz": 1626270833.0}),
    ("pseudorange_rate_model", {
        "sat_state_ecef": [-3381.19, 4727.70, 4169.14, 0.5, -1.0, 2.0, 0.0],
        "receiver_ecef": [-2637.85, 3505.12, 4614.7]}),
    ("calibrate_time_offset", {
        "observations": [[1745764805.0, 1626303262.0]],
        "receiver_ecef": [-2637.85, 3505.12, 4614.7],
        "center_frequency_hz": 1626270833.0,
        "tle_line1": "1 42960U 17061F   25117.46489195  .00000241  00000+0  78821-4 0  9994",
        "tle_line2": "2 42960  86.4033 198.9703 0001974 107.2578 252.8834 14.34218356395236"}),
    ("geo_pointing", {"observer_lat_deg": 46.64, "observer_lon_deg": 126.96,
                      "satellite": "GK-2A"}),
    ("dish_gain", {"diameter_m": 1.8, "frequency_mhz": 1690.0}),
    ("fe_link_budget", {"diameter_m": 1.8, "frequency_mhz": 1690.0}),
    ("cantenna_design", {"frequency_mhz": 1690.0, "tank_inner_diameter_mm": 126.0}),
    ("fe_diagnose", {"symptom": "无信号"}),
    ("modulate_symbols", {"bits": [1, 0, 1, 1], "scheme": "bpsk", "sps": 4}),
    ("constellation_metrics", {"symbols": [1 + 0j, -1 + 0j]}),
    ("measure_ber", {"bits_tx": [1, 0], "bits_rx": [1, 0]}),
    ("link_ber_test", {"scheme": "bpsk", "n_bits": 512}),
    ("pulse_shape_taps", {"sps": 4}),
]


@pytest.mark.parametrize("tool,args", PAYLOAD_CASES)
def test_result_tools_expose_structured_data(reg, tool, args):
    """成功的结果型工具必须把结构化结果放进 data（模型可程序化取用）。"""
    r = reg.call(tool, args)
    assert r.success is True, f"{tool}: {r.content[:120]}"
    assert r.data, f"{tool}: 成功但 data 为空（载荷契约缺口）"
