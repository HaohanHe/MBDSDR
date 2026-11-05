# 测试里的「源码文本 pin」扫描清单

> 自动段由 `python scripts/audit_source_text_pins.py --report docs/source-text-pins-audit.md` 生成；
> manual 段（文件头部的判定）不会随重生成丢失。

<!-- manual:begin -->
## 判定（人工，2026-11-06；只读扫描，测试改动交各文件所属线）

判定单位＝**测试函数**：同一函数内多处文字断言同因同判；逐条位置见文末自动清单。
本仓已因此栽过两次：`test_u03_decoder_rule_copy` 钉整句、`test_settings_convergence`
钉 `float(obj.frequency_hz)`（被 `36607bbaa` 改成局部 `freq_hz` 后误红）。

| 编号 | 处置 | 判据 | 处数 | 测试函数数 |
|---|---|---|---:|---:|
| V1 | **改行为断言** | 钉的是接线/调用/控制流（`settings.set(...)`、`connect(...)`、`set_gain_range`、`_show_help_panel(1)` 等），测试里可用 fake host / 信号 / 控件回读直接观测 | 73 | 31 |
| V2 | 保留：负向守卫 | 断言「不得出现 X」（`not in` / `count(X)==0` / `banned`），行为断言无法证明「不存在」 | 18 | 7 |
| V3 | 保留：文案/单位审计 | 钉用户可见中文字面量或单位词（Msps → MS/s、dBFS、°），审计对象就是文案，且跨 Py/C++/JS 多文件 | 101 | 约 45 |
| V4 | 保留：跨语言常量登记 | 钉 `kXxx`、`SQUELCH_*` 等常量名，与 C++ 头文件/登记表比对，行为断言取不到 C++ token | 9 | 4 |
| V5 | 文档/数据 pin | 读的是 `.md`/`.json`/html 或表格数据，**不计入源码 pin** | 4 | 4 |
| V6 | 待复核 | 派生变量无法判定（`bad`、`banned`、`table`、`choice`、`api_base` 等），需所属线读上下文 | 58 | — |
| — | **合计** | | **263** | **33 个文件** |

### 必须改（V1，73 处 / 31 个函数）

| 测试函数 | 处数 | 钉的接线 |
|---|---:|---|
| `test_settings_convergence.py::test_gui_config_legacy_values_migrate_into_settings` | 3 | `settings.set("gnss_port"/"gnss_baudrate"/"theme"` |
| `test_settings_convergence.py::test_gnss_dialog_persists_through_settings` | 2 | 同上 |
| `test_settings_convergence.py::test_touch_mode_ui_entry_exists` | 4 | `"触屏模式"`、`def _set_touch_mode`、`_set_touch_mode(m)` |
| `test_settings_convergence.py::test_drifting_keys_migrate_into_settings_once` | 5 | `settings.set("frequency_hz"/"demod_mode"/"volume"/"show_waterfall"`、`* 1e6` |
| `test_settings_convergence.py::test_volume_and_waterfall_now_persist_through_settings` | 2 | `settings.set("volume"/"show_waterfall"` |
| `test_settings_convergence.py::test_volume_wire_conversion_isolated_at_boundary` | 3 | `volume_pct_to_hw` / `volume_hw_to_pct` / `volume / 63.0` |
| `test_settings_convergence.py::test_gain_dynamic_range_wired_on_connect` | 2 | `set_gain_range`、`max_gain` |
| `test_settings_convergence.py::test_panel_has_no_dead_slots` | 2 | `def {slot}`、`connect(self.{slot})` |
| `test_settings_convergence.py::_mutated_sky_handler` | 1 | `src.count(guard)` |
| `test_ui_wiring_audit.py::test_module_bandwidth_now_reaches_backend` | 4 | `dev["_bandwidth"] = ...`、`backend.set_bandwidth(...)` |
| `test_ui_wiring_audit.py::test_anr_strength_applies_through_panel_internal_write` | 2 | `self._anr.cfg.alpha = alpha`、`def _on_anr_strength` |
| `test_audio_route_anr.py::test_anr_tap_wired_in_main_path` | 2 | `self.audio_tap(audio)`、`pipe._demod1.audio_tap = ...` |
| `test_command_palette.py::test_main_window_exposes_locator_and_registered_shortcut` | 2 | `_key("help.command_palette")`、`_show_command_palette` |
| `test_discoverability_help.py::test_help_menu_is_wired_in_main_window` | 4 | `QAction("{label}"`、`_show_help_panel(1/0)`、`_show_shortcuts_panel` |
| `test_discoverability_help.py::test_static_capabilities_symbols_exist` | 2 | `_tx_block_reason`、`self.tx_button` |
| `test_info_card_hud.py::test_overlay_wires_formatter` | 2 | `format_observer_chip(self._observer)` |
| `test_record_streaming.py::test_stop_recording_uses_sidecar_when_streamed` | 4 | `_finalize_record_segment`、`write_sidecar`、`session_duration_s` |
| `test_replay_resume_wiring.py::test_resume_condition_is_backend_not_poll_timer` | 2 | `_active_sdr_backend is not None`、`_iq_poll_timer` |
| `test_replay_squelch_margin.py::test_initial_squelch_uses_authoritative_default` | 2 | `self._squelch_db: float = -80.0`、`DEFAULT_SQUELCH_DBFS` |
| `test_sky_projection_single_source.py::test_view_layer_delegates_projection_to_single_core` | 2 | `sky_to_screen_many(` |
| `test_sky_selection_cleanup.py::test_main_window_sets_observer` | 1 | `self.sky_view.set_observer(lat, lon)` |
| `test_planet_illumination_real_ephemeris.py::test_planets_module_delegates_phase_geometry` | 3 | `planet_phase_angle_from_geometry(`、`math.acos` |
| `test_shortcuts_guard.py::test_spectrum_scoped_ids_are_owned_by_spectrum_widget` | 1 | `_bind_shortcut(` |
| `test_shortcuts_guard.py::test_top_bar_tab_order_covers_all_interactive_controls` | 2 | `setTabOrder` |
| `test_sky_label_collision.py::test_flush_labels_uses_tier_cap_before_collision` | 1 | `label_tier_keep(` |
| `test_settings_semantics.py::test_two_bandwidths_are_distinguished` | 1 | `form.addRow("硬件带宽", self.bw_spin)` |
| `test_settings_schema.py::test_settings_panel_shows_demod_migration_reason` | 3 | `"FM"`、`"NBEM"`、`demod_mode` |
| `test_settings_schema.py::test_record_dir_panel_row_and_buttons` | 2 | `"选择目录…"`、`"打开目录"` |
| `test_status_panel_squelch_units.py::test_update_from_backend_uses_new_formatter` | 1 | `_fmt_squelch(` |
| `test_sky_readouts.py::test_scrollbar_drawn_only_when_content_overflows` | 3 | `if max_scroll > 0.0:`、`thumb_y`、`track_x` |
| `test_hardcoded_parameter_registry.py::test_control_panel_uses_kernel_squelch_constants` | 3 | `setRange(-120, 0)` 等控件回读可验证的项 |

### 保留 + 理由（代表）

- **`test_copy_units_audit.py`（97 处）**：审计「同一单位在多端只许一种写法」（Msps→MS/s、dBm 禁用、dBFS 正确），跨 `settings_panel.py`/`status_format.h`/`control_panel.py`/JS，行为断言无法覆盖 C++/JS 文案 → 保留。**但其中约 6 处钉的是代码表达式**（`status.get("rssi", 0)`、`format_sample_rate(_rate)` 等），所属线改文案断言时应顺手换行为断言（未计入 V1 的 73）。
- **`test_sky_readouts.py`（46 处）**：8 个函数里 V1 只占 3 处（滚动条几何）；其余是单位标签（`Magnitude (mag)`、`Rise/Set (UTC)`）与「只经单一格式化函数」的调用核查 → 文案部分 V3 保留；`test_no_ad_hoc_numeric_formatting_left` 的 `":4.0f} deg"`/`":.1f}°"` 属负向守卫（V2）保留，但若能直接断言 `format_angle_deg()` 输出，优先改（V5 变体）。
- **`test_hardcoded_parameter_registry.py`（12 处）**：V4 保留（行为断言读不到 C++ `tokens::kPeakRelDb`）；仅 `setRange(-120, 0)` 一行改 V1。
- **负向守卫（V2，18 处）**：`test_gui_config_no_longer_writes_*`、`test_no_nonexistent_freq_spin_widget`、`test_static_guard_no_bare_dbfs_on_margin`、`test_picked_info_has_no_static_double_ring`、`test_no_builtin_callsign_in_sources`、`test_no_ad_hoc_numeric_formatting_left` —— 「不许出现」只能扫源码，保留。
- **文档/数据 pin（V5，4 处）**：`test_decode_pipeline_e2e`（fixture 清单）、`test_decoder_capability_matrix`（`docs/` 表格）、`test_record_replay_config`/`test_record_rate_split`（sidecar JSON）、`test_web_server`（首页 HTML）——不是源码 pin，单列。

### 复用

```powershell
python scripts/audit_source_text_pins.py                 # 文本摘要
python scripts/audit_source_text_pins.py --json          # 机器可读
python scripts/audit_source_text_pins.py --report docs/source-text-pins-audit.md
```

判定（本段）不会被重生成覆盖：脚本只刷新 `manual` 段之外的统计与逐条清单。
局限：只扫 `tests/*.py`；动态拼装路径（f-string 拼文件名、子进程读源码）会漏检；
`unknown` 类别需人读上下文后才能归类。
<!-- manual:end -->

## 统计

- 扫描测试文件：459
- 命中：263 处 / 33 个文件
- 类别：code 201、doc_or_data 4、unknown 58

## 按文件

| 测试文件 | 处数 | 测试函数数 | 目标文件（Top3） |
|---|---:|---:|---|
| `tests/test_copy_units_audit.py` | 97 | 29 | `settings_panel.py`, `status_format.h`, `control_panel.py` |
| `tests/test_sky_readouts.py` | 46 | 8 | （派生变量） |
| `tests/test_settings_convergence.py` | 29 | 12 | `main_window.py`, `settings_panel.py` |
| `tests/test_settings_schema.py` | 14 | 7 | `exp.json` |
| `tests/test_hardcoded_parameter_registry.py` | 12 | 5 | `control_panel.py` |
| `tests/test_discoverability_help.py` | 6 | 2 | （派生变量） |
| `tests/test_status_panel_squelch_units.py` | 6 | 2 | `status_panel.py` |
| `tests/test_ui_wiring_audit.py` | 6 | 2 | `main_window.py`, `module_panel.py`, `anr_panel.py` |
| `tests/test_decode_pipeline_e2e.py` | 4 | 2 | （派生变量） |
| `tests/test_record_streaming.py` | 4 | 1 | （派生变量） |
| `tests/test_planet_illumination_real_ephemeris.py` | 3 | 1 | （派生变量） |
| `tests/test_record_replay_config.py` | 3 | 2 | `.json` |
| `tests/test_shortcuts_guard.py` | 3 | 2 | （派生变量） |
| `tests/test_ai_tool_selection_semantics.py` | 2 | 2 | （派生变量） |
| `tests/test_audio_route_anr.py` | 2 | 1 | `main_window.py`, `receive_pipeline.py` |
| `tests/test_command_palette.py` | 2 | 1 | `main_window.py` |
| `tests/test_desktop_constants.py` | 2 | 1 | （派生变量） |
| `tests/test_info_card_hud.py` | 2 | 1 | （派生变量） |
| `tests/test_replay_resume_wiring.py` | 2 | 1 | （派生变量） |
| `tests/test_replay_squelch_margin.py` | 2 | 1 | （派生变量） |
| `tests/test_selection_crosshair.py` | 2 | 1 | （派生变量） |
| `tests/test_sky_projection_single_source.py` | 2 | 1 | （派生变量） |
| `tests/test_tooltip_guard.py` | 2 | 1 | `dock_layout_manager.py`, `main_window.py` |
| `tests/test_ai_panel_behavior.py` | 1 | 1 | （派生变量） |
| `tests/test_config_key_migration.py` | 1 | 1 | （派生变量） |
| `tests/test_decoder_capability_matrix.py` | 1 | 1 | （派生变量） |
| `tests/test_record_rate_split.py` | 1 | 1 | `.iq.json` |
| `tests/test_settings_semantics.py` | 1 | 1 | `module_panel.py` |
| `tests/test_settings_ui_coverage.py` | 1 | 1 | （派生变量） |
| `tests/test_sky_label_collision.py` | 1 | 1 | （派生变量） |
| `tests/test_sky_selection_cleanup.py` | 1 | 1 | （派生变量） |
| `tests/test_ssdv_e2e.py` | 1 | 1 | （派生变量） |
| `tests/test_web_server.py` | 1 | 1 | （派生变量） |

## 逐条候选

| # | 位置 | 测试函数 | 类别 | 目标 | 钉的片段 |
|---:|---|---|---|---|---|
| 1 | `tests/test_ai_panel_behavior.py:537` | `test_config_roundtrip_and_legacy_key_migration` | unknown | `-` | `"api_base"` |
| 2 | `tests/test_ai_tool_selection_semantics.py:138` | `test_all_rule_choices_are_registered` | unknown | `-` | `choice` |
| 3 | `tests/test_ai_tool_selection_semantics.py:189` | `test_replay_table` | unknown | `-` | `"sdr_spectrum_peaks"` |
| 4 | `tests/test_audio_route_anr.py:62` | `test_anr_tap_wired_in_main_path` | code | `receive_pipeline.py` | `src.count("self.audio_tap(audio)")` |
| 5 | `tests/test_audio_route_anr.py:66` | `test_anr_tap_wired_in_main_path` | code | `main_window.py` | `"pipe._demod1.audio_tap = self._tap_demod_audio"` |
| 6 | `tests/test_command_palette.py:185` | `test_main_window_exposes_locator_and_registered_shortcut` | code | `main_window.py` | `'_key("help.command_palette")'` |
| 7 | `tests/test_command_palette.py:186` | `test_main_window_exposes_locator_and_registered_shortcut` | code | `main_window.py` | `'_show_command_palette'` |
| 8 | `tests/test_config_key_migration.py:73` | `test_read_migrates_file_once` | unknown | `-` | `"api_base"` |
| 9 | `tests/test_copy_units_audit.py:33` | `test_u03_sample_rate_is_msps_three_decimals` | code | `control_panel.py` | `"format_sample_rate(_rate)"` |
| 10 | `tests/test_copy_units_audit.py:34` | `test_u03_sample_rate_is_msps_three_decimals` | code | `main_window.py` | `"format_sample_rate"` |
| 11 | `tests/test_copy_units_audit.py:54` | `test_u02_rssi_is_dbfs_and_missing_is_dash_not_zero` | code | `main_window.py` | `'status.get("rssi", 0)'` |
| 12 | `tests/test_copy_units_audit.py:56` | `test_u02_rssi_is_dbfs_and_missing_is_dash_not_zero` | code | `status_panel.py` | `"rssi_value.setText(_fmt_rssi_mcp(rssi))"` |
| 13 | `tests/test_copy_units_audit.py:57` | `test_u02_rssi_is_dbfs_and_missing_is_dash_not_zero` | code | `main_window.py` | `'" dBm"'` |
| 14 | `tests/test_copy_units_audit.py:57` | `test_u02_rssi_is_dbfs_and_missing_is_dash_not_zero` | code | `main_window.py` | `'" dBm"'` |
| 15 | `tests/test_copy_units_audit.py:87` | `test_u02_same_screen_has_one_rssi_unit_and_never_dBm` | unknown | `-` | `"0-127 dBµV"` |
| 16 | `tests/test_copy_units_audit.py:87` | `test_u02_same_screen_has_one_rssi_unit_and_never_dBm` | unknown | `-` | `"0-127 dBuV"` |
| 17 | `tests/test_copy_units_audit.py:109` | `test_u02_cli_has_no_minus_one_fake_rssi_and_labels_dbuV` | code | `mbdsdr_mcp_client.py` | `'status.get("rssi", -1)'` |
| 18 | `tests/test_copy_units_audit.py:110` | `test_u02_cli_has_no_minus_one_fake_rssi_and_labels_dbuV` | code | `mbdsdr_mcp_client.py` | `'status.get("rssi")'` |
| 19 | `tests/test_copy_units_audit.py:111` | `test_u02_cli_has_no_minus_one_fake_rssi_and_labels_dbuV` | code | `mbdsdr_mcp_client.py` | `"dBµV"` |
| 20 | `tests/test_copy_units_audit.py:174` | `test_u02_cpp_status_strip_labels_rssi_and_snr_units` | code | `status_format.h` | `'QString("RSSI %1 dBFS")'` |
| 21 | `tests/test_copy_units_audit.py:175` | `test_u02_cpp_status_strip_labels_rssi_and_snr_units` | code | `status_format.h` | `'QString("SNR %1 dB")'` |
| 22 | `tests/test_copy_units_audit.py:180` | `test_u02_mobile_rssi_is_dbfs_with_one_decimal` | unknown | `-` | `"'RSSI ${rssi.toStringAsFixed(1)} dBFS'"` |
| 23 | `tests/test_copy_units_audit.py:181` | `test_u02_mobile_rssi_is_dbfs_with_one_decimal` | unknown | `-` | `"toStringAsFixed(0)} dB'"` |
| 24 | `tests/test_copy_units_audit.py:182` | `test_u02_mobile_rssi_is_dbfs_with_one_decimal` | unknown | `-` | `"'RSSI —'"` |
| 25 | `tests/test_copy_units_audit.py:183` | `test_u02_mobile_rssi_is_dbfs_with_one_decimal` | unknown | `-` | `"'RSSI --'"` |
| 26 | `tests/test_copy_units_audit.py:188` | `test_u03_mobile_sample_rate_is_ms_per_s_three_decimals` | unknown | `-` | `"toStringAsFixed(3)} MS/s"` |
| 27 | `tests/test_copy_units_audit.py:189` | `test_u03_mobile_sample_rate_is_ms_per_s_three_decimals` | unknown | `-` | `"toStringAsFixed(2)}Msps"` |
| 28 | `tests/test_copy_units_audit.py:190` | `test_u03_mobile_sample_rate_is_ms_per_s_three_decimals` | unknown | `-` | `"toStringAsFixed(2)} Msps"` |
| 29 | `tests/test_copy_units_audit.py:191` | `test_u03_mobile_sample_rate_is_ms_per_s_three_decimals` | unknown | `-` | `"toStringAsFixed(3)} MS/s"` |
| 30 | `tests/test_copy_units_audit.py:207` | `test_e06_ntrip_errors_name_next_step_without_internal_jargon` | code | `ntrip_panel.py` | `'err = "sourcetable 中未找到 STR 挂载点记录。"'` |
| 31 | `tests/test_copy_units_audit.py:208` | `test_e06_ntrip_errors_name_next_step_without_internal_jargon` | code | `ntrip_panel.py` | `"请核对地址与端口"` |
| 32 | `tests/test_copy_units_audit.py:209` | `test_e06_ntrip_errors_name_next_step_without_internal_jargon` | code | `ntrip_panel.py` | `'err = f"未知错误: {_clean_exc(e)}"'` |
| 33 | `tests/test_copy_units_audit.py:213` | `test_e03_import_rejection_names_reason_and_next_step` | code | `settings_panel.py` | `'res.error or "未知错误"'` |
| 34 | `tests/test_copy_units_audit.py:214` | `test_e03_import_rejection_names_reason_and_next_step` | code | `settings_panel.py` | `"导入被拒绝："` |
| 35 | `tests/test_copy_units_audit.py:218` | `test_t02_help_uses_real_topbar_demod_labels` | code | `help_panel.py` | `"顶栏「模式」"` |
| 36 | `tests/test_copy_units_audit.py:219` | `test_t02_help_uses_real_topbar_demod_labels` | code | `help_panel.py` | `"FM / WFM / NFM / AM / USB / LSB / CW"` |
| 37 | `tests/test_copy_units_audit.py:223` | `test_t01_help_separates_sky_tab_from_planetarium` | code | `help_panel.py` | `"打开天空模式"` |
| 38 | `tests/test_copy_units_audit.py:224` | `test_t01_help_separates_sky_tab_from_planetarium` | code | `help_panel.py` | `"星象仪模式"` |
| 39 | `tests/test_copy_units_audit.py:225` | `test_t01_help_separates_sky_tab_from_planetarium` | code | `help_panel.py` | `"天空模式"` |
| 40 | `tests/test_copy_units_audit.py:229` | `test_t04_filter_control_is_not_called_vfo_bandwidth` | code | `control_panel.py` | `'self.vfo_bw_label = QLabel("VFO 带宽")'` |
| 41 | `tests/test_copy_units_audit.py:230` | `test_t04_filter_control_is_not_called_vfo_bandwidth` | code | `control_panel.py` | `'self.vfo_bw_label = QLabel("解调带宽")'` |
| 42 | `tests/test_copy_units_audit.py:234` | `test_u06_mobile_help_squelch_and_vfo_terms_are_correct` | unknown | `-` | `"-100 为关闭"` |
| 43 | `tests/test_copy_units_audit.py:235` | `test_u06_mobile_help_squelch_and_vfo_terms_are_correct` | unknown | `-` | `"绝对电平"` |
| 44 | `tests/test_copy_units_audit.py:236` | `test_u06_mobile_help_squelch_and_vfo_terms_are_correct` | unknown | `-` | `"预存的频率+模式预设"` |
| 45 | `tests/test_copy_units_audit.py:240` | `test_n01_cpp_status_freq_six_decimals` | code | `status_format.h` | `'QString("%1 MHz").arg(hz / 1e6, 0, \'f\', 6)'` |
| 46 | `tests/test_copy_units_audit.py:244` | `test_n01_mobile_current_freq_six_decimals` | unknown | `-` | `"toStringAsFixed(6)} MHz"` |
| 47 | `tests/test_copy_units_audit.py:245` | `test_n01_mobile_current_freq_six_decimals` | unknown | `-` | `"toStringAsFixed(6)} MHz"` |
| 48 | `tests/test_copy_units_audit.py:249` | `test_t06_squelch_states_are_chinese_and_single_vocabulary` | code | `status_format.h` | `'QStringLiteral("静噪关闭")'` |
| 49 | `tests/test_copy_units_audit.py:250` | `test_t06_squelch_states_are_chinese_and_single_vocabulary` | code | `status_format.h` | `'QStringLiteral("静噪 有信号")'` |
| 50 | `tests/test_copy_units_audit.py:251` | `test_t06_squelch_states_are_chinese_and_single_vocabulary` | code | `status_format.h` | `'QStringLiteral("静噪 静音")'` |
| 51 | `tests/test_copy_units_audit.py:252` | `test_t06_squelch_states_are_chinese_and_single_vocabulary` | unknown | `-` | `"SQ OPEN"` |
| 52 | `tests/test_copy_units_audit.py:253` | `test_t06_squelch_states_are_chinese_and_single_vocabulary` | unknown | `-` | `"'静噪 静音'"` |
| 53 | `tests/test_copy_units_audit.py:266` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | code | `control_panel.py` | `'"2.4kHz (SSB)"'` |
| 54 | `tests/test_copy_units_audit.py:267` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | code | `control_panel.py` | `'"2.4 kHz (SSB)"'` |
| 55 | `tests/test_copy_units_audit.py:268` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | code | `control_panel.py` | `'QLabel("0dB")'` |
| 56 | `tests/test_copy_units_audit.py:269` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | code | `control_panel.py` | `'QLabel("0 dB")'` |
| 57 | `tests/test_copy_units_audit.py:270` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | unknown | `-` | `"G ${controller.gainDb.toStringAsFixed(1)}dB"` |
| 58 | `tests/test_copy_units_audit.py:271` | `test_u07_gain_unit_has_a_space_but_combo_stays_layout_locked` | unknown | `-` | `"增益 ${controller.gainDb.toStringAsFixed(1)} dB"` |
| 59 | `tests/test_copy_units_audit.py:275` | `test_e04_bookmark_failure_says_what_survives_and_what_to_do` | code | `bookmark_panel.py` | `"书签初始化失败"` |
| 60 | `tests/test_copy_units_audit.py:276` | `test_e04_bookmark_failure_says_what_survives_and_what_to_do` | code | `bookmark_panel.py` | `"书签加载失败："` |
| 61 | `tests/test_copy_units_audit.py:277` | `test_e04_bookmark_failure_says_what_survives_and_what_to_do` | code | `bookmark_panel.py` | `"已有书签未被改动"` |
| 62 | `tests/test_copy_units_audit.py:278` | `test_e04_bookmark_failure_says_what_survives_and_what_to_do` | code | `bookmark_panel.py` | `"检查该目录是否可读写"` |
| 63 | `tests/test_copy_units_audit.py:283` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"服务开关与端口见「服务设置」页签。"` |
| 64 | `tests/test_copy_units_audit.py:284` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"GQRX/hamlib"` |
| 65 | `tests/test_copy_units_audit.py:284` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"远程控制 7356"` |
| 66 | `tests/test_copy_units_audit.py:285` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"HTTP/WS"` |
| 67 | `tests/test_copy_units_audit.py:285` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"Web 8080"` |
| 68 | `tests/test_copy_units_audit.py:286` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"重启远程控制服务"` |
| 69 | `tests/test_copy_units_audit.py:287` | `test_discoverability_ports_are_findable_from_settings_tab` | code | `settings_panel.py` | `"重启 Web 服务"` |
| 70 | `tests/test_copy_units_audit.py:321` | `test_u03_sample_rate_copy_never_uses_mhz_or_msps` | code | `status_format.h` | `"Msps"` |
| 71 | `tests/test_copy_units_audit.py:331` | `test_u03_same_value_renders_identically_on_three_fronts` | code | `status_format.h` | `"QString(\"%1 MS/s\").arg(hz / 1e6, 0, 'f', 3)"` |
| 72 | `tests/test_copy_units_audit.py:332` | `test_u03_same_value_renders_identically_on_three_fronts` | unknown | `-` | `"toStringAsFixed(3)} MS/s"` |
| 73 | `tests/test_copy_units_audit.py:337` | `test_u03_cpp_spectrum_info_strip_labels_fs_as_ms_per_s` | code | `spectrum_widget.cpp` | `'QString("%1 Fs=%2 MS/s F0=%3 MHz N=%4")'` |
| 74 | `tests/test_copy_units_audit.py:338` | `test_u03_cpp_spectrum_info_strip_labels_fs_as_ms_per_s` | code | `spectrum_widget.cpp` | `"Fs=%2 MHz"` |
| 75 | `tests/test_copy_units_audit.py:339` | `test_u03_cpp_spectrum_info_strip_labels_fs_as_ms_per_s` | code | `spectrum_widget.cpp` | `".arg(frame.sampleRateHz / 1e6, 0, 'f', 3)"` |
| 76 | `tests/test_copy_units_audit.py:344` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `orchestrator.py` | `"设置采样率 {sample_rate/1e6:.3f} MS/s"` |
| 77 | `tests/test_copy_units_audit.py:345` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `orchestrator.py` | `"MSPS"` |
| 78 | `tests/test_copy_units_audit.py:346` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `sdr_tools.py` | `'采样率 2.048 MS/s（ oversample ~16x）'` |
| 79 | `tests/test_copy_units_audit.py:347` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `sdr_tools.py` | `'采样率 2.5 MS/s'` |
| 80 | `tests/test_copy_units_audit.py:348` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `sdr_tools.py` | `"MSPS"` |
| 81 | `tests/test_copy_units_audit.py:349` | `test_u03_orchestration_and_advice_copy_use_ms_per_s` | code | `weather_panel.py` | `'"采样率 (MS/s)"'` |
| 82 | `tests/test_copy_units_audit.py:374` | `test_u02_apt_stats_missing_values_render_dash` | code | `satellite_image_panel.py` | `"行数: {'—' if lines is None else lines}"` |
| 83 | `tests/test_copy_units_audit.py:375` | `test_u02_apt_stats_missing_values_render_dash` | code | `satellite_image_panel.py` | `"同步: {'—' if lock is None else f'{lock:.0%}'}"` |
| 84 | `tests/test_copy_units_audit.py:382` | `test_u02_satellite_form_prefill_uses_empty_placeholder` | code | `weather_panel.py` | `"get('freq_mhz', 0)"` |
| 85 | `tests/test_copy_units_audit.py:383` | `test_u02_satellite_form_prefill_uses_empty_placeholder` | code | `weather_panel.py` | `"get('symrate_ksps', 0)"` |
| 86 | `tests/test_copy_units_audit.py:390` | `test_u03_troubleshooting_error_hint_uses_ms_per_s` | code | `troubleshooting.py` | `"如 2.048 MHz"` |
| 87 | `tests/test_copy_units_audit.py:391` | `test_u03_troubleshooting_error_hint_uses_ms_per_s` | code | `troubleshooting.py` | `"如 2.048 MS/s"` |
| 88 | `tests/test_copy_units_audit.py:410` | `test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz` | code | `adsb.py` | `"fs ≥ 2 MS/s"` |
| 89 | `tests/test_copy_units_audit.py:411` | `test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz` | code | `decode_router.py` | `"fs ≥ 2 MS/s"` |
| 90 | `tests/test_copy_units_audit.py:412` | `test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz` | code | `decode_pipeline.py` | `"fs ≥ 2 MS/s"` |
| 91 | `tests/test_copy_units_audit.py:414` | `test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz` | code | `decode_router.py` | `"1090MHz"` |
| 92 | `tests/test_copy_units_audit.py:423` | `test_u03_troubleshooting_next_steps_use_ms_per_s_for_adsb_fs` | code | `troubleshooting.py` | `"ADS-B 保持 1090MHz 与 fs=2 MS/s 或 4 MS/s。"` |
| 93 | `tests/test_copy_units_audit.py:424` | `test_u03_troubleshooting_next_steps_use_ms_per_s_for_adsb_fs` | code | `troubleshooting.py` | `"（ADS-B 需 fs=2 MS/s 或 4 MS/s）"` |
| 94 | `tests/test_copy_units_audit.py:433` | `test_u02_gnss_psd_fields_never_labeled_dbm` | code | `gnss_monitor.py` | `"**不是 dBm**"` |
| 95 | `tests/test_copy_units_audit.py:434` | `test_u02_gnss_psd_fields_never_labeled_dbm` | code | `gnss_monitor.py` | `"平均功率（相对读数 dB，非 dBm）"` |
| 96 | `tests/test_copy_units_audit.py:435` | `test_u02_gnss_psd_fields_never_labeled_dbm` | code | `sdr_tools.py` | `"平均功率: {result.power_dbm:.1f} dB（相对读数，非 dBm）"` |
| 97 | `tests/test_copy_units_audit.py:436` | `test_u02_gnss_psd_fields_never_labeled_dbm` | code | `sdr_tools.py` | `"峰值RSSI: {result['peak_rssi_dbm']}（相对读数 dB，非 dBm）"` |
| 98 | `tests/test_copy_units_audit.py:320` | `test_u03_sample_rate_copy_never_uses_mhz_or_msps` | code | `-` | `"MSps"` |
| 99 | `tests/test_copy_units_audit.py:320` | `test_u03_sample_rate_copy_never_uses_mhz_or_msps` | code | `-` | `"Msps"` |
| 100 | `tests/test_copy_units_audit.py:370` | `test_u02_apt_stats_missing_values_render_dash` | code | `-` | `"get('lines_aligned',0)"` |
| 101 | `tests/test_copy_units_audit.py:371` | `test_u02_apt_stats_missing_values_render_dash` | code | `-` | `"get('lock_ratio',0)"` |
| 102 | `tests/test_copy_units_audit.py:372` | `test_u02_apt_stats_missing_values_render_dash` | code | `-` | `'get("lock_ratio", 0.0)'` |
| 103 | `tests/test_copy_units_audit.py:373` | `test_u02_apt_stats_missing_values_render_dash` | code | `-` | `'get("lines_aligned", 0)'` |
| 104 | `tests/test_copy_units_audit.py:422` | `test_u03_troubleshooting_next_steps_use_ms_per_s_for_adsb_fs` | code | `troubleshooting.py` | `bad` |
| 105 | `tests/test_copy_units_audit.py:402` | `test_u03_decoder_rule_copy_uses_ms_per_s_not_mhz` | code | `-` | `bad` |
| 106 | `tests/test_decode_pipeline_e2e.py:88` | `test_fixtures_are_in_repo_and_manifest_is_honest` | code | `-` | `"not ota"` |
| 107 | `tests/test_decode_pipeline_e2e.py:88` | `test_fixtures_are_in_repo_and_manifest_is_honest` | code | `-` | `"synthetic"` |
| 108 | `tests/test_decode_pipeline_e2e.py:532` | `test_summary_declares_synthetic_not_ota_and_drop_semantics` | unknown | `-` | `"not ota"` |
| 109 | `tests/test_decode_pipeline_e2e.py:532` | `test_summary_declares_synthetic_not_ota_and_drop_semantics` | unknown | `-` | `"synthetic"` |
| 110 | `tests/test_decoder_capability_matrix.py:67` | `test_doc_has_row_per_mode` | unknown | `-` | `f"`{row['mode']}`"` |
| 111 | `tests/test_desktop_constants.py:183` | `test_dock_layout_hardcoded_ids_are_registered` | code | `-` | `"tabText"` |
| 112 | `tests/test_desktop_constants.py:183` | `test_dock_layout_hardcoded_ids_are_registered` | code | `-` | `"tabData"` |
| 113 | `tests/test_discoverability_help.py:180` | `test_static_capabilities_symbols_exist` | code | `-` | `"_tx_block_reason"` |
| 114 | `tests/test_discoverability_help.py:180` | `test_static_capabilities_symbols_exist` | code | `-` | `"self.tx_button"` |
| 115 | `tests/test_discoverability_help.py:293` | `test_help_menu_is_wired_in_main_window` | code | `-` | `"_show_help_panel(1)"` |
| 116 | `tests/test_discoverability_help.py:293` | `test_help_menu_is_wired_in_main_window` | code | `-` | `"_show_help_panel(0)"` |
| 117 | `tests/test_discoverability_help.py:294` | `test_help_menu_is_wired_in_main_window` | code | `-` | `"_show_shortcuts_panel"` |
| 118 | `tests/test_discoverability_help.py:292` | `test_help_menu_is_wired_in_main_window` | code | `-` | `f'QAction("{label}"'` |
| 119 | `tests/test_hardcoded_parameter_registry.py:163` | `test_control_panel_uses_kernel_squelch_constants` | code | `control_panel.py` | `"SQUELCH_MAX_DBFS"` |
| 120 | `tests/test_hardcoded_parameter_registry.py:163` | `test_control_panel_uses_kernel_squelch_constants` | code | `control_panel.py` | `"SQUELCH_MIN_DBFS"` |
| 121 | `tests/test_hardcoded_parameter_registry.py:164` | `test_control_panel_uses_kernel_squelch_constants` | code | `control_panel.py` | `"DEFAULT_SQUELCH_DBFS"` |
| 122 | `tests/test_hardcoded_parameter_registry.py:261` | `test_tuning_step_tiers_match_cpp_and_schema` | unknown | `-` | `"kFreqCoarseStepHz"` |
| 123 | `tests/test_hardcoded_parameter_registry.py:262` | `test_tuning_step_tiers_match_cpp_and_schema` | unknown | `-` | `"kFreqFineStepHz"` |
| 124 | `tests/test_hardcoded_parameter_registry.py:446` | `test_display_gate_and_calibration_gate_are_locked_apart` | unknown | `-` | `"tokens::kPeakRelDb"` |
| 125 | `tests/test_hardcoded_parameter_registry.py:448` | `test_display_gate_and_calibration_gate_are_locked_apart` | unknown | `-` | `"tokens::kPeakRelDb"` |
| 126 | `tests/test_hardcoded_parameter_registry.py:450` | `test_display_gate_and_calibration_gate_are_locked_apart` | unknown | `-` | `"tokens::kPeakThresholdDefault"` |
| 127 | `tests/test_hardcoded_parameter_registry.py:452` | `test_display_gate_and_calibration_gate_are_locked_apart` | unknown | `-` | `"tokens::kPeakThresholdDefault"` |
| 128 | `tests/test_hardcoded_parameter_registry.py:454` | `test_display_gate_and_calibration_gate_are_locked_apart` | unknown | `-` | `"tokens::kPeakRelDb"` |
| 129 | `tests/test_hardcoded_parameter_registry.py:234` | `test_cpp_tokens_match_documented_values` | unknown | `-` | `name` |
| 130 | `tests/test_hardcoded_parameter_registry.py:390` | `test_cross_language_deltas_are_documented` | code | `-` | `semantic` |
| 131 | `tests/test_info_card_hud.py:218` | `test_overlay_wires_formatter` | code | `-` | `"format_observer_chip(self._observer)"` |
| 132 | `tests/test_info_card_hud.py:220` | `test_overlay_wires_formatter` | code | `-` | `"观测站"` |
| 133 | `tests/test_planet_illumination_real_ephemeris.py:249` | `test_planets_module_delegates_phase_geometry` | code | `-` | `"planet_phase_angle_from_geometry("` |
| 134 | `tests/test_planet_illumination_real_ephemeris.py:251` | `test_planets_module_delegates_phase_geometry` | code | `-` | `"r_e * r_e"` |
| 135 | `tests/test_planet_illumination_real_ephemeris.py:252` | `test_planets_module_delegates_phase_geometry` | code | `-` | `"math.acos"` |
| 136 | `tests/test_record_rate_split.py:146` | `test_single_segment_keeps_plain_sidecar` | doc_or_data | `.iq.json` | `"segments"` |
| 137 | `tests/test_record_replay_config.py:148` | `test_replay_stops_recording_and_saves_sidecar` | doc_or_data | `.json` | `"center_freq_hz"` |
| 138 | `tests/test_record_replay_config.py:148` | `test_replay_stops_recording_and_saves_sidecar` | doc_or_data | `.json` | `"sample_rate"` |
| 139 | `tests/test_record_replay_config.py:204` | `test_corrupt_config_backed_up_and_original_bytes_kept` | unknown | `-` | `"window"` |
| 140 | `tests/test_record_streaming.py:109` | `test_stop_recording_uses_sidecar_when_streamed` | code | `-` | `"_finalize_record_segment"` |
| 141 | `tests/test_record_streaming.py:113` | `test_stop_recording_uses_sidecar_when_streamed` | code | `-` | `"write_sidecar"` |
| 142 | `tests/test_record_streaming.py:116` | `test_stop_recording_uses_sidecar_when_streamed` | code | `-` | `"_write_session_sidecars"` |
| 143 | `tests/test_record_streaming.py:117` | `test_stop_recording_uses_sidecar_when_streamed` | code | `-` | `"session_duration_s"` |
| 144 | `tests/test_replay_resume_wiring.py:57` | `test_resume_condition_is_backend_not_poll_timer` | unknown | `-` | `"_active_sdr_backend is not None"` |
| 145 | `tests/test_replay_resume_wiring.py:58` | `test_resume_condition_is_backend_not_poll_timer` | unknown | `-` | `"_iq_poll_timer"` |
| 146 | `tests/test_replay_squelch_margin.py:107` | `test_initial_squelch_uses_authoritative_default` | code | `-` | `"self._squelch_db: float = -80.0"` |
| 147 | `tests/test_replay_squelch_margin.py:109` | `test_initial_squelch_uses_authoritative_default` | code | `-` | `"DEFAULT_SQUELCH_DBFS"` |
| 148 | `tests/test_selection_crosshair.py:160` | `test_picked_info_has_no_static_double_ring` | code | `-` | `"drawEllipse(pos, 12, 12)"` |
| 149 | `tests/test_selection_crosshair.py:162` | `test_picked_info_has_no_static_double_ring` | code | `-` | `"drawEllipse(pos, 16, 16)"` |
| 150 | `tests/test_settings_convergence.py:117` | `test_gui_config_no_longer_writes_canonical_keys` | unknown | `-` | `'"theme"'` |
| 151 | `tests/test_settings_convergence.py:124` | `test_gui_config_legacy_values_migrate_into_settings` | unknown | `-` | `'settings.set("gnss_port"'` |
| 152 | `tests/test_settings_convergence.py:125` | `test_gui_config_legacy_values_migrate_into_settings` | unknown | `-` | `'settings.set("gnss_baudrate"'` |
| 153 | `tests/test_settings_convergence.py:126` | `test_gui_config_legacy_values_migrate_into_settings` | unknown | `-` | `'settings.set("theme"'` |
| 154 | `tests/test_settings_convergence.py:133` | `test_gnss_dialog_persists_through_settings` | unknown | `-` | `'settings.set("gnss_port"'` |
| 155 | `tests/test_settings_convergence.py:134` | `test_gnss_dialog_persists_through_settings` | unknown | `-` | `'settings.set("gnss_baudrate"'` |
| 156 | `tests/test_settings_convergence.py:206` | `test_touch_mode_ui_entry_exists` | code | `main_window.py` | `"触屏模式"` |
| 157 | `tests/test_settings_convergence.py:207` | `test_touch_mode_ui_entry_exists` | code | `main_window.py` | `"def _set_touch_mode"` |
| 158 | `tests/test_settings_convergence.py:208` | `test_touch_mode_ui_entry_exists` | code | `main_window.py` | `"_set_touch_mode(m)"` |
| 159 | `tests/test_settings_convergence.py:208` | `test_touch_mode_ui_entry_exists` | code | `main_window.py` | `'_set_touch_mode(m)'` |
| 160 | `tests/test_settings_convergence.py:241` | `test_drifting_keys_migrate_into_settings_once` | unknown | `-` | `'settings.set("frequency_hz"'` |
| 161 | `tests/test_settings_convergence.py:242` | `test_drifting_keys_migrate_into_settings_once` | unknown | `-` | `"* 1e6"` |
| 162 | `tests/test_settings_convergence.py:243` | `test_drifting_keys_migrate_into_settings_once` | unknown | `-` | `'settings.set("demod_mode"'` |
| 163 | `tests/test_settings_convergence.py:244` | `test_drifting_keys_migrate_into_settings_once` | unknown | `-` | `'settings.set("volume"'` |
| 164 | `tests/test_settings_convergence.py:245` | `test_drifting_keys_migrate_into_settings_once` | unknown | `-` | `'settings.set("show_waterfall"'` |
| 165 | `tests/test_settings_convergence.py:250` | `test_volume_and_waterfall_now_persist_through_settings` | code | `main_window.py` | `'settings.set("volume"'` |
| 166 | `tests/test_settings_convergence.py:251` | `test_volume_and_waterfall_now_persist_through_settings` | code | `main_window.py` | `'settings.set("show_waterfall"'` |
| 167 | `tests/test_settings_convergence.py:386` | `test_no_nonexistent_freq_spin_widget` | code | `main_window.py` | `"freq_spin.setValue"` |
| 168 | `tests/test_settings_convergence.py:416` | `test_volume_wire_conversion_isolated_at_boundary` | code | `main_window.py` | `"volume_pct_to_hw"` |
| 169 | `tests/test_settings_convergence.py:417` | `test_volume_wire_conversion_isolated_at_boundary` | code | `main_window.py` | `"volume_hw_to_pct"` |
| 170 | `tests/test_settings_convergence.py:418` | `test_volume_wire_conversion_isolated_at_boundary` | code | `main_window.py` | `"volume / 63.0"` |
| 171 | `tests/test_settings_convergence.py:424` | `test_gain_dynamic_range_wired_on_connect` | code | `main_window.py` | `"set_gain_range"` |
| 172 | `tests/test_settings_convergence.py:426` | `test_gain_dynamic_range_wired_on_connect` | code | `main_window.py` | `"max_gain"` |
| 173 | `tests/test_settings_convergence.py:113` | `test_gui_config_no_longer_writes_canonical_keys` | unknown | `-` | `f'config["{key}"]'` |
| 174 | `tests/test_settings_convergence.py:115` | `test_gui_config_no_longer_writes_canonical_keys` | unknown | `-` | `f'"{key}"'` |
| 175 | `tests/test_settings_convergence.py:215` | `test_panel_has_no_dead_slots` | code | `settings_panel.py` | `f"def {slot}"` |
| 176 | `tests/test_settings_convergence.py:216` | `test_panel_has_no_dead_slots` | code | `settings_panel.py` | `f"connect(self.{slot})"` |
| 177 | `tests/test_settings_convergence.py:234` | `test_gui_config_no_longer_writes_drifting_keys` | unknown | `-` | `f'config["{key}"]'` |
| 178 | `tests/test_settings_convergence.py:360` | `_mutated_sky_handler` | code | `-` | `src.count(guard)` |
| 179 | `tests/test_settings_schema.py:669` | `test_settings_panel_shows_demod_migration_reason` | code | `-` | `"FM"` |
| 180 | `tests/test_settings_schema.py:669` | `test_settings_panel_shows_demod_migration_reason` | code | `-` | `"NBEM"` |
| 181 | `tests/test_settings_schema.py:669` | `test_settings_panel_shows_demod_migration_reason` | code | `-` | `"demod_mode"` |
| 182 | `tests/test_settings_schema.py:811` | `test_export_has_version_and_no_secrets` | doc_or_data | `exp.json` | `"sk-secret"` |
| 183 | `tests/test_settings_schema.py:882` | `test_import_old_document_migrates_missing_fields` | unknown | `-` | `"已按默认补齐"` |
| 184 | `tests/test_settings_schema.py:906` | `test_import_same_major_newer_minor_accepts_and_lists_unknown` | unknown | `-` | `"较新"` |
| 185 | `tests/test_settings_schema.py:1229` | `test_squelch_migration_notice_is_visible_on_old_config` | code | `-` | `"静噪默认值迁移"` |
| 186 | `tests/test_settings_schema.py:1230` | `test_squelch_migration_notice_is_visible_on_old_config` | code | `-` | `"6 dB"` |
| 187 | `tests/test_settings_schema.py:1230` | `test_squelch_migration_notice_is_visible_on_old_config` | code | `-` | `"余量"` |
| 188 | `tests/test_settings_schema.py:1231` | `test_squelch_migration_notice_is_visible_on_old_config` | code | `-` | `"-80"` |
| 189 | `tests/test_settings_schema.py:1307` | `test_record_dir_panel_row_and_buttons` | code | `-` | `"选择目录…"` |
| 190 | `tests/test_settings_schema.py:1307` | `test_record_dir_panel_row_and_buttons` | code | `-` | `"打开目录"` |
| 191 | `tests/test_settings_schema.py:1324` | `test_panel_has_required_groups_and_all_rows` | unknown | `-` | `group` |
| 192 | `tests/test_settings_schema.py:1333` | `test_panel_has_required_groups_and_all_rows` | unknown | `-` | `SCHEMA[key].label` |
| 193 | `tests/test_settings_semantics.py:355` | `test_two_bandwidths_are_distinguished` | code | `module_panel.py` | `'form.addRow("硬件带宽", self.bw_spin)'` |
| 194 | `tests/test_settings_ui_coverage.py:260` | `test_inventory_table_matches_script` | unknown | `-` | `report.source_commit` |
| 195 | `tests/test_shortcuts_guard.py:103` | `test_spectrum_scoped_ids_are_owned_by_spectrum_widget` | code | `-` | `"_bind_shortcut("` |
| 196 | `tests/test_shortcuts_guard.py:186` | `test_top_bar_tab_order_covers_all_interactive_controls` | code | `-` | `"setTabOrder"` |
| 197 | `tests/test_shortcuts_guard.py:185` | `test_top_bar_tab_order_covers_all_interactive_controls` | code | `-` | `widget` |
| 198 | `tests/test_sky_label_collision.py:254` | `test_flush_labels_uses_tier_cap_before_collision` | code | `-` | `"label_tier_keep("` |
| 199 | `tests/test_sky_projection_single_source.py:129` | `test_view_layer_delegates_projection_to_single_core` | code | `-` | `"sky_to_screen_many("` |
| 200 | `tests/test_sky_projection_single_source.py:132` | `test_view_layer_delegates_projection_to_single_core` | code | `-` | `banned` |
| 201 | `tests/test_sky_readouts.py:254` | `test_card_rise_set_shown_in_utc` | code | `-` | `"Rise/Set (UTC)"` |
| 202 | `tests/test_sky_readouts.py:378` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `"未来过境"` |
| 203 | `tests/test_sky_readouts.py:380` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `"24h"` |
| 204 | `tests/test_sky_readouts.py:381` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `"_pass_window_hours"` |
| 205 | `tests/test_sky_readouts.py:382` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `"峰值(几何)"` |
| 206 | `tests/test_sky_readouts.py:383` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `"format_angle_deg("` |
| 207 | `tests/test_sky_readouts.py:384` | `test_pass_panel_labels_and_single_formatter` | code | `-` | `":4.0f} deg"` |
| 208 | `tests/test_sky_readouts.py:393` | `test_picked_satellite_card_units` | code | `-` | `"Magnitude (mag)"` |
| 209 | `tests/test_sky_readouts.py:394` | `test_picked_satellite_card_units` | code | `-` | `"Distance (km)"` |
| 210 | `tests/test_sky_readouts.py:395` | `test_picked_satellite_card_units` | code | `-` | `"Rise/Set (UTC)"` |
| 211 | `tests/test_sky_readouts.py:396` | `test_picked_satellite_card_units` | code | `-` | `"Az/Alt 几何 (Az 0°=N)"` |
| 212 | `tests/test_sky_readouts.py:397` | `test_picked_satellite_card_units` | code | `-` | `"format_azimuth("` |
| 213 | `tests/test_sky_readouts.py:398` | `test_picked_satellite_card_units` | code | `-` | `"format_angle_deg("` |
| 214 | `tests/test_sky_readouts.py:399` | `test_picked_satellite_card_units` | code | `-` | `"format_dms_deg("` |
| 215 | `tests/test_sky_readouts.py:399` | `test_picked_satellite_card_units` | code | `-` | `"format_hms_ra("` |
| 216 | `tests/test_sky_readouts.py:401` | `test_picked_satellite_card_units` | code | `-` | `'"°{m:02d}'` |
| 217 | `tests/test_sky_readouts.py:402` | `test_picked_satellite_card_units` | code | `-` | `'strftime("%H:%M")'` |
| 218 | `tests/test_sky_readouts.py:406` | `test_body_card_units` | code | `-` | `"Magnitude (mag)"` |
| 219 | `tests/test_sky_readouts.py:407` | `test_body_card_units` | code | `-` | `"Rise/Set (UTC)"` |
| 220 | `tests/test_sky_readouts.py:408` | `test_body_card_units` | code | `-` | `"Az/Alt (视/几何, Az 0°=N)"` |
| 221 | `tests/test_sky_readouts.py:409` | `test_body_card_units` | code | `-` | `"format_azimuth("` |
| 222 | `tests/test_sky_readouts.py:411` | `test_body_card_units` | code | `-` | `"_format_alt_pair("` |
| 223 | `tests/test_sky_readouts.py:412` | `test_body_card_units` | code | `-` | `"format_dms_deg("` |
| 224 | `tests/test_sky_readouts.py:412` | `test_body_card_units` | code | `-` | `"format_hms_ra("` |
| 225 | `tests/test_sky_readouts.py:416` | `test_status_card_states_utc_and_local_offset` | code | `-` | `"format_utc_time("` |
| 226 | `tests/test_sky_readouts.py:417` | `test_status_card_states_utc_and_local_offset` | code | `-` | `"format_local_time("` |
| 227 | `tests/test_sky_readouts.py:418` | `test_status_card_states_utc_and_local_offset` | code | `-` | `'"本地"'` |
| 228 | `tests/test_sky_readouts.py:419` | `test_status_card_states_utc_and_local_offset` | code | `-` | `"format_angle_deg("` |
| 229 | `tests/test_sky_readouts.py:419` | `test_status_card_states_utc_and_local_offset` | code | `-` | `"format_azimuth("` |
| 230 | `tests/test_sky_readouts.py:420` | `test_status_card_states_utc_and_local_offset` | code | `-` | `'deg"'` |
| 231 | `tests/test_sky_readouts.py:425` | `test_no_ad_hoc_numeric_formatting_left` | code | `-` | `src.count("strftime(")` |
| 232 | `tests/test_sky_readouts.py:426` | `test_no_ad_hoc_numeric_formatting_left` | code | `-` | `":4.0f} deg"` |
| 233 | `tests/test_sky_readouts.py:427` | `test_no_ad_hoc_numeric_formatting_left` | code | `-` | `":.1f}°"` |
| 234 | `tests/test_sky_readouts.py:429` | `test_no_ad_hoc_numeric_formatting_left` | code | `-` | `src.count(':3d}°{m:02d}')` |
| 235 | `tests/test_sky_readouts.py:430` | `test_no_ad_hoc_numeric_formatting_left` | code | `-` | `":.0f} km"` |
| 236 | `tests/test_sky_readouts.py:465` | `test_card_rows_use_single_fact_source` | code | `-` | `'"恒星时"'` |
| 237 | `tests/test_sky_readouts.py:466` | `test_card_rows_use_single_fact_source` | code | `-` | `"_current_jd("` |
| 238 | `tests/test_sky_readouts.py:466` | `test_card_rows_use_single_fact_source` | code | `-` | `"local_sidereal_time_deg("` |
| 239 | `tests/test_sky_readouts.py:467` | `test_card_rows_use_single_fact_source` | code | `-` | `"format_view_axis("` |
| 240 | `tests/test_sky_readouts.py:467` | `test_card_rows_use_single_fact_source` | code | `-` | `'"视轴(几何)"'` |
| 241 | `tests/test_sky_readouts.py:468` | `test_card_rows_use_single_fact_source` | code | `-` | `"star_catalog_credit_text("` |
| 242 | `tests/test_sky_readouts.py:468` | `test_card_rows_use_single_fact_source` | code | `-` | `'"星表来源"'` |
| 243 | `tests/test_sky_readouts.py:470` | `test_card_rows_use_single_fact_source` | code | `-` | `"—（无站址）"` |
| 244 | `tests/test_sky_readouts.py:567` | `test_scrollbar_drawn_only_when_content_overflows` | code | `-` | `"if max_scroll > 0.0:"` |
| 245 | `tests/test_sky_readouts.py:568` | `test_scrollbar_drawn_only_when_content_overflows` | code | `-` | `"thumb_y"` |
| 246 | `tests/test_sky_readouts.py:568` | `test_scrollbar_drawn_only_when_content_overflows` | code | `-` | `"track_x"` |
| 247 | `tests/test_sky_selection_cleanup.py:144` | `test_main_window_sets_observer` | code | `-` | `"self.sky_view.set_observer(lat, lon)"` |
| 248 | `tests/test_ssdv_e2e.py:452` | `test_no_builtin_callsign_in_sources` | code | `-` | `bad` |
| 249 | `tests/test_status_panel_squelch_units.py:65` | `test_static_guard_no_bare_dbfs_on_margin` | code | `status_panel.py` | `'f"{squelch:.0f} dBFS"'` |
| 250 | `tests/test_status_panel_squelch_units.py:67` | `test_static_guard_no_bare_dbfs_on_margin` | code | `status_panel.py` | `"_fmt_squelch("` |
| 251 | `tests/test_status_panel_squelch_units.py:68` | `test_static_guard_no_bare_dbfs_on_margin` | code | `status_panel.py` | `"squelch_applied_by"` |
| 252 | `tests/test_status_panel_squelch_units.py:69` | `test_static_guard_no_bare_dbfs_on_margin` | code | `status_panel.py` | `"squelch_threshold_dbfs"` |
| 253 | `tests/test_status_panel_squelch_units.py:84` | `test_static_guard_no_bare_dbfs_on_margin` | unknown | `-` | `"余量"` |
| 254 | `tests/test_status_panel_squelch_units.py:92` | `test_update_from_backend_uses_new_formatter` | code | `-` | `"_fmt_squelch("` |
| 255 | `tests/test_tooltip_guard.py:173` | `test_upgraded_tooltips_meet_the_rules` | code | `main_window.py` | `"失败时状态栏会显示真实错误原因"` |
| 256 | `tests/test_tooltip_guard.py:175` | `test_upgraded_tooltips_meet_the_rules` | code | `dock_layout_manager.py` | `"面板：点击显示或隐藏"` |
| 257 | `tests/test_ui_wiring_audit.py:367` | `test_module_bandwidth_now_reaches_backend` | code | `module_panel.py` | `'dev["_bandwidth"] = float(self.bw_spin.value())'` |
| 258 | `tests/test_ui_wiring_audit.py:369` | `test_module_bandwidth_now_reaches_backend` | code | `main_window.py` | `"bandwidth: float = 0.0"` |
| 259 | `tests/test_ui_wiring_audit.py:371` | `test_module_bandwidth_now_reaches_backend` | code | `main_window.py` | `"backend.set_bandwidth(float(bandwidth))"` |
| 260 | `tests/test_ui_wiring_audit.py:373` | `test_module_bandwidth_now_reaches_backend` | code | `main_window.py` | `'dev.get("_bandwidth"'` |
| 261 | `tests/test_ui_wiring_audit.py:386` | `test_anr_strength_applies_through_panel_internal_write` | code | `anr_panel.py` | `"self._anr.cfg.alpha = alpha"` |
| 262 | `tests/test_ui_wiring_audit.py:390` | `test_anr_strength_applies_through_panel_internal_write` | code | `-` | `"def _on_anr_strength"` |
| 263 | `tests/test_web_server.py:51` | `test_index_page_and_404` | code | `-` | `"MBDSDR"` |
