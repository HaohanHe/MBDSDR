# 工具可用性总账（P13 逐项审计）

- 工具总数：**495**（分类 75 个）
- 审计环境：`QT_QPA_PLATFORM=offscreen` + `MBDSDR_NO_HW=1` + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_OFFLINE=1`，**无真机；输入为合成替身**
- 结论：一类 404 / 二类 91 / 三类 0 / 四类 0

复跑：`python scripts/tool_availability_audit.py`（`--only <工具名>` 单跑）。
证据列：`OK` = 当次调用成功且非空；`断言:`= 往返/还原断言；`降级:`= 诚实不可用原因；`坏:`= 真坏原因。

## 补齐清单（二类按「需要什么」分组）

分组计数合计 **91** = 二类 91

| 补齐组 | 数量 | 最少补齐动作 |
| --- | --- | --- |
| ① SDR | 37 | 接一根 RTL-SDR（或 rtl_tcp / SoapySDR 后端），sdr_connect 后重跑 |
| ② 额外硬件/外部程序 | 14 | 接对应外设：云台 rotctld、GNSS/串口仪器、NanoVNA、电台或发射设备；SatDump 类需安装外部程序；界面类需在桌面端运行（UI 执行器） |
| ③ 真样本 | 22 | 提供真实 OTA 录音 / IQ 文件（含合法同步字与完整帧），或按协议构造的合法帧；或先录制/先建提案 |
| ④ 网络/API | 8 | 联网或填 API key；离线时保持诚实拒绝 |
| ⑤ 人工确认 | 10 | 显式 arm_manual=true（写文件/发射的人工确认门） |

各组工具名（与上表「补齐组」列逐行一致）：

- ① SDR（37）：`sdr_adsb_decode`、`sdr_ai_find_center`、`sdr_ai_sweep`、`sdr_bookmark_goto`、`sdr_connect`、`sdr_decode_noaa_apt`、`sdr_demodulate`、`sdr_detect_fhss`、`sdr_fm_scan`、`sdr_get_frequency`、`sdr_get_imu`、`sdr_identify_modulation`、`sdr_iq_correct`、`sdr_listen_fm`、`sdr_measure_signal`、`sdr_rds_decode`、`sdr_record_start`、`sdr_satellite_doppler_track`、`sdr_set_agc`、`sdr_set_bandwidth`、`sdr_set_demod`、`sdr_set_frequency`、`sdr_set_gain`、`sdr_set_sample_rate`、`sdr_set_squelch`、`sdr_set_volume`、`sdr_spectrum_analyze`、`sdr_spectrum_center_offset`、`sdr_spectrum_find_signals`、`sdr_spectrum_screenshot`、`sdr_spectrum_text`、`sdr_sweep_scan`、`sdr_switch_device`、`sdr_watch_capture`、`sdr_wfm_stereo`、`signal_detect`、`signal_detect_interference`
- ② 额外硬件/外部程序（14）：`gimbal_move`、`gimbal_rssi_sweep`、`instrument_query`、`radio_send_cw`、`satdump_compose_image`、`satdump_live`、`satdump_process`、`sky_gimbal_follow`、`sky_track_satellite`、`sky_tune_satellite`、`ui_layout_control`、`ui_layout_undo`、`vna_list_ports`、`vna_sweep`
- ③ 真样本（22）：`acars_decode_iq`、`amr_classify`、`amr_extract_features`、`astu_telemetry_decode`、`cma_equalize`、`dab_decode_iq`、`dab_eti_parse`、`dmr_decode`、`doppler_orbit_determine`、`fy4_lrit_decode`、`fy4_lrit_decode_iq`、`gk2a_lrit_decode`、`goes_extract_image`、`goes_hrit_parse`、`goes_lrit_parse`、`gps_code_acquire`、`interferometry_tdoa`、`meteor_lrpt_decode_iq`、`noaa_apt_decode`、`sdrangel_ogn_decode`、`ssdv_ccsds_decode`、`verify_decode_chain`
- ④ 网络/API（8）：`git_clone_repo`、`openapi_aircraft_nearby`、`openapi_iss_position`、`openapi_people_in_space`、`openapi_weather`、`subagent_execute`、`time_sync_ntp`、`web_fetch_url`
- ⑤ 人工确认（10）：`code_git_commit`、`code_modify_file`、`code_modify_section`、`code_rollback`、`orbit_error_plot`、`orbit_sky_plot`、`sdr_transmit_cw`、`sdr_transmit_iq`、`set_ppm_correction`、`tc_uplink_transmit`

## 疑似「成功文案里藏虚构」（0 项）

判据：success=True 但正文出现失败/未执行措辞。逐项人工复核后要么改失败，要么在正文写明「未确认送达」。
- 无

## 逐项总表

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。
| 工具 | 分类 | 结论 | 证据（本次调用） | 不可用时缺什么 | 补齐组 |
| --- | --- | --- | --- | --- | --- |
| `amr_classify` | amr | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜错误：未提供足够的 IQ 样本（至少 2 个交替 I/Q 实数）。AMR 只对真实信号分类；为空时不会用正弦/模拟数据冒充输入，请从接收机或录制文件提供 iq_samples。 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `amr_extract_features` | amr | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜错误：未提供足够的 IQ 样本（至少 2 个交替 I/Q 实数）。为空时不会用正弦/模拟数据冒充输入，请提供真实 iq_samples。 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `dab_decode_iq` | broadcast | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜DAB 解码失败：未找到 ETI FSYNC 同步——请确认输入为 ETI(NI) 字节流（可含前缀杂质，工具会自动搜索同步），制式为 DAB Mode I | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `dab_eti_parse` | broadcast | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜ETI 解析失败：FSYNC 已同步但后续校验失败（ERR 标志/头 CRC/MSC CRC）：帧可能损坏，或输入制式不是 DAB Mode I ETI(NI) | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `code_git_commit` | code_editor | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 code_git_commit（写操作需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `code_modify_file` | code_editor | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 code_modify_file（写操作需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `code_modify_section` | code_editor | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 code_modify_section（写操作需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `code_rollback` | code_editor | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 code_rollback（写操作需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `acars_decode_iq` | decode | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜未检测到 ACARS 帧（诚实空态：输入音频无同步/CRC 未通过；请用含真实报文的中频录音，合成正弦不会被当作报文） | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `dmr_decode` | digital | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜DMR 解码失败：未匹配到 DMR 同步字（容差内最近偏差 sync_errors=12）：请确认 dibit 流以同步字段对齐，且制式为 DMR 4FSK (4800 sym/s) | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `gimbal_rssi_sweep` | gimbal | 二类·诚实不可用 | 降级：硬件：SDR 设备｜=== 定向天线方位测向扫描 ===  模式: 自动, 步进 1.0°, 仰角 45.0°  方位(°)   RSSI(dBm)     未采到 RSSI（SDR 未连接或无读数）；可切 manual 模式人工测向。 | 硬件：SDR 设备 | ② 额外硬件/外部程序 |
| `instrument_query` | hal | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 未发现可用仪器 | 硬件：SDR 设备 | ② 额外硬件/外部程序 |
| `sdr_transmit_cw` | hal | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 CW 发射（gate_not_armed: 未过人工门：需显式 arm_manual=true 或有效 token） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `sdr_transmit_iq` | hal | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 IQ 发射（gate_not_armed: 未过人工门：需显式 arm_manual=true 或有效 token） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `doppler_orbit_determine` | orbit | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜轨道半径异常: 5997.004 km | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `orbit_error_plot` | orbit | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 orbit_error_plot（PNG 落盘属写操作，需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `orbit_sky_plot` | orbit | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 orbit_sky_plot（PNG 落盘属写操作，需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `time_sync_ntp` | orbit | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜NTP 查询失败: 离线模式（MBDSDR_OFFLINE）：NTP 授时需要联网，已拒绝；时间源保持系统时钟（未校准） | 网络：外网访问/在线数据源 | ④ 网络/API |
| `gimbal_move` | pointing | 二类·诚实不可用 | 降级：硬件：外设未响应（云台/仪器）｜云台控制失败: 连接超时：对端无响应（检查设备是否在线、网络/防火墙是否放行） | 硬件：外设未响应（云台/仪器） | ② 额外硬件/外部程序 |
| `radio_send_cw` | radio_control | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜=== CW 发送 === 文本: HELLO 速度: 20 WPM Morse: .... . .-.. .-.. --- 状态: 失败（需先PTT on）  提示: 先调用 radio_ptt on 切换到发射，再调 | 有效输入：合成替身不满足该工具的输入要求 | ② 额外硬件/外部程序 |
| `sdrangel_ogn_decode` | sat_groundstation | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜错误: OGN/FLARM 帧解析失败: 非 V6 帧: proto=8 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `astu_telemetry_decode` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜参数/计算错误: 需要 iq（复数采样列表）或 iq_path（complex64 raw 文件）或 iq_base64（float32 交错 base64） | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `fy4_lrit_decode` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜FY-4 解码失败: VCDU 长度必须为 892 字节，实际 24 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `fy4_lrit_decode_iq` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜{"success": false, "stage": "frame_sync", "error": "未从 IQ 中解出完整 CCSDS 帧", "diagnostic": {"n_bits_demodded": 81 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `gk2a_lrit_decode` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜{"png": "", "width": 0, "height": 0, "annotation": "", "error": "软符号长度不足"} | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `goes_extract_image` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜错误: 参数 files[0] 不是合法的十六进制字符串（收到 'TEST'），无法解析为字节 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `goes_hrit_parse` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜LRIT 解析失败: VCDU 长度必须为 892 字节，实际 24 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `goes_lrit_parse` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜LRIT 解析失败: VCDU 长度必须为 892 字节，实际 24 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `gps_code_acquire` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜{"per_code": {"PRN1": {"peak": 2.181236839175987, "chip_phase": 174, "doppler_hz": -4100.0}, "PRN2": {"peak":  | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `interferometry_tdoa` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜{"lag_samples": 1548.2356462052346, "delay_s": 0.19352945577565434, "peak": 0.15542614220190815, "min_peak": 0 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `satdump_compose_image` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜=== 云图合成 ===  输入目录: test 输出文件: C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_2sb2m_ih\output_file.bin 增强: 是   | 有效输入：合成替身不满足该工具的输入要求 | ② 额外硬件/外部程序 |
| `satdump_live` | satellite | 二类·诚实不可用 | 降级：外部程序/依赖：未安装｜=== SatDump 实时接收 ===  卫星: noaa19_apt 频率: 100.000 MHz 输出目录: C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_2sb2 | 外部程序/依赖：未安装 | ② 额外硬件/外部程序 |
| `satdump_process` | satellite | 二类·诚实不可用 | 降级：外部程序/依赖：未安装｜=== SatDump 离线处理 ===  输入文件: C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_2sb2m_ih\input_file.bin 卫星: noaa19_ | 外部程序/依赖：未安装 | ② 额外硬件/外部程序 |
| `tc_uplink_transmit` | satellite | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜{"status": "GATED", "code": "gate_not_armed", "reason_code": "gate_not_armed", "gate_state": "disarmed", "reas | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `verify_decode_chain` | satellite | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜验证链失败: CRC crc16_ccitt_false 校验失败：帧 5555 ≠ 计算 75B0——帧内容损坏，不产出字段结论 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `openapi_aircraft_nearby` | sdr_ai | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜=== 附近飞机（ADS-B） ===  位置: 43.8868°, 125.3245° 半径: 50 km  错误: 离线模式（MBDSDR_OFFLINE）：附近飞机（OpenSky ADS-B） 需要联网，已拒绝； | 网络：外网访问/在线数据源 | ④ 网络/API |
| `openapi_iss_position` | sdr_ai | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜=== 国际空间站（ISS）位置 ===  错误: 离线模式（MBDSDR_OFFLINE）：ISS 实时位置（open-notify） 需要联网，已拒绝；这些公开 API 无本地缓存可降级 | 网络：外网访问/在线数据源 | ④ 网络/API |
| `openapi_people_in_space` | sdr_ai | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜=== 当前太空人员 ===  错误: 离线模式（MBDSDR_OFFLINE）：太空人数（open-notify） 需要联网，已拒绝；这些公开 API 无本地缓存可降级 | 网络：外网访问/在线数据源 | ④ 网络/API |
| `openapi_weather` | sdr_ai | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜=== 天气预报 ===  位置: 43.8868°, 125.3245°  错误: 离线模式（MBDSDR_OFFLINE）：实时天气（open-meteo） 需要联网，已拒绝；这些公开 API 无本地缓存可降级 | 网络：外网访问/在线数据源 | ④ 网络/API |
| `sdr_ai_find_center` | sdr_ai | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_ai_sweep` | sdr_ai | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_detect_fhss` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_identify_modulation` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_iq_correct` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_measure_signal` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `signal_detect` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，请先调用 sdr_connect | 硬件：SDR 设备 | ① SDR |
| `signal_detect_interference` | sdr_analysis | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，请先调用 sdr_connect | 硬件：SDR 设备 | ① SDR |
| `meteor_lrpt_decode_iq` | sdr_decode | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜未解出任何 LRPT 帧（四种相位旋转都未命中同步字/RS 块）——检查采样率、sps 与信号质量。 | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `noaa_apt_decode` | sdr_decode | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜读取 WAV 失败: File format b'\x00\x00\x00\x00' not understood. Only 'RIFF', 'RIFX', and 'RF64' supported. | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `sdr_adsb_decode` | sdr_decode | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_decode_noaa_apt` | sdr_decode | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接（给 input_path 可离线解码 WAV/IQ 文件） | 硬件：SDR 设备 | ① SDR |
| `sdr_listen_fm` | sdr_decode | 二类·诚实不可用 | 降级：硬件：SDR 设备｜设备未连接，无法收听。请先 sdr_connect 连接 SDR 设备（RTL-SDR/HackRF 等），。 | 硬件：SDR 设备 | ① SDR |
| `sdr_rds_decode` | sdr_decode | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_wfm_stereo` | sdr_decode | 二类·诚实不可用 | 降级：硬件：SDR 设备｜设备未连接（给 input_path 可离线解码 IQ 文件） | 硬件：SDR 设备 | ① SDR |
| `sdr_demodulate` | sdr_demod | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_set_demod` | sdr_demod | 二类·诚实不可用 | 降级：硬件：SDR 设备｜解调模式设置失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_set_squelch` | sdr_demod | 二类·诚实不可用 | 降级：硬件：SDR 设备｜操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_set_volume` | sdr_demod | 二类·诚实不可用 | 降级：硬件：SDR 设备｜操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_bookmark_goto` | sdr_device | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，无法跳转频率 | 硬件：SDR 设备 | ① SDR |
| `sdr_connect` | sdr_device | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜连接失败 | 有效输入：合成替身不满足该工具的输入要求 | ① SDR |
| `sdr_switch_device` | sdr_device | 二类·诚实不可用 | 降级：硬件：SDR 设备｜切换失败：设备 mock 不存在或无法打开（先用 sdr_list_devices 核对 device_id） | 硬件：SDR 设备 | ① SDR |
| `sdr_get_frequency` | sdr_frequency | 二类·诚实不可用 | 降级：硬件：SDR 设备｜未连接设备或未打开 IQ 文件，无法查询频率（先 sdr_connect / sdr_open_iq_file） | 硬件：SDR 设备 | ① SDR |
| `sdr_set_bandwidth` | sdr_frequency | 二类·诚实不可用 | 降级：硬件：SDR 设备｜操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_set_frequency` | sdr_frequency | 二类·诚实不可用 | 降级：硬件：SDR 设备｜频率设置失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_set_sample_rate` | sdr_frequency | 二类·诚实不可用 | 降级：硬件：SDR 设备｜采样率设置失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `set_ppm_correction` | sdr_frequency | 二类·诚实不可用 | 降级：人工确认：arm_manual 安全门｜手动模式：未执行 set_ppm_correction（写操作需显式传 arm_manual=true） | 人工确认：arm_manual 安全门 | ⑤ 人工确认 |
| `sdr_set_agc` | sdr_gain | 二类·诚实不可用 | 降级：硬件：SDR 设备｜操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_set_gain` | sdr_gain | 二类·诚实不可用 | 降级：硬件：SDR 设备｜操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file | 硬件：SDR 设备 | ① SDR |
| `sdr_get_imu` | sdr_location | 二类·诚实不可用 | 降级：硬件：SDR 设备｜IMU 姿态: 未连接（未检测到九轴硬件） | 硬件：SDR 设备 | ① SDR |
| `sdr_record_start` | sdr_record | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_satellite_doppler_track` | sdr_satellite | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，无法跟踪录制 | 硬件：SDR 设备 | ① SDR |
| `sdr_fm_scan` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜当前无可用的实时 SDR 设备（离线 IQ 回放源是单段固定录制，也不能步进调谐扫频）。请先 sdr_connect 连接真实设备后再扫频。 | 硬件：SDR 设备 | ① SDR |
| `sdr_spectrum_analyze` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，请先调用 sdr_connect | 硬件：SDR 设备 | ① SDR |
| `sdr_spectrum_center_offset` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_spectrum_find_signals` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_spectrum_screenshot` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_spectrum_text` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接 | 硬件：SDR 设备 | ① SDR |
| `sdr_sweep_scan` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，无法扫频 | 硬件：SDR 设备 | ① SDR |
| `sdr_watch_capture` | sdr_spectrum | 二类·诚实不可用 | 降级：硬件：SDR 设备｜错误: 设备未连接，无法守听 | 硬件：SDR 设备 | ① SDR |
| `cma_equalize` | signal_analysis | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜{"cost_initial": 1.437035, "cost_final": 1.320546, "evm_before_db": 0.012, "evm_after_db": -1.059, "samples_us | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `sky_gimbal_follow` | sky | 二类·诚实不可用 | 降级：桌面：需在桌面端运行（UI 执行器未挂载）｜{"ok": false, "status": "UNAVAILABLE", "reason": "天空动作执行器未挂载（非桌面环境），硬件未动作"} | 桌面：需在桌面端运行（UI 执行器未挂载） | ② 额外硬件/外部程序 |
| `sky_track_satellite` | sky | 二类·诚实不可用 | 降级：桌面：需在桌面端运行（UI 执行器未挂载）｜{"ok": false, "status": "UNAVAILABLE", "reason": "天空动作执行器未挂载（非桌面环境），硬件未动作"} | 桌面：需在桌面端运行（UI 执行器未挂载） | ② 额外硬件/外部程序 |
| `sky_tune_satellite` | sky | 二类·诚实不可用 | 降级：桌面：需在桌面端运行（UI 执行器未挂载）｜{"ok": false, "status": "UNAVAILABLE", "reason": "天空动作执行器未挂载（非桌面环境），硬件未动作"} | 桌面：需在桌面端运行（UI 执行器未挂载） | ② 额外硬件/外部程序 |
| `ssdv_ccsds_decode` | ssdv_rx | 二类·诚实不可用 | 降级：有效输入：合成替身不满足该工具的输入要求｜asm=0 rs=[] mcu=0/0 jpeg=0B | 有效输入：合成替身不满足该工具的输入要求 | ③ 真样本 |
| `subagent_execute` | subagent | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜{   "success": false,   "output": "",   "data": {},   "error": "模型调用失败: 离线模式（MBDSDR_OFFLINE）：模型 API 需要联网，已拒绝调用 | 网络：外网访问/在线数据源 | ④ 网络/API |
| `ui_layout_control` | ui | 二类·诚实不可用 | 降级：桌面：需在桌面端运行（UI 执行器未挂载）｜{"ok": false, "status": "UNAVAILABLE", "reason": "UI 执行器未挂载（非桌面环境），界面未改变"} | 桌面：需在桌面端运行（UI 执行器未挂载） | ② 额外硬件/外部程序 |
| `ui_layout_undo` | ui | 二类·诚实不可用 | 降级：桌面：需在桌面端运行（UI 执行器未挂载）｜{"ok": false, "status": "EMPTY", "reason": "没有可撤销的界面动作"} | 桌面：需在桌面端运行（UI 执行器未挂载） | ② 额外硬件/外部程序 |
| `vna_list_ports` | vna | 二类·诚实不可用 | 降级：硬件：SDR 设备｜未探测到 NanoVNA（VID:PID 0x0483:0x5740）。设备未连接、被占用或无 pyserial 时均为此诚实空态。 | 硬件：SDR 设备 | ② 额外硬件/外部程序 |
| `vna_sweep` | vna | 二类·诚实不可用 | 降级：硬件：SDR 设备｜扫频未执行（无可用设备或参数被拒）：未探测到 NanoVNA（VID:PID 0x0483:0x5740）。 请用 --port 显式指定串口（Linux /dev/ttyACM0，Windows COMx），并确认 U | 硬件：SDR 设备 | ② 额外硬件/外部程序 |
| `git_clone_repo` | web | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜离线模式（MBDSDR_OFFLINE）：git clone 需要联网，已拒绝；工作区 repos/ 下已有的仓库仍可直接使用 | 网络：外网访问/在线数据源 | ④ 网络/API |
| `web_fetch_url` | web | 二类·诚实不可用 | 降级：网络：外网访问/在线数据源｜离线模式（MBDSDR_OFFLINE）：web_fetch_url 需要联网，已拒绝；本工具无本地缓存可降级（离线请用本地文件/SDR 数据） | 网络：外网访问/在线数据源 | ④ 网络/API |
| `cantenna_design` | acquisition | 一类·真跑 | OK（299 字符，data=有） |  | — |
| `dish_gain` | acquisition | 一类·真跑 | OK（77 字符，data=有） |  | — |
| `fe_diagnose` | acquisition | 一类·真跑 | OK（229 字符，data=有） |  | — |
| `fe_link_budget` | acquisition | 一类·真跑 | OK（235 字符，data=有） |  | — |
| `geo_pointing` | acquisition | 一类·真跑 | OK（93 字符，data=有） |  | — |
| `pointing_tolerance` | acquisition | 一类·真跑 | OK（79 字符，data=有） |  | — |
| `rx_acceptance` | acquisition | 一类·真跑 | OK（919 字符，data=有） |  | — |
| `sat_rx_profile` | acquisition | 一类·真跑 | OK（245 字符，data=有） |  | — |
| `telemetry_rx_chain` | acquisition | 一类·真跑 | OK（454 字符，data=有） |  | — |
| `amr_add_sample` | amr | 一类·真跑 | OK（26 字符，data=无） |  | — |
| `amr_stats` | amr | 一类·真跑 | OK（320 字符，data=有） |  | — |
| `astro_altaz_to_equatorial` | astronomy | 一类·真跑 | OK（146 字符，data=有） |  | — |
| `astro_antenna_params` | astronomy | 一类·真跑 | OK（245 字符，data=有） |  | — |
| `astro_compute_airmass` | astronomy | 一类·真跑 | OK（20 字符，data=无） |  | — |
| `astro_compute_refraction` | astronomy | 一类·真跑 | OK（35 字符，data=无） |  | — |
| `astro_equatorial_to_altaz` | astronomy | 一类·真跑 | OK（151 字符，data=有） |  | — |
| `astro_get_observer` | astronomy | 一类·真跑 | OK（170 字符，data=有） |  | — |
| `astro_pointing_guidance` | astronomy | 一类·真跑 | OK（490 字符，data=有） |  | — |
| `astro_set_observer` | astronomy | 一类·真跑 | OK（64 字符，data=无） |  | — |
| `astro_time_info` | astronomy | 一类·真跑 | OK（192 字符，data=有） |  | — |
| `dab_fic_decode` | broadcast | 一类·真跑 | OK（78 字符，data=有） |  | — |
| `hdradio_decode_iq` | broadcast | 一类·真跑 | OK（58 字符，data=有） |  | — |
| `hdradio_frame_parse` | broadcast | 一类·真跑 | OK（29 字符，data=有） |  | — |
| `hdradio_ofdm_demod` | broadcast | 一类·真跑 | OK（69 字符，data=有） |  | — |
| `baseband_save` | capture | 一类·真跑 | OK（386 字符，data=有） |  | — |
| `code_git_status` | code_editor | 一类·真跑 | OK（862 字符，data=有） |  | — |
| `code_hot_reload` | code_editor | 一类·真跑 | OK（15 字符，data=无） |  | — |
| `code_list_edits` | code_editor | 一类·真跑 | OK（2 字符，data=无） |  | — |
| `code_read_file` | code_editor | 一类·真跑 | OK（4023 字符，data=无） |  | — |
| `code_run_tests` | code_editor | 一类·真跑 | OK（50 字符，data=有） |  | — |
| `code_stats` | code_editor | 一类·真跑 | OK（199 字符，data=有） |  | — |
| `coord_ecef_to_eci` | coord | 一类·真跑 | OK（101 字符，data=有） |  | — |
| `coord_ecef_to_enu` | coord | 一类·真跑 | OK（22 字符，data=有） |  | — |
| `coord_ecef_to_geodetic` | coord | 一类·真跑 | OK（44 字符，data=有） |  | — |
| `coord_eci_to_ecef` | coord | 一类·真跑 | OK（100 字符，data=有） |  | — |
| `coord_enu_to_az_el` | coord | 一类·真跑 | OK（20 字符，data=有） |  | — |
| `coord_geodetic_to_ecef` | coord | 一类·真跑 | OK（64 字符，data=有） |  | — |
| `acars_msk_demod` | decode | 一类·真跑 | OK（44 字符，data=有） |  | — |
| `acars_parse_message` | decode | 一类·真跑 | OK（38 字符，data=有） |  | — |
| `adsb_decode_iq` | decode | 一类·真跑 | OK（257 字符，data=有） |  | — |
| `afsk_demodulate` | decode | 一类·真跑 | OK（26 字符，data=有） |  | — |
| `aprs_decode_audio` | decode | 一类·真跑 | OK（26 字符，data=有） |  | — |
| `aprs_parse` | decode | 一类·真跑 | OK（308 字符，data=有） |  | — |
| `apt_decode_audio` | decode | 一类·真跑 | OK（45 字符，data=有） |  | — |
| `ax25_decode` | decode | 一类·真跑 | OK（142 字符，data=有） |  | — |
| `ccsds_decode_bits` | decode | 一类·真跑 | OK（26 字符，data=有） |  | — |
| `ccsds_decode_iq` | decode | 一类·真跑 | OK（115 字符，data=有） |  | — |
| `ccsds_encode_frame` | decode | 一类·真跑 | OK（51 字符，data=有） |  | — |
| `cw_decode_audio` | decode | 一类·真跑 | OK（2580 字符，data=有） |  | — |
| `fst4_encode` | decode | 一类·真跑 | OK（566 字符，data=有） |  | — |
| `fst4_ldpc_decode` | decode | 一类·真跑 | OK（767 字符，data=有） |  | — |
| `ft8_decode_audio` | decode | 一类·真跑 | OK（45 字符，data=有） |  | — |
| `ft8_encode` | decode | 一类·真跑 | OK（329 字符，data=有） |  | — |
| `ft8_ldpc_decode` | decode | 一类·真跑 | OK（859 字符，data=有） |  | — |
| `ft8_soft_decode` | decode | 一类·真跑 | OK（330 字符，data=有） |  | — |
| `ft8_unpack_callsign` | decode | 一类·真跑 | OK（34 字符，data=有） |  | — |
| `ft8_unpack_message` | decode | 一类·真跑 | OK（98 字符，data=有） |  | — |
| `rds_decode_mpx` | decode | 一类·真跑 | OK（45 字符，data=有） |  | — |
| `demod_analog_audio` | demod | 一类·真跑 | OK（125 字符，data=有） |  | — |
| `demod_wfm_stereo` | demod | 一类·真跑 | OK（80 字符，data=有） |  | — |
| `dsd_decode_iq` | digital | 一类·真跑 | OK（42 字符，data=有） |  | — |
| `fourfsk_demod` | digital | 一类·真跑 | OK（48 字符，data=有） |  | — |
| `p25_decode` | digital | 一类·真跑 | OK（37 字符，data=有） |  | — |
| `feldhell_decode` | digital_mode | 一类·真跑 | OK（33 字符，data=有） |  | — |
| `mfsk_decode` | digital_mode | 一类·真跑 | OK（81 字符，data=有） |  | — |
| `psk31_decode` | digital_mode | 一类·真跑 | OK（35 字符，data=有） |  | — |
| `psk31_encode` | digital_mode | 一类·真跑 | OK（79 字符，data=有） |  | — |
| `rtty_decode` | digital_mode | 一类·真跑 | OK（34 字符，data=有） |  | — |
| `baudot_decode` | digital_modes | 一类·真跑 | OK（18 字符，data=有） |  | — |
| `baudot_encode` | digital_modes | 一类·真跑 | OK（31 字符，data=有） |  | — |
| `bell103_demodulate_text` | digital_modes | 一类·真跑 | OK（30 字符，data=有） |  | — |
| `bell103_modulate_text` | digital_modes | 一类·真跑 | OK（64 字符，data=有） |  | — |
| `digital_mode_frequencies` | digital_modes | 一类·真跑 | OK（455 字符，data=无） |  | — |
| `digital_mode_params` | digital_modes | 一类·真跑 | OK（52 字符，data=无） |  | — |
| `fsk_demodulate` | digital_modes | 一类·真跑 | OK（45 字符，data=有） |  | — |
| `fsk_modulate` | digital_modes | 一类·真跑 | OK（37 字符，data=有） |  | — |
| `minimodem_decode_audio` | digital_modes | 一类·真跑 | OK（33 字符，data=有） |  | — |
| `wsjtx_read_decodes` | digital_modes | 一类·真跑 | OK（137 字符，data=无） |  | — |
| `m17_4fsk_roundtrip` | digital_voice | 一类·真跑 | OK（209 字符，data=有） |  | — |
| `m17_conv_roundtrip` | digital_voice | 一类·真跑 | OK（159 字符，data=有） |  | — |
| `m17_crc16` | digital_voice | 一类·真跑 | OK（134 字符，data=有） |  | — |
| `m17_lsf_build` | digital_voice | 一类·真跑 | OK（172 字符，data=有） |  | — |
| `p25_imbe_parse` | digital_voice | 一类·真跑 | OK（204 字符，data=有） |  | — |
| `p25_nid_decode` | digital_voice | 一类·真跑 | OK（195 字符，data=有） |  | — |
| `p25_nid_roundtrip` | digital_voice | 一类·真跑 | OK（156 字符，data=有） |  | — |
| `p25_phase2_info` | digital_voice | 一类·真跑 | OK（284 字符，data=有） |  | — |
| `sdrtrunk_affiliation` | digital_voice | 一类·真跑 | OK（118 字符，data=有） |  | — |
| `sdrtrunk_channel_grant` | digital_voice | 一类·真跑 | OK（119 字符，data=有） |  | — |
| `sdrtrunk_snapshot` | digital_voice | 一类·真跑 | OK（275 字符，data=有） |  | — |
| `sdrtrunk_state_transition` | digital_voice | 一类·真跑 | OK（160 字符，data=有） |  | — |
| `doppler_from_frequency` | doppler | 一类·真跑 | OK（55 字符，data=有） |  | — |
| `doppler_to_frequency` | doppler | 一类·真跑 | OK（55 字符，data=有） |  | — |
| `diff_decode` | dsp | 一类·真跑 | OK（20 字符，data=有） |  | — |
| `diff_encode` | dsp | 一类·真跑 | OK（20 字符，data=有） |  | — |
| `downchannelize` | dsp | 一类·真跑 | OK（43 字符，data=有） |  | — |
| `dsp_engine_create` | dsp | 一类·真跑 | OK（61 字符，data=有） |  | — |
| `fft_filter` | dsp | 一类·真跑 | OK（69 字符，data=有） |  | — |
| `fir_filter` | dsp | 一类·真跑 | OK（53 字符，data=有） |  | — |
| `iq_freq_track` | dsp | 一类·真跑 | OK（30 字符，data=有） |  | — |
| `pfb_resample` | dsp | 一类·真跑 | OK（50 字符，data=有） |  | — |
| `rational_resample` | dsp | 一类·真跑 | OK（70 字符，data=有） |  | — |
| `afsk_modulate` | encode | 一类·真跑 | OK（101 字符，data=有） |  | — |
| `ax25_encode` | encode | 一类·真跑 | OK（152 字符，data=有） |  | — |
| `evolution_commit` | evolution | 一类·真跑 | OK（31 字符，data=无） |  | — |
| `evolution_evaluate` | evolution | 一类·真跑 | OK（28 字符，data=有） |  | — |
| `evolution_history` | evolution | 一类·真跑 | OK（789 字符，data=有） |  | — |
| `evolution_propose` | evolution | 一类·真跑 | OK（78 字符，data=无） |  | — |
| `evolution_rollback` | evolution | 一类·真跑 | OK（58 字符，data=无） |  | — |
| `evolution_status` | evolution | 一类·真跑 | OK（70 字符，data=无） |  | — |
| `bch_decode` | fec | 一类·真跑 | OK（12 字符，data=有） |  | — |
| `bch_encode` | fec | 一类·真跑 | OK（19 字符，data=有） |  | — |
| `bch_generator` | fec | 一类·真跑 | OK（29 字符，data=有） |  | — |
| `file_change_changelog` | file_tracker | 一类·真跑 | OK（4023 字符，data=无） |  | — |
| `file_change_history` | file_tracker | 一类·真跑 | OK（4023 字符，data=有） |  | — |
| `file_change_revert` | file_tracker | 一类·真跑 | OK（481 字符，data=有） |  | — |
| `file_change_stats` | file_tracker | 一类·真跑 | OK（279 字符，data=有） |  | — |
| `file_change_track` | file_tracker | 一类·真跑 | OK（289 字符，data=有） |  | — |
| `gimbal_connect` | gimbal | 一类·真跑 | OK（41 字符，data=无） |  | — |
| `gimbal_modes` | gimbal | 一类·真跑 | OK（333 字符，data=无） |  | — |
| `gimbal_point` | gimbal | 一类·真跑 | OK（125 字符，data=无） |  | — |
| `gimbal_read_pose` | gimbal | 一类·真跑 | OK（92 字符，data=无） |  | — |
| `gimbal_stop` | gimbal | 一类·真跑 | OK（21 字符，data=无） |  | — |
| `gimbal_track_satellite` | gimbal | 一类·真跑 | OK（125 字符，data=无） |  | — |
| `ecef_to_llh` | gnss | 一类·真跑 | OK（154 字符，data=有） |  | — |
| `gps_ephemeris_satpos` | gnss | 一类·真跑 | OK（62 字符，data=有） |  | — |
| `gps_nav_decode` | gnss | 一类·真跑 | OK（53 字符，data=有） |  | — |
| `gps_pvt_solve` | gnss | 一类·真跑 | OK（67 字符，data=有） |  | — |
| `gps_time_to_utc` | gnss | 一类·真跑 | OK（146 字符，data=有） |  | — |
| `how_validate` | gnss | 一类·真跑 | OK（133 字符，data=有） |  | — |
| `nav_bit_sync` | gnss | 一类·真跑 | OK（323 字符，data=有） |  | — |
| `ntrip_connect` | gnss | 一类·真跑 | OK（85 字符，data=有） |  | — |
| `rinex_parse` | gnss | 一类·真跑 | OK（36 字符，data=有） |  | — |
| `spp_locate` | gnss | 一类·真跑 | OK（311 字符，data=有） |  | — |
| `subframe_assemble` | gnss | 一类·真跑 | OK（39 字符，data=有） |  | — |
| `gnss_direction_find` | gnss_monitor | 一类·真跑 | OK（118 字符，data=有） |  | — |
| `gnss_monitor_all` | gnss_monitor | 一类·真跑 | OK（814 字符，data=无） |  | — |
| `gnss_monitor_band` | gnss_monitor | 一类·真跑 | OK（187 字符，data=无） |  | — |
| `guardian_list` | guardian | 一类·真跑 | OK（2 字符，data=无） |  | — |
| `guardian_rollback` | guardian | 一类·真跑 | OK（11 字符，data=无） |  | — |
| `guardian_status` | guardian | 一类·真跑 | OK（119 字符，data=无） |  | — |
| `instrument_list` | hal | 一类·真跑 | OK（508 字符，data=无） |  | — |
| `platform_info` | hal | 一类·真跑 | OK（128 字符，data=无） |  | — |
| `sdr_connect_hardware` | hal | 一类·真跑 | OK（51 字符，data=无） |  | — |
| `sdr_list_hardware` | hal | 一类·真跑 | OK（1913 字符，data=无） |  | — |
| `ardop_build_frame` | ham_modes | 一类·真跑 | OK（43 字符，data=有） |  | — |
| `ardop_constellation` | ham_modes | 一类·真跑 | OK（39 字符，data=有） |  | — |
| `ardop_state_machine` | ham_modes | 一类·真跑 | OK（77 字符，data=有） |  | — |
| `js8_decode_message` | ham_modes | 一类·真跑 | OK（36 字符，data=有） |  | — |
| `js8_encode_message` | ham_modes | 一类·真跑 | OK（72 字符，data=有） |  | — |
| `js8_submodes` | ham_modes | 一类·真跑 | OK（60 字符，data=有） |  | — |
| `pat_b2f_decode` | ham_modes | 一类·真跑 | OK（68 字符，data=有） |  | — |
| `pat_b2f_encode` | ham_modes | 一类·真跑 | OK（32 字符，data=有） |  | — |
| `pat_list_transports` | ham_modes | 一类·真跑 | OK（765 字符，data=有） |  | — |
| `pat_mid_list` | ham_modes | 一类·真跑 | OK（19 字符，data=有） |  | — |
| `xastir_decode_frame` | ham_modes | 一类·真跑 | OK（37 字符，data=有） |  | — |
| `xastir_encode_object` | ham_modes | 一类·真跑 | OK（40 字符，data=有） |  | — |
| `xastir_geofence` | ham_modes | 一类·真跑 | OK（24 字符，data=有） |  | — |
| `judge_evaluate` | judge | 一类·真跑 | OK（400 字符，data=有） |  | — |
| `judge_history` | judge | 一类·真跑 | OK（444 字符，data=有） |  | — |
| `judge_stats` | judge | 一类·真跑 | OK（347 字符，data=有） |  | — |
| `learning_experiences` | learning | 一类·真跑 | OK（4023 字符，data=有） |  | — |
| `learning_patterns` | learning | 一类·真跑 | OK（2 字符，data=无） |  | — |
| `learning_record` | learning | 一类·真跑 | OK（269 字符，data=有） |  | — |
| `learning_stats` | learning | 一类·真跑 | OK（274 字符，data=有） |  | — |
| `memory_search` | memory | 一类·真跑 | OK（2614 字符，data=有） |  | — |
| `memory_write` | memory | 一类·真跑 | OK（12 字符，data=无） |  | — |
| `context_status` | meta | 一类·真跑 | OK（109 字符，data=有） |  | — |
| `list_models` | meta | 一类·真跑 | OK（4 字符，data=有） |  | — |
| `list_tools` | meta | 一类·真跑 | OK（4025 字符，data=有） |  | — |
| `model_status` | meta | 一类·真跑 | OK（179 字符，data=有） |  | — |
| `switch_model` | meta | 一类·真跑 | OK（31 字符，data=无） |  | — |
| `tool_log` | meta | 一类·真跑 | OK（173 字符，data=有） |  | — |
| `gmsk_demodulate` | modem | 一类·真跑 | OK（15 字符，data=有） |  | — |
| `gmsk_modulate` | modem | 一类·真跑 | OK（32 字符，data=有） |  | — |
| `constellation_metrics` | modulation | 一类·真跑 | OK（66 字符，data=有） |  | — |
| `demodulate_symbols` | modulation | 一类·真跑 | OK（31 字符，data=有） |  | — |
| `eye_diagram` | modulation | 一类·真跑 | OK（70 字符，data=有） |  | — |
| `link_ber_test` | modulation | 一类·真跑 | OK（58 字符，data=有） |  | — |
| `measure_ber` | modulation | 一类·真跑 | OK（38 字符，data=有） |  | — |
| `modulate_symbols` | modulation | 一类·真跑 | OK（40 字符，data=有） |  | — |
| `pulse_shape_taps` | modulation | 一类·真跑 | OK（51 字符，data=有） |  | — |
| `afsk1200_demod` | multimon | 一类·真跑 | OK（1359 字符，data=有） |  | — |
| `dtmf_decode` | multimon | 一类·真跑 | OK（31 字符，data=有） |  | — |
| `pocsag_bch_correct` | multimon | 一类·真跑 | OK（100 字符，data=有） |  | — |
| `pocsag_decode` | multimon | 一类·真跑 | OK（15 字符，data=有） |  | — |
| `zvei_decode` | multimon | 一类·真跑 | OK（34 字符，data=有） |  | — |
| `gis_bearing` | new_spacetime | 一类·真跑 | OK（98 字符，data=无） |  | — |
| `gis_destination` | new_spacetime | 一类·真跑 | OK（97 字符，data=无） |  | — |
| `gis_distance` | new_spacetime | 一类·真跑 | OK（113 字符，data=无） |  | — |
| `gnss_parse_rmc` | new_spacetime | 一类·真跑 | OK（27 字符，data=无） |  | — |
| `gnss_system_info` | new_spacetime | 一类·真跑 | OK（361 字符，data=无） |  | — |
| `pnt_get_state` | new_spacetime | 一类·真跑 | OK（37 字符，data=无） |  | — |
| `pnt_update_source` | new_spacetime | 一类·真跑 | OK（188 字符，data=无） |  | — |
| `satellite_predict_all` | new_spacetime | 一类·真跑 | OK（202 字符，data=无） |  | — |
| `satellite_predict_pass` | new_spacetime | 一类·真跑 | OK（52 字符，data=无） |  | — |
| `sky_view_visible` | new_spacetime | 一类·真跑 | OK（90 字符，data=无） |  | — |
| `time_get_info` | new_spacetime | 一类·真跑 | OK（124 字符，data=无） |  | — |
| `time_ntp_sync` | new_spacetime | 一类·真跑 | OK（78 字符，data=无） |  | — |
| `calibrate_time_offset` | orbit | 一类·真跑 | OK（56 字符，data=有） |  | — |
| `doppler_tune_frequency` | orbit | 一类·真跑 | OK（260 字符，data=有） |  | — |
| `initial_orbit_laplace` | orbit | 一类·真跑 | OK（530 字符，data=有） |  | — |
| `lro_track` | orbit | 一类·真跑 | OK（51 字符，data=有） |  | — |
| `orbit_propagate_stm_j2` | orbit | 一类·真跑 | OK（615 字符，data=有） |  | — |
| `predict_satellite_passes` | orbit | 一类·真跑 | OK（26 字符，data=有） |  | — |
| `propagate_state_stm` | orbit | 一类·真跑 | OK（34 字符，data=有） |  | — |
| `pseudorange_rate_model` | orbit | 一类·真跑 | OK（28 字符，data=有） |  | — |
| `rls_update` | orbit | 一类·真跑 | OK（53 字符，data=有） |  | — |
| `satellite_now_pointing` | orbit | 一类·真跑 | OK（291 字符，data=有） |  | — |
| `tle_state_ecef` | orbit | 一类·真跑 | OK（66 字符，data=有） |  | — |
| `sdr_plan` | planning | 一类·真跑 | OK（80 字符，data=无） |  | — |
| `sdr_spill_read` | planning | 一类·真跑 | OK（34 字符，data=无） |  | — |
| `sdr_spill_save` | planning | 一类·真跑 | OK（104 字符，data=无） |  | — |
| `sdr_todo` | planning | 一类·真跑 | OK（29 字符，data=无） |  | — |
| `ar_pointing_guidance` | pose | 一类·真跑 | OK（278 字符，data=有） |  | — |
| `ar_project_satellite` | pose | 一类·真跑 | OK（234 字符，data=有） |  | — |
| `pose_get` | pose | 一类·真跑 | OK（312 字符，data=有） |  | — |
| `pose_update_gps` | pose | 一类·真跑 | OK（319 字符，data=有） |  | — |
| `pose_update_imu` | pose | 一类·真跑 | OK（309 字符，data=有） |  | — |
| `crc_check` | protocol | 一类·真跑 | OK（83 字符，data=有） |  | — |
| `field_extract` | protocol | 一类·真跑 | OK（8 字符，data=有） |  | — |
| `frame_layout_spec_load` | protocol | 一类·真跑 | OK（105 字符，data=有） |  | — |
| `morse_decode` | radio_control | 一类·真跑 | OK（54 字符，data=有） |  | — |
| `morse_encode` | radio_control | 一类·真跑 | OK（54 字符，data=有） |  | — |
| `radio_connect` | radio_control | 一类·真跑 | OK（189 字符，data=无） |  | — |
| `radio_list_ports` | radio_control | 一类·真跑 | OK（52 字符，data=无） |  | — |
| `radio_ptt` | radio_control | 一类·真跑 | OK（27 字符，data=无） |  | — |
| `radio_set_frequency` | radio_control | 一类·真跑 | OK（52 字符，data=无） |  | — |
| `radio_set_mode` | radio_control | 一类·真跑 | OK（28 字符，data=无） |  | — |
| `rds_decode_groups` | rds_decoder | 一类·真跑 | OK（74 字符，data=有） |  | — |
| `rds_extract_ps_rt` | rds_decoder | 一类·真跑 | OK（104 字符，data=有） |  | — |
| `r2cloud_recording_meta` | sat_groundstation | 一类·真跑 | OK（8 字符，data=有） |  | — |
| `r2cloud_schedule` | sat_groundstation | 一类·真跑 | OK（20 字符，data=有） |  | — |
| `satnogs_doppler_plan` | sat_groundstation | 一类·真跑 | OK（29 字符，data=有） |  | — |
| `satnogs_list_modes` | sat_groundstation | 一类·真跑 | OK（18 字符，data=有） |  | — |
| `sdrangel_firedetector` | sat_groundstation | 一类·真跑 | OK（9 字符，data=有） |  | — |
| `sdrangel_remotecontrol` | sat_groundstation | 一类·真跑 | OK（22 字符，data=有） |  | — |
| `frame_layout_list` | sat_profile | 一类·真跑 | OK（48 字符，data=有） |  | — |
| `frame_layout_load` | sat_profile | 一类·真跑 | OK（786 字符，data=有） |  | — |
| `sat_profile_list` | sat_profile | 一类·真跑 | OK（98 字符，data=有） |  | — |
| `sat_profile_load` | sat_profile | 一类·真跑 | OK（729 字符，data=有） |  | — |
| `array_correlator_df` | satellite | 一类·真跑 | OK（255 字符，data=有） |  | — |
| `df_amplitude_compare` | satellite | 一类·真跑 | OK（194 字符，data=有） |  | — |
| `doppler_calc` | satellite | 一类·真跑 | OK（243 字符，data=有） |  | — |
| `fy3_hrpt_decode` | satellite | 一类·真跑 | OK（22 字符，data=有） |  | — |
| `fy3_pass_predict` | satellite | 一类·真跑 | OK（43 字符，data=有） |  | — |
| `gps_prn_sequence` | satellite | 一类·真跑 | OK（382 字符，data=有） |  | — |
| `interferometry_resolution` | satellite | 一类·真跑 | OK（391 字符，data=有） |  | — |
| `lro_doppler_predict` | satellite | 一类·真跑 | OK（222 字符，data=无） |  | — |
| `lro_od_demo` | satellite | 一类·真跑 | OK（238 字符，data=无） |  | — |
| `lro_orbit_info` | satellite | 一类·真跑 | OK（282 字符，data=无） |  | — |
| `lrpt_decode_full` | satellite | 一类·真跑 | OK（7 字符，data=有） |  | — |
| `meteor_demod_setup` | satellite | 一类·真跑 | OK（284 字符，data=无） |  | — |
| `meteor_get_params` | satellite | 一类·真跑 | OK（185 字符，data=无） |  | — |
| `meteor_list_satellites` | satellite | 一类·真跑 | OK（1780 字符，data=无） |  | — |
| `multistation_tdoa_fix` | satellite | 一类·真跑 | OK（673 字符，data=有） |  | — |
| `pcm_psk_pm_modulate` | satellite | 一类·真跑 | OK（38 字符，data=有） |  | — |
| `sat_compose_falsecolor` | satellite | 一类·真跑 | OK（26 字符，data=有） |  | — |
| `sat_image_enhance` | satellite | 一类·真跑 | OK（67 字符，data=有） |  | — |
| `sat_link_get` | satellite | 一类·真跑 | OK（356 字符，data=有） |  | — |
| `sat_link_identify` | satellite | 一类·真跑 | OK（51 字符，data=有） |  | — |
| `sat_link_list` | satellite | 一类·真跑 | OK（91 字符，data=有） |  | — |
| `sat_link_tuning` | satellite | 一类·真跑 | OK（29 字符，data=有） |  | — |
| `sat_pass_predict` | satellite | 一类·真跑 | OK（2152 字符，data=有） |  | — |
| `sat_project_image` | satellite | 一类·真跑 | OK（25 字符，data=有） |  | — |
| `satdump_check` | satellite | 一类·真跑 | OK（202 字符，data=无） |  | — |
| `satdump_list_sats` | satellite | 一类·真跑 | OK（696 字符，data=无） |  | — |
| `sgp4_propagate` | satellite | 一类·真跑 | OK（235 字符，data=有） |  | — |
| `tc_build_frame` | satellite | 一类·真跑 | OK（31 字符，data=有） |  | — |
| `tc_frame_parse` | satellite | 一类·真跑 | OK（27 字符，data=有） |  | — |
| `tle_parse` | satellite | 一类·真跑 | OK（401 字符，data=有） |  | — |
| `scheduler_add` | scheduler | 一类·真跑 | OK（38 字符，data=无） |  | — |
| `scheduler_disable` | scheduler | 一类·真跑 | OK（5 字符，data=无） |  | — |
| `scheduler_enable` | scheduler | 一类·真跑 | OK（5 字符，data=无） |  | — |
| `scheduler_list` | scheduler | 一类·真跑 | OK（4024 字符，data=有） |  | — |
| `scheduler_status` | scheduler | 一类·真跑 | OK（4023 字符，data=无） |  | — |
| `scheduler_tick` | scheduler | 一类·真跑 | OK（11 字符，data=无） |  | — |
| `bladerf_get_freq_range` | sdr | 一类·真跑 | OK（291 字符，data=有） |  | — |
| `bladerf_list_gains` | sdr | 一类·真跑 | OK（3312 字符，data=有） |  | — |
| `bladerf_set_params` | sdr | 一类·真跑 | OK（949 字符，data=有） |  | — |
| `hackrf_get_freq_range` | sdr | 一类·真跑 | OK（280 字符，data=有） |  | — |
| `hackrf_list_gains` | sdr | 一类·真跑 | OK（1829 字符，data=有） |  | — |
| `hackrf_set_params` | sdr | 一类·真跑 | OK（270 字符，data=有） |  | — |
| `limesdr_get_freq_range` | sdr | 一类·真跑 | OK（492 字符，data=有） |  | — |
| `limesdr_list_gains` | sdr | 一类·真跑 | OK（1700 字符，data=有） |  | — |
| `limesdr_set_params` | sdr | 一类·真跑 | OK（383 字符，data=有） |  | — |
| `osmosdr_create_source` | sdr | 一类·真跑 | OK（329 字符，data=有） |  | — |
| `osmosdr_get_gain_ranges` | sdr | 一类·真跑 | OK（96 字符，data=有） |  | — |
| `osmosdr_list_devices` | sdr | 一类·真跑 | OK（139 字符，data=有） |  | — |
| `rtlsdr_get_freq_range` | sdr | 一类·真跑 | OK（353 字符，data=有） |  | — |
| `rtlsdr_list_gains` | sdr | 一类·真跑 | OK（617 字符，data=有） |  | — |
| `rtlsdr_set_params` | sdr | 一类·真跑 | OK（580 字符，data=有） |  | — |
| `openapi_list_apis` | sdr_ai | 一类·真跑 | OK（309 字符，data=无） |  | — |
| `energy_sense` | sdr_analysis | 一类·真跑 | OK（164 字符，data=无） |  | — |
| `estimate_ppm_offset` | sdr_analysis | 一类·真跑 | OK（226 字符，data=有） |  | — |
| `sdr_analyze_recording` | sdr_analysis | 一类·真跑 | OK（473 字符，data=无） |  | — |
| `sdr_cfo_correct` | sdr_analysis | 一类·真跑 | OK（284 字符，data=无） |  | — |
| `signal_extract_features` | sdr_analysis | 一类·真跑 | OK（131 字符，data=无） |  | — |
| `signal_identify_modulation` | sdr_analysis | 一类·真跑 | OK（144 字符，data=无） |  | — |
| `meteor_lrpt_demod` | sdr_decode | 一类·真跑 | OK（68 字符，data=有） |  | — |
| `meteor_viterbi_decode` | sdr_decode | 一类·真跑 | OK（131 字符，data=有） |  | — |
| `sdr_decode_adsb` | sdr_decode | 一类·真跑 | OK（33 字符，data=无） |  | — |
| `sdr_decode_aprs` | sdr_decode | 一类·真跑 | OK（282 字符，data=无） |  | — |
| `sdr_decode_cw` | sdr_decode | 一类·真跑 | OK（41 字符，data=无） |  | — |
| `sdr_decode_ft8` | sdr_decode | 一类·真跑 | OK（131 字符，data=无） |  | — |
| `sdr_decode_sstv` | sdr_decode | 一类·真跑 | OK（106 字符，data=无） |  | — |
| `gqrx_receiver_create` | sdr_demod | 一类·真跑 | OK（216 字符，data=有） |  | — |
| `hdsdr_bandwidth_preset` | sdr_demod | 一类·真跑 | OK（167 字符，data=有） |  | — |
| `sdr_play_audio` | sdr_demod | 一类·真跑 | OK（66 字符，data=无） |  | — |
| `sdr_bookmark_add` | sdr_device | 一类·真跑 | OK（35 字符，data=无） |  | — |
| `sdr_bookmark_list` | sdr_device | 一类·真跑 | OK（4023 字符，data=无） |  | — |
| `sdr_disconnect` | sdr_device | 一类·真跑 | OK（8 字符，data=无） |  | — |
| `sdr_fm_preset` | sdr_device | 一类·真跑 | OK（83 字符，data=有） |  | — |
| `sdr_list_devices` | sdr_device | 一类·真跑 | OK（2 字符，data=无） |  | — |
| `sdr_open_iq_file` | sdr_device | 一类·真跑 | OK（118 字符，data=无） |  | — |
| `sdr_status` | sdr_device | 一类·真跑 | OK（8 字符，data=无） |  | — |
| `agc_process` | sdr_dsp | 一类·真跑 | OK（134 字符，data=有） |  | — |
| `iq_correct` | sdr_dsp | 一类·真跑 | OK（214 字符，data=有） |  | — |
| `owrx_fft_average` | sdr_dsp | 一类·真跑 | OK（177 字符，data=有） |  | — |
| `owrx_plan_fft` | sdr_dsp | 一类·真跑 | OK（223 字符，data=有） |  | — |
| `sdrsharp_make_window` | sdr_dsp | 一类·真跑 | OK（224 字符，data=有） |  | — |
| `sdrsharp_spectrum_fft` | sdr_dsp | 一类·真跑 | OK（171 字符，data=有） |  | — |
| `vfo_add` | sdr_frequency | 一类·真跑 | OK（60 字符，data=无） |  | — |
| `vfo_list` | sdr_frequency | 一类·真跑 | OK（138 字符，data=无） |  | — |
| `vfo_remove` | sdr_frequency | 一类·真跑 | OK（14 字符，data=无） |  | — |
| `vfo_select` | sdr_frequency | 一类·真跑 | OK（63 字符，data=无） |  | — |
| `sdr_get_gps` | sdr_location | 一类·真跑 | OK（29 字符，data=无） |  | — |
| `sdr_aprs_decode` | sdr_network | 一类·真跑 | OK（114 字符，data=有） |  | — |
| `sdr_aprs_encode` | sdr_network | 一类·真跑 | OK（224 字符，data=有） |  | — |
| `sdr_aprs_send_position` | sdr_network | 一类·真跑 | OK（353 字符，data=无） |  | — |
| `sdr_decode_ax25` | sdr_network | 一类·真跑 | OK（113 字符，data=无） |  | — |
| `sdr_digipeater_process` | sdr_network | 一类·真跑 | OK（100 字符，data=无） |  | — |
| `sdr_encode_ax25` | sdr_network | 一类·真跑 | OK（142 字符，data=有） |  | — |
| `sdr_kiss_decode` | sdr_network | 一类·真跑 | OK（46 字符，data=有） |  | — |
| `sdr_kiss_encode` | sdr_network | 一类·真跑 | OK（183 字符，data=有） |  | — |
| `protocol_stack_decode` | sdr_protocol | 一类·真跑 | OK（176 字符，data=有） |  | — |
| `protocol_stack_list` | sdr_protocol | 一类·真跑 | OK（186 字符，data=有） |  | — |
| `protocol_stack_split_frames` | sdr_protocol | 一类·真跑 | OK（565 字符，data=有） |  | — |
| `urh_decode_iq` | sdr_protocol | 一类·真跑 | OK（887 字符，data=有） |  | — |
| `urh_demodulate` | sdr_protocol | 一类·真跑 | OK（213 字符，data=有） |  | — |
| `urh_modulate` | sdr_protocol | 一类·真跑 | OK（141 字符，data=有） |  | — |
| `urh_sync_detect` | sdr_protocol | 一类·真跑 | OK（77 字符，data=有） |  | — |
| `sdr_record_stop` | sdr_record | 一类·真跑 | OK（9 字符，data=无） |  | — |
| `sdr_recordings_list` | sdr_record | 一类·真跑 | OK（178 字符，data=有） |  | — |
| `sdr_satellite_doppler` | sdr_satellite | 一类·真跑 | OK（174 字符，data=有） |  | — |
| `sdr_satellite_passes` | sdr_satellite | 一类·真跑 | OK（110 字符，data=无） |  | — |
| `sdr_satellite_sky_view` | sdr_satellite | 一类·真跑 | OK（205 字符，data=无） |  | — |
| `inspectrum_measure_bandwidth` | sdr_spectrum | 一类·真跑 | OK（153 字符，data=有） |  | — |
| `inspectrum_measure_duty_cycle` | sdr_spectrum | 一类·真跑 | OK（129 字符，data=有） |  | — |
| `inspectrum_measure_period` | sdr_spectrum | 一类·真跑 | OK（199 字符，data=有） |  | — |
| `sdr_spectrum_pan` | sdr_spectrum | 一类·真跑 | OK（13 字符，data=无） |  | — |
| `sdr_spectrum_zoom` | sdr_spectrum | 一类·真跑 | OK（10 字符，data=无） |  | — |
| `sigutils_detect_channels` | sdr_spectrum | 一类·真跑 | OK（131 字符，data=有） |  | — |
| `sigutils_identify_modulation` | sdr_spectrum | 一类·真跑 | OK（250 字符，data=有） |  | — |
| `sigutils_peak_detector` | sdr_spectrum | 一类·真跑 | OK（74 字符，data=有） |  | — |
| `cubicsdr_drag_tune` | sdr_ui | 一类·真跑 | OK（170 字符，data=有） |  | — |
| `cubicsdr_vfo_state_machine` | sdr_ui | 一类·真跑 | OK（177 字符，data=有） |  | — |
| `cubicsdr_waterfall_colormap` | sdr_ui | 一类·真跑 | OK（207 字符，data=有） |  | — |
| `hdsdr_gain_table` | sdr_ui | 一类·真跑 | OK（375 字符，data=有） |  | — |
| `hdsdr_rf_gain` | sdr_ui | 一类·真跑 | OK（178 字符，data=有） |  | — |
| `owrx_device_bandwidth_adapt` | sdr_ui | 一类·真跑 | OK（286 字符，data=有） |  | — |
| `owrx_waterfall_colormap` | sdr_ui | 一类·真跑 | OK（161 字符，data=有） |  | — |
| `sdrsharp_list_devices` | sdr_ui | 一类·真跑 | OK（1958 字符，data=有） |  | — |
| `sdrsharp_plugin_contract` | sdr_ui | 一类·真跑 | OK（424 字符，data=有） |  | — |
| `alpha_estimate` | signal_analysis | 一类·真跑 | OK（126 字符，data=有） |  | — |
| `dehop_demod` | signal_analysis | 一类·真跑 | OK（249 字符，data=有） |  | — |
| `fhss_hop_track` | signal_analysis | 一类·真跑 | OK（168 字符，data=有） |  | — |
| `interference_assess` | signal_analysis | 一类·真跑 | OK（404 字符，data=有） |  | — |
| `jammer_generate` | signal_analysis | 一类·真跑 | OK（210 字符，data=有） |  | — |
| `modulation_classify` | signal_analysis | 一类·真跑 | OK（422 字符，data=有） |  | — |
| `symbol_rate_estimate` | signal_analysis | 一类·真跑 | OK（65 字符，data=有） |  | — |
| `skill_list` | skill | 一类·真跑 | OK（859 字符，data=有） |  | — |
| `skill_load` | skill | 一类·真跑 | OK（600 字符，data=无） |  | — |
| `sky_capability_report` | sky | 一类·真跑 | OK（651 字符，data=有） |  | — |
| `sky_capability_state` | sky | 一类·真跑 | OK（655 字符，data=有） |  | — |
| `constellation_evm` | spectrum | 一类·真跑 | OK（105 字符，data=有） |  | — |
| `sdr_anr_denoise` | spectrum | 一类·真跑 | OK（4025 字符，data=有） |  | — |
| `sdr_constellation_scatter` | spectrum | 一类·真跑 | OK（58 字符，data=有） |  | — |
| `sdr_signal_overview` | spectrum | 一类·真跑 | OK（462 字符，data=有） |  | — |
| `sdr_spectrum_peaks` | spectrum | 一类·真跑 | OK（95 字符，data=有） |  | — |
| `sdr_squelch_gate` | spectrum | 一类·真跑 | OK（122 字符，data=有） |  | — |
| `signal_quality_stats` | spectrum | 一类·真跑 | OK（162 字符，data=有） |  | — |
| `spectrum_analyze_iq` | spectrum | 一类·真跑 | OK（225 字符，data=有） |  | — |
| `subagent_create` | subagent | 一类·真跑 | OK（49 字符，data=无） |  | — |
| `subagent_list` | subagent | 一类·真跑 | OK（668 字符，data=有） |  | — |
| `subagent_stats` | subagent | 一类·真跑 | OK（192 字符，data=有） |  | — |
| `costas_track` | sync | 一类·真跑 | OK（126 字符，data=有） |  | — |
| `fll_track` | sync | 一类·真跑 | OK（44 字符，data=有） |  | — |
| `symbol_sync_recover` | sync | 一类·真跑 | OK（36 字符，data=有） |  | — |
| `golden_assert` | testing | 一类·真跑 | OK（141 字符，data=有） |  | — |
| `vna_analyze` | vna | 一类·真跑 | OK（1875 字符，data=有） |  | — |
| `codec2_decode` | voice | 一类·真跑 | OK（43 字符，data=有） |  | — |
| `codec2_encode` | voice | 一类·真跑 | OK（50 字符，data=有） |  | — |
| `freedv_demodulate` | voice | 一类·真跑 | OK（66 字符，data=有） |  | — |
| `freedv_modulate` | voice | 一类·真跑 | OK（51 字符，data=有） |  | — |
| `kismet_device_list` | wireless | 一类·真跑 | OK（413 字符，data=有） |  | — |
| `kismet_discover_devices` | wireless | 一类·真跑 | OK（413 字符，data=有） |  | — |
| `kismet_parse_dot11` | wireless | 一类·真跑 | OK（215 字符，data=有） |  | — |
| `workflow_execute` | workflow | 一类·真跑 | OK（252 字符，data=有） |  | — |
| `workflow_list` | workflow | 一类·真跑 | OK（2 字符，data=无） |  | — |
| `workflow_record_start` | workflow_recorder | 一类·真跑 | OK（38 字符，data=无） |  | — |
| `workflow_record_stop` | workflow_recorder | 一类·真跑 | OK（257 字符，data=有） |  | — |
| `workflow_recorder_status` | workflow_recorder | 一类·真跑 | OK（146 字符，data=有） |  | — |
| `workflow_template_create` | workflow_recorder | 一类·真跑 | OK（230 字符，data=有） |  | — |
| `workflow_template_list` | workflow_recorder | 一类·真跑 | OK（1617 字符，data=有） |  | — |
