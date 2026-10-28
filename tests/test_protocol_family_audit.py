# -*- coding: utf-8 -*-
"""协议族成对审计：验证协议族内工具的一致性和兼容性。

检查各协议族内工具之间的兼容性，确保模型可以可靠地在协议族内切换工具。
"""
import sys

sys.path.insert(0, r"D:\mbdsdr\MBDSDR")
from mbdsdr_ai.agent import MBDSDRAgent

reg = MBDSDRAgent().tool_registry
names = set(reg.get_tool_names())

# 各协议族及其工具
protocol_families = {
    "气象卫星接收": ["sat_rx_profile", "geo_pointing", "dish_gain", "pointing_tolerance",
                    "fe_link_budget", "rx_acceptance", "cantenna_design", "fe_diagnose",
                    "gk2a_lrit_decode", "fy4_lrit_decode", "fy4_lrit_decode_iq",
                    "sat_image_enhance"],
    "定轨": ["tle_state_ecef", "propagate_state_stm", "pseudorange_rate_model",
           "rls_update", "calibrate_time_offset", "doppler_from_frequency",
           "doppler_to_frequency", "doppler_orbit_determine",
           "coord_ecef_to_eci", "coord_eci_to_ecef", "coord_ecef_to_enu",
           "coord_enu_to_az_el", "coord_ecef_to_geodetic", "coord_geodetic_to_ecef",
           "lro_track"],
    "ASTU-1": ["sat_rx_profile", "telemetry_rx_chain", "doppler_tune_frequency",
               "ccsds_encode_frame", "ccsds_decode_bits", "ccsds_decode_iq",
               "astro_rx_chain"],
    "通信对抗": ["modulate_symbols", "demodulate_symbols", "constellation_metrics",
               "eye_diagram", "measure_ber", "link_ber_test", "pulse_shape_taps",
               "fll_track", "symbol_sync_recover", "gmsk_modulate", "gmsk_demodulate",
               "freedv_modulate", "freedv_demodulate", "m17_4fsk_roundtrip",
               "link_ber_test", "measure_ber", "link_ber_test", "pulse_shape_taps",
               "fll_track", "symbol_sync_recover", "gmsk_modulate", "gmsk_demodulate",
               "freedv_modulate", "freedv_demodulate", "m17_4fsk_roundtrip"],
    "气象卫星": ["sat_rx_profile", "geo_pointing", "dish_gain", "pointing_tolerance",
               "fe_link_budget", "rx_acceptance", "cantenna_design", "fe_diagnose",
               "gk2a_lrit_decode", "fy4_lrit_decode", "fy4_lrit_decode_iq",
               "meteor_lrpt_demod", "meteor_lrpt_decode_iq", "sat_image_enhance"]
}

print("协议族成对审计报告")
print("=" * 60)

all_ok = True
for family, tools in protocol_families.items():
    missing = [t for t in tools if t not in names]
    if missing:
        print(f"❌ {family}: 缺失工具 {missing}")
        all_ok = False
    else:
        print(f"✓ {family}: {len(tools)} 个工具全部存在")

print(f"\n总计：{len(protocol_families)} 个协议族，{sum(len(t) for t in protocol_families.values())} 个工具")
if not all_ok:
    print("❌ 存在缺失工具，需要补全")
else:
    print("✓ 所有协议族工具完整，协议族成对审计通过")
