# SPDX-License-Identifier: MIT
"""MCP 工具面 ↔ agent 工具注册表的显式子集映射。

两个工具面各自独立，不能假定同名：

  * MCP 面（``MCPToolSet``）：暴露 ``SDRController`` 方法，用短名
    （``set_frequency`` / ``start_recording``），供 MCP 客户端按域取用；
  * agent 面（``ToolRegistry``）：给模型/子 agent 用的全量工具，多数带
    ``sdr_`` 前缀（``sdr_set_frequency`` / ``sdr_record_start``）。

唯一可机械判定的对应关系是 ``ToolRegistry.resolve_tool_name`` 已登记的
短名别名。本模块把它冻结成 :data:`MCP_TO_REGISTRY`；名字相同且两边都注册的
``sat_link_*`` 也在其中（恒等映射）。其余 MCP 工具显式登记为
「MCP 独有」，其余 registry 工具显式登记为「registry 独有」——两边的差异
都是**已登记**的，不是漂移。

``tests/test_tool_surface_audit.py`` 断言：
  * 每个映射目标都真实存在于 registry，且与别名解析器的结果逐项一致；
  * ``MCP 独有集合 == MCP_ONLY_WHITELIST``；
  * ``registry 独有集合 == REGISTRY_ONLY_WHITELIST``；
  * 多一个/少一个都红（新增工具必须显式登记）。

不改变任何工具的执行语义；这里只有名字映射与差集白名单。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Set

#: MCP 工具名 → registry 注册名。只收「别名解析器能真实解析到注册名」的项。
MCP_TO_REGISTRY: Dict[str, str] = {
    "connect": "sdr_connect",
    "disconnect": "sdr_disconnect",
    "get_frequency": "sdr_get_frequency",
    "list_devices": "sdr_list_devices",
    "sat_link_get": "sat_link_get",
    "sat_link_identify": "sat_link_identify",
    "sat_link_list": "sat_link_list",
    "sat_link_tuning": "sat_link_tuning",
    "set_bandwidth": "sdr_set_bandwidth",
    "set_demod": "sdr_set_demod",
    "set_frequency": "sdr_set_frequency",
    "set_gain": "sdr_set_gain",
    "set_sample_rate": "sdr_set_sample_rate",
    "set_squelch": "sdr_set_squelch",
    "set_volume": "sdr_set_volume",
}

#: 只存在于 MCP 面、registry 没有对应工具的名字（显式登记）。
MCP_ONLY_WHITELIST: Set[str] = frozenset({
    "add_bookmark",
    "add_vfo",
    "aigui_component_catalog",
    "aigui_render_preview",
    "aigui_validate_message",
    "answer_question",
    "ask_question",
    "decoder_dab_fic_decode",
    "decoder_fst4_encode_text",
    "decoder_gk2a_parse_headers",
    "decoder_nrsc5_frame_parse",
    "decoder_rtl433_decode",
    "decoder_rtl433_list",
    "delete_bookmark",
    "device_bladerf_params_query",
    "export_peaks_csv",
    "export_spectrum_snapshot",
    "export_vna_report",
    "find_nearest_bookmark",
    "get_decoder_messages",
    "get_demod",
    "get_recording_status",
    "get_satellite_pass",
    "get_scan_results",
    "get_service_status",
    "get_status",
    "is_connected",
    "learn_noise_floor",
    "list_audio_outputs",
    "list_bookmarks",
    "list_decoders",
    "list_satellites",
    "list_vfos",
    "mcp_client_call",
    "mcp_client_connect",
    "mcp_client_disconnect",
    "mcp_client_list",
    "question_status",
    "read_audio",
    "read_iq",
    "read_spectrum",
    "remove_vfo",
    "set_anr_enabled",
    "set_anr_strength",
    "set_audio_output",
    "set_deemphasis",
    "set_ground_station",
    "set_iq_invert",
    "set_mute",
    "set_primary_vfo",
    "shutdown",
    "start_analyze",
    "start_audio",
    "start_decoder",
    "start_recording",
    "start_remote_control",
    "start_scan",
    "start_web_server",
    "stop_audio",
    "stop_decoder",
    "stop_recording",
    "stop_remote_control",
    "stop_scan",
    "stop_web_server",
    "tune_satellite",
    "vna_analyze_sweep",
    "vna_apply_calibration",
    "vna_solve_error_box",
})

#: 只存在于 registry、MCP 面没有对外的名字（显式登记；新工具入册必须登记）。
REGISTRY_ONLY_WHITELIST: Set[str] = frozenset("""
acars_decode_iq
acars_msk_demod
acars_parse_message
adsb_decode_iq
afsk1200_demod
afsk_demodulate
afsk_modulate
agc_process
alpha_estimate
amr_add_sample
amr_classify
amr_extract_features
amr_stats
aprs_decode_audio
aprs_parse
apt_decode_audio
ar_pointing_guidance
ar_project_satellite
ardop_build_frame
ardop_constellation
ardop_state_machine
array_correlator_df
astro_altaz_to_equatorial
astro_antenna_params
astro_compute_airmass
astro_compute_refraction
astro_equatorial_to_altaz
astro_get_observer
astro_pointing_guidance
astro_set_observer
astro_time_info
astu_telemetry_decode
ax25_decode
ax25_encode
baseband_save
baudot_decode
baudot_encode
bch_decode
bch_encode
bch_generator
bell103_demodulate_text
bell103_modulate_text
bladerf_get_freq_range
bladerf_list_gains
bladerf_set_params
calibrate_time_offset
camera_capture_plan
cantenna_design
ccsds_decode_bits
ccsds_decode_iq
ccsds_encode_frame
cma_equalize
code_git_commit
code_git_status
code_hot_reload
code_list_edits
code_modify_file
code_modify_section
code_read_file
code_rollback
code_run_tests
code_stats
codec2_decode
codec2_encode
constellation_evm
constellation_metrics
context_status
coord_ecef_to_eci
coord_ecef_to_enu
coord_ecef_to_geodetic
coord_eci_to_ecef
coord_enu_to_az_el
coord_geodetic_to_ecef
costas_track
crc_check
cubicsdr_drag_tune
cubicsdr_vfo_state_machine
cubicsdr_waterfall_colormap
cw_decode_audio
dab_decode_iq
dab_eti_parse
dab_fic_decode
dehop_demod
demod_analog_audio
demod_wfm_stereo
demodulate_symbols
df_amplitude_compare
diff_decode
diff_encode
digital_mode_frequencies
digital_mode_params
dish_gain
dmr_decode
doppler_calc
doppler_from_frequency
doppler_orbit_determine
doppler_to_frequency
doppler_tune_frequency
downchannelize
dsd_decode_iq
dsp_engine_create
dtmf_decode
ecef_to_llh
energy_sense
estimate_ppm_offset
evolution_commit
evolution_evaluate
evolution_history
evolution_propose
evolution_rollback
evolution_status
eye_diagram
fe_diagnose
fe_link_budget
feldhell_decode
fft_filter
fhss_hop_track
field_extract
file_change_changelog
file_change_history
file_change_revert
file_change_stats
file_change_track
fir_filter
fll_track
fourfsk_demod
frame_layout_list
frame_layout_load
frame_layout_spec_load
freedv_demodulate
freedv_modulate
fsk_demodulate
fsk_modulate
fst4_encode
fst4_ldpc_decode
ft8_decode_audio
ft8_encode
ft8_ldpc_decode
ft8_soft_decode
ft8_unpack_callsign
ft8_unpack_message
fy3_hrpt_decode
fy3_pass_predict
fy4_lrit_decode
fy4_lrit_decode_iq
geo_pointing
gimbal_connect
gimbal_modes
gimbal_move
gimbal_point
gimbal_read_pose
gimbal_rssi_sweep
gimbal_stop
gimbal_track_satellite
gis_bearing
gis_destination
gis_distance
git_clone_repo
gk2a_lrit_decode
gmsk_demodulate
gmsk_modulate
gnss_direction_find
gnss_monitor_all
gnss_monitor_band
gnss_parse_rmc
gnss_sky_fit
gnss_system_info
goes_extract_image
goes_hrit_parse
goes_lrit_parse
golden_assert
gps_code_acquire
gps_ephemeris_satpos
gps_nav_decode
gps_prn_sequence
gps_pvt_solve
gps_time_to_utc
gqrx_receiver_create
guardian_list
guardian_rollback
guardian_status
hackrf_get_freq_range
hackrf_list_gains
hackrf_set_params
hdradio_decode_iq
hdradio_frame_parse
hdradio_ofdm_demod
hdsdr_bandwidth_preset
hdsdr_gain_table
hdsdr_rf_gain
how_validate
initial_orbit_laplace
inspectrum_measure_bandwidth
inspectrum_measure_duty_cycle
inspectrum_measure_period
instrument_list
instrument_query
interference_assess
interferometry_resolution
interferometry_tdoa
iq_correct
iq_freq_track
iq_manifest_audit
iq_split_plan
jammer_generate
js8_decode_message
js8_encode_message
js8_submodes
judge_evaluate
judge_history
judge_stats
kismet_device_list
kismet_discover_devices
kismet_parse_dot11
learning_experiences
learning_patterns
learning_record
learning_stats
limesdr_get_freq_range
limesdr_list_gains
limesdr_set_params
link_ber_test
list_models
list_tools
lro_doppler_predict
lro_od_demo
lro_orbit_info
lro_track
lrpt_decode_full
m17_4fsk_roundtrip
m17_conv_roundtrip
m17_crc16
m17_lsf_build
measure_ber
memory_search
memory_write
meteor_demod_setup
meteor_get_params
meteor_list_satellites
meteor_lrpt_decode_iq
meteor_lrpt_demod
meteor_viterbi_decode
mfsk_decode
minimodem_decode_audio
model_status
modulate_symbols
modulation_classify
morse_decode
morse_encode
mount_abort
mount_slew
mount_status
multistation_tdoa_fix
nav_bit_sync
noaa_apt_decode
ntrip_connect
observation_export
openapi_aircraft_nearby
openapi_iss_position
openapi_list_apis
openapi_people_in_space
openapi_weather
orbit_error_plot
orbit_propagate_stm_j2
orbit_sky_plot
osmosdr_create_source
osmosdr_get_gain_ranges
osmosdr_list_devices
owrx_device_bandwidth_adapt
owrx_fft_average
owrx_plan_fft
owrx_waterfall_colormap
p25_decode
p25_imbe_parse
p25_nid_decode
p25_nid_roundtrip
p25_phase2_info
pat_b2f_decode
pat_b2f_encode
pat_list_transports
pat_mid_list
pcm_psk_pm_modulate
pfb_resample
platform_info
pnt_get_state
pnt_update_source
pocsag_bch_correct
pocsag_decode
pointing_tolerance
pose_get
pose_update_gps
pose_update_imu
predict_satellite_passes
propagate_state_stm
protocol_stack_decode
protocol_stack_list
protocol_stack_split_frames
pseudorange_rate_model
psk31_decode
psk31_encode
pulse_shape_taps
r2cloud_recording_meta
r2cloud_schedule
radio_connect
radio_list_ports
radio_ptt
radio_send_cw
radio_set_frequency
radio_set_mode
rational_resample
rds_decode_groups
rds_decode_mpx
rds_extract_ps_rt
rinex_parse
rls_update
rtlsdr_get_freq_range
rtlsdr_list_gains
rtlsdr_set_params
rtty_decode
rx_acceptance
sat_compose_falsecolor
sat_image_enhance
sat_pass_predict
sat_profile_list
sat_profile_load
sat_project_image
sat_rx_profile
satdump_check
satdump_compose_image
satdump_list_sats
satdump_live
satdump_process
satellite_now_pointing
satellite_predict_all
satellite_predict_pass
satnogs_doppler_plan
satnogs_list_modes
scheduler_add
scheduler_disable
scheduler_enable
scheduler_list
scheduler_status
scheduler_tick
sdr_adsb_decode
sdr_ai_find_center
sdr_ai_sweep
sdr_analyze_recording
sdr_anr_denoise
sdr_aprs_decode
sdr_aprs_encode
sdr_aprs_send_position
sdr_bookmark_add
sdr_bookmark_goto
sdr_bookmark_list
sdr_cfo_correct
sdr_connect_hardware
sdr_constellation_scatter
sdr_decode_adsb
sdr_decode_aprs
sdr_decode_ax25
sdr_decode_cw
sdr_decode_ft8
sdr_decode_noaa_apt
sdr_decode_sstv
sdr_demodulate
sdr_detect_fhss
sdr_digipeater_process
sdr_doppler_compensation
sdr_encode_ax25
sdr_fm_preset
sdr_fm_scan
sdr_get_gps
sdr_get_imu
sdr_identify_modulation
sdr_iq_correct
sdr_kiss_decode
sdr_kiss_encode
sdr_list_hardware
sdr_listen_fm
sdr_measure_signal
sdr_open_iq_file
sdr_plan
sdr_play_audio
sdr_preconnect
sdr_rds_decode
sdr_record_start
sdr_record_stop
sdr_recordings_list
sdr_satellite_doppler
sdr_satellite_doppler_track
sdr_satellite_passes
sdr_satellite_sky_view
sdr_set_agc
sdr_signal_overview
sdr_spectrum_analyze
sdr_spectrum_center_offset
sdr_spectrum_find_signals
sdr_spectrum_pan
sdr_spectrum_peaks
sdr_spectrum_screenshot
sdr_spectrum_text
sdr_spectrum_zoom
sdr_spill_read
sdr_spill_save
sdr_squelch_gate
sdr_status
sdr_sweep_scan
sdr_switch_device
sdr_todo
sdr_transmit_cw
sdr_transmit_iq
sdr_watch_capture
sdr_wfm_stereo
sdrangel_firedetector
sdrangel_ogn_decode
sdrangel_remotecontrol
sdrsharp_list_devices
sdrsharp_make_window
sdrsharp_plugin_contract
sdrsharp_spectrum_fft
sdrtrunk_affiliation
sdrtrunk_channel_grant
sdrtrunk_snapshot
sdrtrunk_state_transition
set_ppm_correction
sgp4_propagate
signal_detect
signal_detect_interference
signal_extract_features
signal_identify_modulation
signal_quality_stats
sigutils_detect_channels
sigutils_identify_modulation
sigutils_peak_detector
skill_list
skill_load
sky_asterism_lookup
sky_calibration_save
sky_capability_report
sky_capability_state
sky_culture_lookup
sky_gimbal_follow
sky_health
sky_identify_at
sky_look_at
sky_meteor_showers
sky_pointing_check
sky_pose_fuse
sky_dso_lookup
sky_object_lookup
sky_observing_sites
sky_panorama
sky_solve_status
sky_tonight
sky_track_satellite
sky_tune_satellite
sky_view_visible
sky_whats_here
spectrum_analyze_iq
spp_locate
ssdv_ccsds_decode
subagent_create
subagent_execute
subagent_list
subagent_stats
subframe_assemble
switch_model
symbol_rate_estimate
symbol_sync_recover
tc_build_frame
tc_frame_parse
tc_uplink_transmit
telemetry_rx_chain
time_get_info
time_ntp_sync
time_sync_ntp
tle_parse
tle_state_ecef
tool_log
ui_layout_control
ui_layout_undo
urh_decode_iq
urh_demodulate
urh_modulate
urh_sync_detect
verify_decode_chain
vfo_add
vfo_list
vfo_remove
vfo_select
vna_analyze
vna_list_ports
vna_sweep
web_fetch_url
workflow_execute
workflow_list
workflow_record_start
workflow_record_stop
workflow_recorder_status
workflow_template_create
workflow_template_list
wsjtx_read_decodes
xastir_decode_frame
xastir_encode_object
xastir_geofence
zvei_decode
""".split())


@dataclass(frozen=True)
class SurfaceDiff:
    """一次差集计算的结果；``*_unexpected`` / ``*_missing`` 非空即漂移。"""

    mapped: Dict[str, str]
    mcp_only: List[str]
    registry_only: List[str]
    mcp_only_unexpected: List[str]
    mcp_only_missing: List[str]
    registry_only_unexpected: List[str]
    registry_only_missing: List[str]
    mapping_missing_in_registry: List[str]
    mcp_undeclared: List[str]
    mapping_stale: List[str]

    @property
    def clean(self) -> bool:
        """全部差异都落在已登记白名单内，且映射目标齐全。"""
        return not any((
            self.mcp_only_unexpected, self.mcp_only_missing,
            self.registry_only_unexpected, self.registry_only_missing,
            self.mapping_missing_in_registry, self.mcp_undeclared,
            self.mapping_stale,
        ))


def diff_surfaces(mcp_names: Iterable[str],
                  registry_names: Iterable[str]) -> SurfaceDiff:
    """计算 MCP 面与 registry 面的差集，并逐项对照白名单。"""
    mcp = set(mcp_names)
    registry = set(registry_names)
    mapped = {n: MCP_TO_REGISTRY[n] for n in mcp if n in MCP_TO_REGISTRY}
    mcp_only = mcp - set(MCP_TO_REGISTRY)
    registry_only = registry - set(MCP_TO_REGISTRY.values())
    return SurfaceDiff(
        mapped=mapped,
        mcp_only=sorted(mcp_only),
        registry_only=sorted(registry_only),
        mcp_only_unexpected=sorted(mcp_only - MCP_ONLY_WHITELIST),
        mcp_only_missing=sorted(MCP_ONLY_WHITELIST - mcp_only),
        registry_only_unexpected=sorted(
            registry_only - REGISTRY_ONLY_WHITELIST),
        registry_only_missing=sorted(
            REGISTRY_ONLY_WHITELIST - registry_only),
        mapping_missing_in_registry=sorted(
            v for v in set(MCP_TO_REGISTRY.values()) if v not in registry),
        mcp_undeclared=sorted(mcp - set(MCP_TO_REGISTRY) - MCP_ONLY_WHITELIST),
        mapping_stale=sorted(set(MCP_TO_REGISTRY) - mcp),
    )


def resolve_mcp_tool(name: str, registry: object) -> Optional[str]:
    """用 registry 自己的别名解析规则求 MCP 名对应的注册名；无对应返回 None。

    ``MCP_TO_REGISTRY`` 必须与这里的结果逐项一致——映射是规则的结果，不是
    手抄快照。``registry`` 需提供 ``tools`` 与 ``resolve_tool_name``。
    """
    tools: Mapping[str, object] = getattr(registry, "tools", {})
    try:
        resolved = registry.resolve_tool_name(name)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 缺解析器时按「无对应」处理
        return None
    return resolved if resolved in tools else None
