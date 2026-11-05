# AI 工具诚实性矩阵

> 生成命令：`python scripts/tool_honesty_scan.py --json D:\mbdsdr\.tool_honesty_run.json --md docs/tool-honesty-matrix.md`

判定口径（写死）：假成功 = `ok=true` 且返回不可测的具体数值；诚实 = `ok=false` + 可行动文案，或 `ok=true` 但不可测量为 null/—；静默 = `ok=true` 且空数据、无说明（不合格）。

## 汇总

| 环境 | 诚实 | 假成功 | 静默 | 未覆盖（不适用） | 总计 |
| --- | --- | --- | --- | --- | --- |
| no_device | 82 | 0 | 0 | 0 | 82 |
| offline | 2 | 0 | 0 | 80 | 82 |
| no_api_key | 0 | 0 | 0 | 82 | 82 |

### 未覆盖（J）及原因

- `no_device`：**J=0**，全部工具都在无设备环境下有区分力。
- `offline`：**J=80**，这些工具不访问外网（纯本地 DSP/文件/配置/解码），断网不会改变它们的正确行为。
- `no_api_key`：**J=82**，本 MCP 工具表**没有任何模型类工具**；模型调用在 agent/provider 层，不在此表，因此清空 API key 对 82 个工具均无区分力（这是覆盖边界，不是诚实性结论）。

### 修复前后对照（反例）

修复前实测（同一脚本、绕过 confirm_token 直接调 handler）：

- `set_frequency`/`set_demod`/`set_gain`/`set_sample_rate`/`set_bandwidth`/`set_squelch`/`set_volume`/`set_mute`/`set_anr_enabled`/`set_anr_strength`/`add_vfo`/`remove_vfo`/`stop_recording`/`stop_audio` 等设值工具在无设备时返回 `{"ok": true}`，无 `connected` / `applied` / `note` —— 调用方会误以为已下发硬件（静默，等同不合格）。
- `get_frequency` / `get_demod` 返回具体数值/模式，不声明 `connected=false`，无法区分「配置目标」与「硬件回读」。
- `start_scan` / `start_decoder` 在未连接时返回成功；`stop_scan` / `stop_decoder` 对不存在的句柄返回成功；`get_scan_results` / `get_decoder_messages` 空结果无说明。
- `start_audio` 失败只回 `{"ok": false}`，无可行动文案；`learn_noise_floor` 只回 `{"error": "not_connected"}`。

修复后：设值工具统一回执 `connected=false / applied=false / stored=true / note`；配置读取带 `source: configured-target`；扫描/解码器未连接与未知句柄一律 `ok=false`；`start_audio` / `learn_noise_floor` 失败带 error + suggestion。上表汇总即修复后实测。

## 明细

| 工具 | 域 | 环境 | code | ok | 编造值 | 判定 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `list_devices` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `connect` | 系统设备 | no_device | `DeviceNotFoundError` | false | 否 | 诚实 | 抛出用户可见错误 |
| `disconnect` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `is_connected` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `set_frequency` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `get_frequency` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_demod` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `get_demod` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_gain` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_sample_rate` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_bandwidth` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_squelch` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_deemphasis` | SDR 调谐/VFO | no_device | `SDRUserError` | false | 否 | 诚实 | 抛出用户可见错误 |
| `set_iq_invert` | SDR 调谐/VFO | no_device | `SDRUserError` | false | 否 | 诚实 | 抛出用户可见错误 |
| `list_audio_outputs` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `set_audio_output` | 系统设备 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `start_audio` | 系统设备 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `stop_audio` | 系统设备 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_volume` | 系统设备 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_mute` | 系统设备 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `read_spectrum` | SDR 调谐/VFO | no_device | `not_connected` | false | 否 | 诚实 |  |
| `read_iq` | 导出演制 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `read_audio` | 导出演制 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `export_spectrum_snapshot` | 导出演制 | no_device | `ok` | true | 否 | 诚实 |  |
| `export_peaks_csv` | 导出演制 | no_device | `ok` | true | 否 | 诚实 |  |
| `vna_analyze_sweep` | VNA 分析 | no_device | `ValueError` | false | 否 | 诚实 | 抛出用户可见错误 |
| `vna_solve_error_box` | VNA 校准 | no_device | `ok` | true | 否 | 诚实 |  |
| `vna_apply_calibration` | VNA 校准 | no_device | `ok` | true | 否 | 诚实 |  |
| `export_vna_report` | 导出演制 | no_device | `ok` | true | 否 | 诚实 |  |
| `start_analyze` | SDR 调谐/VFO | no_device | `not_connected` | false | 否 | 诚实 |  |
| `start_scan` | 书签扫描卫星 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `stop_scan` | 书签扫描卫星 | no_device | `scan_not_found` | false | 否 | 诚实 |  |
| `get_scan_results` | 书签扫描卫星 | no_device | `scan_not_found` | false | 否 | 诚实 |  |
| `start_decoder` | 亚音数字解码 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `stop_decoder` | 亚音数字解码 | no_device | `decoder_not_found` | false | 否 | 诚实 |  |
| `get_decoder_messages` | 亚音数字解码 | no_device | `decoder_not_found` | false | 否 | 诚实 |  |
| `list_satellites` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `get_satellite_pass` | 书签扫描卫星 | no_device | `station_untrusted` | false | 否 | 诚实 |  |
| `tune_satellite` | 书签扫描卫星 | no_device | `station_untrusted` | false | 否 | 诚实 |  |
| `set_ground_station` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `sat_link_list` | 卫星链路 | no_device | `ok` | true | 否 | 诚实 |  |
| `sat_link_get` | 卫星链路 | no_device | `SDRUserError` | false | 否 | 诚实 | 抛出用户可见错误 |
| `sat_link_identify` | 卫星链路 | no_device | `ok` | true | 否 | 诚实 |  |
| `sat_link_tuning` | 卫星链路 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `add_bookmark` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `list_bookmarks` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `delete_bookmark` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `find_nearest_bookmark` | 书签扫描卫星 | no_device | `ok` | true | 否 | 诚实 |  |
| `start_recording` | 导出演制 | no_device | `not_connected` | false | 否 | 诚实 |  |
| `stop_recording` | 导出演制 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `get_recording_status` | 导出演制 | no_device | `ok` | true | 否 | 诚实 |  |
| `start_remote_control` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `stop_remote_control` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `start_web_server` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `stop_web_server` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `get_service_status` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `set_anr_enabled` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_anr_strength` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `learn_noise_floor` | SDR 调谐/VFO | no_device | `not_connected` | false | 否 | 诚实 |  |
| `add_vfo` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `remove_vfo` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `set_primary_vfo` | SDR 调谐/VFO | no_device | `vfo_not_found` | false | 否 | 诚实 |  |
| `list_vfos` | SDR 调谐/VFO | no_device | `ok` | true | 否 | 诚实 |  |
| `get_status` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `shutdown` | 系统设备 | no_device | `ok` | true | 否 | 诚实 |  |
| `decoder_dab_fic_decode` | 解码器长尾 | no_device | `ok` | true | 否 | 诚实 |  |
| `decoder_nrsc5_frame_parse` | 解码器长尾 | no_device | `ok` | true | 否 | 诚实 |  |
| `decoder_rtl433_list` | 解码器长尾 | no_device | `ok` | true | 否 | 诚实 |  |
| `decoder_rtl433_decode` | 解码器长尾 | no_device | `unknown_device_type` | false | 否 | 诚实 |  |
| `decoder_gk2a_parse_headers` | 解码器长尾 | no_device | `ok` | true | 否 | 诚实 |  |
| `decoder_fst4_encode_text` | 解码器长尾 | no_device | `invalid_text` | false | 否 | 诚实 |  |
| `device_bladerf_params_query` | 解码器长尾 | no_device | `ok` | true | 否 | 诚实 |  |
| `aigui_component_catalog` | 工作台编排 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `aigui_validate_message` | 工作台编排 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `aigui_render_preview` | 工作台编排 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `ask_question` | 人机问答 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `answer_question` | 人机问答 | no_device | `未知 question_id：audit-q` | false | 否 | 诚实 |  |
| `question_status` | 人机问答 | no_device | `ok` | true | 否 | 诚实 | ok=true 且说明了不可测/未应用 |
| `mcp_client_list` | 外部工具 | no_device | `ok` | true | 否 | 诚实 |  |
| `mcp_client_connect` | 外部工具 | no_device | `unknown_server` | false | 否 | 诚实 |  |
| `mcp_client_disconnect` | 外部工具 | no_device | `unknown_server` | false | 否 | 诚实 |  |
| `mcp_client_call` | 外部工具 | no_device | `unknown_server` | false | 否 | 诚实 |  |
| `list_devices` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `connect` | 系统设备 | offline | `DeviceNotFoundError` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `disconnect` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `is_connected` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_frequency` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_frequency` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_demod` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_demod` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_gain` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_sample_rate` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_bandwidth` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_squelch` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_deemphasis` | SDR 调谐/VFO | offline | `SDRUserError` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_iq_invert` | SDR 调谐/VFO | offline | `SDRUserError` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `list_audio_outputs` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_audio_output` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_audio` | 系统设备 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_audio` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_volume` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_mute` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `read_spectrum` | SDR 调谐/VFO | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `read_iq` | 导出演制 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `read_audio` | 导出演制 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `export_spectrum_snapshot` | 导出演制 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `export_peaks_csv` | 导出演制 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `vna_analyze_sweep` | VNA 分析 | offline | `ValueError` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `vna_solve_error_box` | VNA 校准 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `vna_apply_calibration` | VNA 校准 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `export_vna_report` | 导出演制 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_analyze` | SDR 调谐/VFO | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_scan` | 书签扫描卫星 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_scan` | 书签扫描卫星 | offline | `scan_not_found` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_scan_results` | 书签扫描卫星 | offline | `scan_not_found` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_decoder` | 亚音数字解码 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_decoder` | 亚音数字解码 | offline | `decoder_not_found` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_decoder_messages` | 亚音数字解码 | offline | `decoder_not_found` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `list_satellites` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_satellite_pass` | 书签扫描卫星 | offline | `station_untrusted` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `tune_satellite` | 书签扫描卫星 | offline | `station_untrusted` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_ground_station` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `sat_link_list` | 卫星链路 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `sat_link_get` | 卫星链路 | offline | `SDRUserError` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `sat_link_identify` | 卫星链路 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `sat_link_tuning` | 卫星链路 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `add_bookmark` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `list_bookmarks` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `delete_bookmark` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `find_nearest_bookmark` | 书签扫描卫星 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_recording` | 导出演制 | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_recording` | 导出演制 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_recording_status` | 导出演制 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_remote_control` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_remote_control` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `start_web_server` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `stop_web_server` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_service_status` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_anr_enabled` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_anr_strength` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `learn_noise_floor` | SDR 调谐/VFO | offline | `not_connected` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `add_vfo` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `remove_vfo` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `set_primary_vfo` | SDR 调谐/VFO | offline | `vfo_not_found` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `list_vfos` | SDR 调谐/VFO | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `get_status` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `shutdown` | 系统设备 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_dab_fic_decode` | 解码器长尾 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_nrsc5_frame_parse` | 解码器长尾 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_rtl433_list` | 解码器长尾 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_rtl433_decode` | 解码器长尾 | offline | `unknown_device_type` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_gk2a_parse_headers` | 解码器长尾 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `decoder_fst4_encode_text` | 解码器长尾 | offline | `invalid_text` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `device_bladerf_params_query` | 解码器长尾 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `aigui_component_catalog` | 工作台编排 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `aigui_validate_message` | 工作台编排 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `aigui_render_preview` | 工作台编排 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `ask_question` | 人机问答 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `answer_question` | 人机问答 | offline | `未知 question_id：audit-q` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `question_status` | 人机问答 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `mcp_client_list` | 外部工具 | offline | `ok` | true | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `mcp_client_connect` | 外部工具 | offline | `transport` | false | 否 | 诚实 |  |
| `mcp_client_disconnect` | 外部工具 | offline | `unknown_server` | false | 否 | 不适用 | 工具不访问外网，离线环境对它无区分力 |
| `mcp_client_call` | 外部工具 | offline | `not_connected` | false | 否 | 诚实 |  |
| `list_devices` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `connect` | 系统设备 | no_api_key | `DeviceNotFoundError` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `disconnect` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `is_connected` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_frequency` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_frequency` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_demod` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_demod` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_gain` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_sample_rate` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_bandwidth` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_squelch` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_deemphasis` | SDR 调谐/VFO | no_api_key | `SDRUserError` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_iq_invert` | SDR 调谐/VFO | no_api_key | `SDRUserError` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `list_audio_outputs` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_audio_output` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_audio` | 系统设备 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_audio` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_volume` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_mute` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `read_spectrum` | SDR 调谐/VFO | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `read_iq` | 导出演制 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `read_audio` | 导出演制 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `export_spectrum_snapshot` | 导出演制 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `export_peaks_csv` | 导出演制 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `vna_analyze_sweep` | VNA 分析 | no_api_key | `ValueError` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `vna_solve_error_box` | VNA 校准 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `vna_apply_calibration` | VNA 校准 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `export_vna_report` | 导出演制 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_analyze` | SDR 调谐/VFO | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_scan` | 书签扫描卫星 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_scan` | 书签扫描卫星 | no_api_key | `scan_not_found` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_scan_results` | 书签扫描卫星 | no_api_key | `scan_not_found` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_decoder` | 亚音数字解码 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_decoder` | 亚音数字解码 | no_api_key | `decoder_not_found` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_decoder_messages` | 亚音数字解码 | no_api_key | `decoder_not_found` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `list_satellites` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_satellite_pass` | 书签扫描卫星 | no_api_key | `station_untrusted` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `tune_satellite` | 书签扫描卫星 | no_api_key | `station_untrusted` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_ground_station` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `sat_link_list` | 卫星链路 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `sat_link_get` | 卫星链路 | no_api_key | `SDRUserError` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `sat_link_identify` | 卫星链路 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `sat_link_tuning` | 卫星链路 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `add_bookmark` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `list_bookmarks` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `delete_bookmark` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `find_nearest_bookmark` | 书签扫描卫星 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_recording` | 导出演制 | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_recording` | 导出演制 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_recording_status` | 导出演制 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_remote_control` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_remote_control` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `start_web_server` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `stop_web_server` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_service_status` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_anr_enabled` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_anr_strength` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `learn_noise_floor` | SDR 调谐/VFO | no_api_key | `not_connected` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `add_vfo` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `remove_vfo` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `set_primary_vfo` | SDR 调谐/VFO | no_api_key | `vfo_not_found` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `list_vfos` | SDR 调谐/VFO | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `get_status` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `shutdown` | 系统设备 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_dab_fic_decode` | 解码器长尾 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_nrsc5_frame_parse` | 解码器长尾 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_rtl433_list` | 解码器长尾 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_rtl433_decode` | 解码器长尾 | no_api_key | `unknown_device_type` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_gk2a_parse_headers` | 解码器长尾 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `decoder_fst4_encode_text` | 解码器长尾 | no_api_key | `invalid_text` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `device_bladerf_params_query` | 解码器长尾 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `aigui_component_catalog` | 工作台编排 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `aigui_validate_message` | 工作台编排 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `aigui_render_preview` | 工作台编排 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `ask_question` | 人机问答 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `answer_question` | 人机问答 | no_api_key | `未知 question_id：audit-q` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `question_status` | 人机问答 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `mcp_client_list` | 外部工具 | no_api_key | `ok` | true | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `mcp_client_connect` | 外部工具 | no_api_key | `unknown_server` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `mcp_client_disconnect` | 外部工具 | no_api_key | `unknown_server` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
| `mcp_client_call` | 外部工具 | no_api_key | `unknown_server` | false | 否 | 不适用 | 本表无模型类工具（模型调用在 agent/provider 层） |
