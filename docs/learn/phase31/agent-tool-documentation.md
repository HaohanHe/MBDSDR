# Agent 工具能力清单（自动生成，58 个工具）
固定字段：name / description / JSON Schema / read-write 标记 / 错误示例。参数可能是非法 JSON 或幻觉字段，调用前由 runtime 校验器拒绝并以 role=tool 回注自纠。

## tune_frequency  [write 写(手动模式拦截)]
description: Tune the receiver to a center frequency in Hz.
schema: {"properties":{"freq_hz":{"description":"Center frequency in Hz, e.g. 98500000 for 98.5 MHz","maximum":1700000000,"minimum":24000000,"type":"number"}},"required":["freq_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 tune_frequency"}（不自动重试写动作）

## set_mode  [write 写(手动模式拦截)]
description: Set demodulation mode.
schema: {"properties":{"mode":{"enum":["AM","NFM","WFM","USB","LSB","CW"],"type":"string"}},"required":["mode"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_mode"}（不自动重试写动作）

## start_recording  [write 写(手动模式拦截)]
description: Start recording raw IQ to SigMF file.
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 start_recording"}（不自动重试写动作）

## stop_recording  [write 写(手动模式拦截)]
description: Stop recording.
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 stop_recording"}（不自动重试写动作）

## scan_band  [write 写(手动模式拦截)]
description: Scan a frequency band and return the peak signal.
schema: {"properties":{"high_hz":{"description":"End frequency Hz","maximum":1700000000,"minimum":24000000,"type":"number"},"low_hz":{"description":"Start frequency Hz","maximum":1700000000,"minimum":24000000,"type":"number"},"step_hz":{"description":"Step size Hz (default 200k)","minimum":1,"type":"number"}},"required":["low_hz","high_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 scan_band"}（不自动重试写动作）

## set_bandwidth  [write 写(手动模式拦截)]
description: Set channel filter bandwidth in Hz.
schema: {"properties":{"bandwidth_hz":{"description":"Filter bandwidth in Hz, e.g. 8000 for AM, 12500 for NFM, 200000 for WFM","enum":[9000,12500,2400,500,12000,200000,2000000],"type":"number"}},"required":["bandwidth_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_bandwidth"}（不自动重试写动作）

## get_status  [read 只读]
description: Return current receiver state: frequency, mode, bandwidth, sample rate.
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## predict_passes  [read 只读]
description: 只读：用本地新鲜 TLE 缓存预测指定卫星未来的过境（升/降时刻、最高仰角、起止方位）。无新鲜 TLE 时诚实返回空态，不使用陈旧内置数据。
schema: {"properties":{"hours_ahead":{"description":"预测窗口小时数，默认 24","maximum":168,"minimum":1,"type":"number"},"satellite_name":{"description":"卫星名，大小写不敏感子串匹配，如 NOAA / ISS","type":"string"},"station_lat_deg":{"description":"本站纬度（度，-90..90）","type":"number"},"station_lon_deg":{"description":"本站经度（度，-180..180）","type":"number"}},"required":["satellite_name"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## calibrate_frequency  [read 只读]
description: 只读测量：用一段已知精确频率的参考信号估计本机晶振 ppm 误差。不修改任何设置。参考源：handheld=手台在已知频点按 PTT 发射；gsm_fcch=GSM FCCH 精确纯音；manual=任意已知精确频率。未检测到参考载波时诚实返回 detected=false，不编造 ppm。
schema: {"properties":{"reference_freq_hz":{"description":"参考频率 Hz：handheld/manual 为已知精确频率；gsm_fcch 为 ARFCN 下行中心频率","maximum":1700000000,"minimum":24000000,"type":"number"},"reference_type":{"description":"参考源类型","enum":["handheld","gsm_fcch","manual"],"type":"string"},"sample_count":{"description":"采集复样本数，默认 32768（4 段独立测量）","minimum":4096,"type":"number"}},"required":["reference_freq_hz","reference_type"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## apply_frequency_correction  [write 写(手动模式拦截)]
description: 写入并应用频率校正 ppm（通常取 calibrate_frequency 的 measured_ppm）。会保存到设置并下发给接收机；属于写动作，手动模式下被拦截。
schema: {"properties":{"ppm":{"description":"要应用的 ppm 校正值（如 32.0）","maximum":100,"minimum":-100,"type":"number"},"reference_freq_hz":{"description":"可选：测量时所用参考频率，仅用于出处/前后对比","maximum":1700000000,"minimum":24000000,"type":"number"}},"required":["ppm"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 apply_frequency_correction"}（不自动重试写动作）

## get_pocsag_messages  [read 只读]
description: 只读：读取指定（默认当前选中）信道已解码的 POCSAG 寻呼消息快照（地址 RIC/功能位/文本）。无解码结果时诚实返回空列表，不编造消息。
schema: {"properties":{"channel_id":{"description":"可选：信道 VFO id；键名 channel_id（亦可接受 channel）；缺省为当前选中信道","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_m17_calls  [read 只读]
description: 只读：读取指定（默认当前选中）信道已解码的 M17 呼叫/帧快照（源/目的呼号、类型、CRC 状态、语音帧诚实标注未解码）。无解码结果时诚实返回空列表。
schema: {"properties":{"channel_id":{"description":"可选：信道 VFO id；键名 channel_id（亦可接受 channel）；缺省为当前选中信道","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_vor_radial  [read 只读]
description: 只读：读取指定（默认当前选中）VOR 信道最新径向读数（radialDeg 方位、质量、莫尔斯识别码、锁定态）。未锁定时 locked=false，方位不可信并被显式标注，不编造方位。
schema: {"properties":{"channel_id":{"description":"可选：信道 VFO id；键名 channel_id（亦可接受 channel）；缺省为当前选中信道","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_acars_packets  [read 只读]
description: 只读：读取指定（默认当前选中）信道已解码的 ACARS 航空报文快照（方向 air/ground、label、block id、ack、正文、CRC 结果）。无解码结果时诚实返回空列表，不编造报文。
schema: {"properties":{"channel_id":{"description":"可选：信道 VFO id；键名 channel_id（亦可接受 channel）；缺省为当前选中信道","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_navtex_messages  [read 只读]
description: 只读：读取指定（默认当前选中）信道已解码的 NAVTEX 海上安全报文快照（发台 B1、类型 B2、编号、正文、时间分集/定相状态）。无解码结果时诚实返回空列表，不编造报文。
schema: {"properties":{"channel_id":{"description":"可选：信道 VFO id；键名 channel_id（亦可接受 channel）；缺省为当前选中信道","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## export_iq_segment  [write 写(手动模式拦截)]
description: 写入（一次性）：立即抓取一段当前中心频率的基带 IQ 复样本并导出为 cf32_le SigMF 文件（.sigmf-data + .sigmf-meta），返回真实路径与样本数。区别于 start_recording 的连续录制：这是按需导出一个有界窗口后即返回。无 IQ 数据时诚实报错，不生成空文件。属于写动作，手动模式下被拦截。
schema: {"properties":{"sample_count":{"description":"导出复样本数，默认 65536","minimum":1024,"type":"number"},"tune_hz":{"description":"可选：先调谐到该 Hz 再导出；缺省/负数=保持当前中心频率","maximum":1700000000,"minimum":24000000,"type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 export_iq_segment"}（不自动重试写动作）

## set_network_audio_sink  [write 写(手动模式拦截)]
description: 写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。enable 开关、port 端口、format 协议；可选 host 与 stereo。属于写动作，手动模式下被拦截。
schema: {"properties":{"enable":{"description":"是否开启网络音频流","type":"boolean"},"format":{"description":"协议：udp 或 tcp（默认 udp）","type":"string"},"host":{"description":"绑定/对端主机（默认 127.0.0.1）","type":"string"},"port":{"description":"网络音频端口号 (1..65535)","type":"number"},"stereo":{"description":"立体声（默认 false=单声道）","type":"boolean"}},"required":["enable","port"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_network_audio_sink"}（不自动重试写动作）

## get_network_audio_status  [read 只读]
description: 只读：返回网络音频流状态（是否使能、端口、格式）。无状态时诚实返回 enabled=false，不编造端口。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## start_scan_link  [write 写(手动模式拦截)]
description: 写入：启动扫描活动链路（扫描→命中→驻留→解码→录制），目标频率 target_freq_hz。属于写动作，手动模式下被拦截。
schema: {"properties":{"target_freq_hz":{"description":"扫描目标中心频率 Hz","maximum":1700000000,"minimum":24000000,"type":"number"}},"required":["target_freq_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 start_scan_link"}（不自动重试写动作）

## stop_scan_link  [write 写(手动模式拦截)]
description: 写入：停止扫描活动链路。属于写动作，手动模式下被拦截。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 stop_scan_link"}（不自动重试写动作）

## get_scan_link_status  [read 只读]
description: 只读：返回扫描活动链路状态（scanning/dwelling/hit）。未运行时诚实返回 scanning=false、无命中，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_squelch  [write 写(手动模式拦截)]
description: 写入：设置静噪（enabled 开关、threshold_db 门限、auto 自动链路）。属于写动作，手动模式下被拦截。
schema: {"properties":{"auto":{"description":"是否启用自动静噪","type":"boolean"},"enabled":{"description":"是否开启静噪","type":"boolean"},"threshold_db":{"description":"静噪门限 dB","type":"number"}},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_squelch"}（不自动重试写动作）

## get_squelch_status  [read 只读]
description: 只读：返回静噪状态（enabled/threshold_db/auto/当前是否 open）。无实时门限读数时诚实标注，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_ctcss  [write 写(手动模式拦截)]
description: 写入：设置 CTCSS 亚音（enabled 开关、frequency_hz 亚音频率 67.0–254.1 Hz、可选 gate_audio 亚音门控静音开关）。属于写动作，手动模式下被拦截；越界频率诚实拒绝，不静默钳位。
schema: {"properties":{"enabled":{"description":"是否开启 CTCSS 亚音检测","type":"boolean"},"frequency_hz":{"description":"CTCSS 亚音频率 Hz（67.0–254.1，缺省沿用当前/默认 88.5）","maximum":254.1,"minimum":67,"type":"number"},"gate_audio":{"description":"可选：是否开启亚音门控静音（开启后仅在检测到匹配亚音时才放音，录制不受影响；缺省不改动当前门控）","type":"boolean"}},"required":["enabled"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_ctcss"}（不自动重试写动作）

## get_ctcss_status  [read 只读]
description: 只读：返回 CTCSS 亚音状态（enabled 是否使能、frequency_hz 调谐频率、active 是否真实检测到亚音、gate_audio 是否开启亚音门控静音）。无信号/未使能时 active 诚实为 false，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_cdcss  [write 写(手动模式拦截)]
description: 写入：设置 CDCSS/DCS 数字亚音（enabled 开关、code 三位八进制 DCS 码 "023"–"754"、可选 gate_audio 数字亚音门控静音开关）。属于写动作，手动模式下被拦截；非表内 DCS 码诚实拒绝，不静默接受。
schema: {"properties":{"code":{"description":"三位八进制 DCS 码字符串（如 \"023\"，须在公开 104 码表内；缺省沿用当前码）","type":"string"},"enabled":{"description":"是否开启 CDCSS/DCS 数字亚音检测","type":"boolean"},"gate_audio":{"description":"可选：是否开启数字亚音门控静音（开启后仅在检测到匹配 DCS 码时才放音，录制不受影响；缺省不改动当前门控）","type":"boolean"}},"required":["enabled"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_cdcss"}（不自动重试写动作）

## get_cdcss_status  [read 只读]
description: 只读：返回 CDCSS/DCS 数字亚音状态（enabled 是否使能、code 调谐 DCS 码、active 是否真实检测到匹配码、gate_audio 是否开启数字亚音门控静音）。无信号/未使能时 active 诚实为 false，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_ft8  [write 写(手动模式拦截)]
description: 写入：开关 FT8 数字模式检测层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。本轮为检测层（Costas 同步 + 候选帧统计），C++ BP 解码留后续，不编造解码消息。
schema: {"properties":{"enabled":{"description":"是否开启 FT8 检测层","type":"boolean"}},"required":["enabled"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_ft8"}（不自动重试写动作）

## get_ft8_status  [read 只读]
description: 只读：返回 FT8 检测层状态（enabled 是否使能、active 是否真实检出候选帧、freq_offset_hz 估计频偏、sync_quality 相关峰/次峰比、candidate_count 候选数）。无信号/未使能时 active 诚实为 false，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_lrpt  [write 写(手动模式拦截)]
description: 写入：开关 LRPT 卫星云图接收层（enabled 布尔，必填）。属于写动作，手动模式下被拦截。本轮为状态控制层（C++ 解调/FEC 移植留后续第④轮），不编造图像或帧。
schema: {"properties":{"enabled":{"description":"是否开启 LRPT 卫星云图接收","type":"boolean"}},"required":["enabled"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_lrpt"}（不自动重试写动作）

## get_lrpt_status  [read 只读]
description: 只读：返回 LRPT 接收层状态（enabled 是否使能、sync_locked 是否已锁定位同步、decoded_frames 已解码行数）。C++ 解码器未移植前 sync_locked 诚实为 false、decoded_frames 为 0，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_noise_blanker  [write 写(手动模式拦截)]
description: 写入：开关噪声抑制器（on 布尔）。属于写动作，手动模式下被拦截。
schema: {"properties":{"on":{"description":"是否开启噪声抑制","type":"boolean"}},"required":["on"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_noise_blanker"}（不自动重试写动作）

## get_noise_blanker_status  [read 只读]
description: 只读：返回噪声抑制器状态（enabled 是否使能）。读取引擎真实开关，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## list_bookmarks  [read 只读]
description: 只读：列出书签（频率/名称/模式）。无书签时诚实返回空列表，不编造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## add_bookmark  [write 写(手动模式拦截)]
description: 写入：添加书签（freq_hz/name/mode/bandwidth_hz/group）。属于写动作，手动模式下被拦截。
schema: {"properties":{"bandwidth_hz":{"description":"书签带宽 Hz（可选）","type":"number"},"freq_hz":{"description":"书签频率 Hz","maximum":1700000000,"minimum":24000000,"type":"number"},"group":{"description":"书签分组名（可选）","type":"string"},"mode":{"description":"解调模式，如 NFM/AM","type":"string"},"name":{"description":"书签名称","type":"string"}},"required":["freq_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 add_bookmark"}（不自动重试写动作）

## tune_to_bookmark  [write 写(手动模式拦截)]
description: 写入：调谐到指定下标书签的频率。属于写动作，手动模式下被拦截。
schema: {"properties":{"index":{"description":"书签下标（从 0 开始）","type":"number"}},"required":["index"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 tune_to_bookmark"}（不自动重试写动作）

## delete_bookmark  [write 写(手动模式拦截)]
description: 写入：删除指定下标书签。属于写动作，手动模式下被拦截。
schema: {"properties":{"index":{"description":"书签下标（从 0 开始）","type":"number"}},"required":["index"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 delete_bookmark"}（不自动重试写动作）

## list_vfos  [read 只读]
description: 只读：列出全部 VFO 信道（id/频率/带宽/模式/选中态）。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## add_vfo  [write 写(手动模式拦截)]
description: 写入：新增一个 VFO 信道。属于写动作，手动模式下被拦截。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 add_vfo"}（不自动重试写动作）

## switch_vfo  [write 写(手动模式拦截)]
description: 写入：切换选中的 VFO 信道（index）。属于写动作，手动模式下被拦截。
schema: {"properties":{"index":{"description":"VFO id","type":"number"}},"required":["index"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 switch_vfo"}（不自动重试写动作）

## rename_vfo  [write 写(手动模式拦截)]
description: 写入：重命名指定 VFO 信道（index/name）。属于写动作，手动模式下被拦截。
schema: {"properties":{"index":{"description":"VFO id","type":"number"},"name":{"description":"新名称","type":"string"}},"required":["index","name"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 rename_vfo"}（不自动重试写动作）

## set_vfo_armed  [write 写(手动模式拦截)]
description: 写入：开启/关闭某 VFO 的后台并行解调（index/enabled）。开启后即使该 VFO 未被选中，仍会在每个数据块被解调，便于并行监听/录制；关闭则停止以节省 CPU。属于写动作，手动模式下被拦截。
schema: {"properties":{"enabled":{"description":"true=后台并行监听，false=停止","type":"boolean"},"index":{"description":"VFO 序号（list_vfos 返回顺序）","type":"number"}},"required":["index","enabled"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_vfo_armed"}（不自动重试写动作）

## set_vfo_frequency  [write 写(手动模式拦截)]
description: 写入：把指定 VFO 调谐到新频率（index 或 id 二选一 + freq_hz）。属于写动作，手动模式下被拦截。
schema: {"properties":{"freq_hz":{"description":"目标频率（Hz）","type":"number"},"index":{"description":"VFO 序号（list_vfos 返回顺序；亦可改用 id 直传 VFO id）","type":"number"}},"required":["index","freq_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_vfo_frequency"}（不自动重试写动作）

## set_vfo_mode  [write 写(手动模式拦截)]
description: 写入：切换指定 VFO 的解调模式（index 或 id 二选一 + mode，mode 取值见 ControlHub 模式表：AM/NFM/WFM/USB/LSB/CW/POCSAG/m17/VOR/ACARS/NAVTEX）。属于写动作，手动模式下被拦截。
schema: {"properties":{"index":{"description":"VFO 序号（list_vfos 返回顺序；亦可改用 id 直传 VFO id）","type":"number"},"mode":{"description":"解调模式（大小写不敏感）","type":"string"}},"required":["index","mode"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_vfo_mode"}（不自动重试写动作）

## set_vfo_bandwidth  [write 写(手动模式拦截)]
description: 写入：设置指定 VFO 的信道带宽（index 或 id 二选一 + bandwidth_hz）。属于写动作，手动模式下被拦截。
schema: {"properties":{"bandwidth_hz":{"description":"信道带宽（Hz）","type":"number"},"index":{"description":"VFO 序号（list_vfos 返回顺序；亦可改用 id 直传 VFO id）","type":"number"}},"required":["index","bandwidth_hz"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_vfo_bandwidth"}（不自动重试写动作）

## list_recordings  [read 只读]
description: 只读：扫描录制目录并列出已有录制文件。目录不存在或为空时诚实返回空列表。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## delete_recording  [write 写(手动模式拦截)]
description: 写入：删除录制目录下指定名称的文件（仅限录制目录内）。属于写动作，手动模式下被拦截。
schema: {"properties":{"name":{"description":"录制文件名（仅文件名，不得含路径）","type":"string"}},"required":["name"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 delete_recording"}（不自动重试写动作）

## export_recording  [write 写(手动模式拦截)]
description: 写入：把录制目录下指定文件复制导出到 out_path。属于写动作，手动模式下被拦截。
schema: {"properties":{"name":{"description":"录制文件名（仅文件名）","type":"string"},"out_path":{"description":"导出目标完整路径","type":"string"}},"required":["name","out_path"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 export_recording"}（不自动重试写动作）

## set_fft_params  [write 写(手动模式拦截)]
description: 写入：设置频谱 FFT 参数（fft_size/window/average）。window/average 接受字符串枚举（推荐）或原始整数 0/1/2。属于写动作，手动模式下被拦截。
schema: {"properties":{"average":{"description":"平均模式（亦接受整数 0=Off/1=Slow/2=Fast）","enum":["Off","Slow","Fast"],"type":"string"},"fft_size":{"description":"FFT 点数，如 1024/2048/4096/8192","maximum":65536,"minimum":256,"type":"number"},"window":{"description":"窗函数（亦接受整数 0=Hann/1=Flattop/2=Blackman）","enum":["Hann","Flattop","Blackman"],"type":"string"}},"required":["fft_size"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_fft_params"}（不自动重试写动作）

## set_color_map  [write 写(手动模式拦截)]
description: 写入：保存瀑布图色板文件路径到设置（headless 仅持久化偏好，重绘由 UI 持有）。属于写动作，手动模式下被拦截。
schema: {"properties":{"file_path":{"description":"色板文件路径","type":"string"}},"required":["file_path"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_color_map"}（不自动重试写动作）

## get_spectrum_status  [read 只读]
description: 只读：返回频谱当前参数（fft_size/window/average）真实值。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_doppler_compensation  [write 写(手动模式拦截)]
description: 写入：开关过境实时多普勒自动补偿（1Hz TLE 距离率重调 VFO）。需已设置本站位置并捕获一个过境，否则保持关闭（诚实拒绝）。属于写动作，手动模式下被拦截。
schema: {"properties":{"enable":{"description":"是否开启多普勒自动补偿","type":"boolean"}},"required":["enable"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 set_doppler_compensation"}（不自动重试写动作）

## connect_network_source  [write 写(手动模式拦截)]
description: 写入：连接 rtl_tcp 网络接收机（host/port）。真实 TCP 握手+RTL0 设备头；失败返回真实 socket 原因并回到空态，不伪造 IQ。属于写动作，手动模式下被拦截。
schema: {"properties":{"host":{"description":"rtl_tcp 服务端主机/IP","type":"string"},"port":{"description":"rtl_tcp 服务端端口（默认 1234）","type":"number"}},"required":["host"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；手动模式写门拒绝 -> {"ok":false,"gated":true,"error":"手动模式：未执行 connect_network_source"}（不自动重试写动作）

## get_capabilities  [read 只读]
description: 只读：返回当前源的真实能力（设备名、可调谐频率范围、采样率范围、离散增益档 gains_db）。未连接或测试信号源时诚实返回 connected=false、空增益档数组与来源说明 provenance，不编造调谐范围或增益表。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_recording_state  [read 只读]
description: 只读：返回录制状态（recording 是否在手动录制中、recording_path 当前路径、watch_enabled 值守录制是否使能、recording_dir 录制目录）。未录制时诚实返回 recording=false、空路径，不伪造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## set_vna_sweep  [write 写]
description: 写入：外置 NanoVNA(H/H4) 的扫频参数（start_hz/stop_hz/points）。参数先校验（stop>start、points>0）；未连接设备时诚实返回 connected=false、applied=false 且 note 说明"参数已校验但未下发"，不伪造数据。
schema: {"properties":{"start_hz":{"type":"number"},"stop_hz":{"type":"number"},"points":{"type":"number"}},"required":["start_hz","stop_hz","points"],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠；stop<=start 或 points<=0 -> {"ok":false,...}

## get_vna_data  [read 只读]
description: 只读：返回 NanoVNA 当前扫频的 frequencies(Hz) 与 S11 复数（s11_re/s11_im 平行数组）。未连接时诚实返回 connected=false、空数组，note 说明"数组为空"，不伪造曲线。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠

## get_vna_status  [read 只读]
description: 只读：返回 NanoVNA 连接/身份/校准状态（connected、model 板名、version 固件、cal 校准项数组、has_sweep、start_hz/stop_hz/points）。未连接时诚实返回 connected=false、空字段，note 说明，不伪造。
schema: {"properties":{},"required":[],"type":"object"}
错误示例: 参数非法/缺失/幻觉字段 -> {"ok":false,"reasons":[...]}，以 role=tool 回注自纠
