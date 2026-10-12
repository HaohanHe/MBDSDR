// ============================================================================
// AI 工具能力清单（只读参考目录）—— 对齐桌面 cpp/src/ai/tool_schema.cpp。
// ----------------------------------------------------------------------------
// 诚实性说明：
//   * 这是**只读参考目录**，不是"移动端现在能调这些工具"的声明。
//   * 桌面端（C++/Qt）注册了 77 个 Agent 工具；移动端 AiClient 实际接入的工具
//     由 lib/app/ai_tools.dart 的 buildRadioTools() 决定（当前为 10 个）。
//   * 本目录逐项给出 name / 一句话说明 / read|write 标记；其中与移动端当前工具
//     **同名**的条目会被 UI 标为「移动端已接入」，其余为桌面端能力，移动端未实现，
//     此处仅作只读对照，绝不冒充移动端已有。
//   * 数据为手写只读快照（与 tool_schema.cpp registeredToolSpecs 对齐）；不发请求、
//     不构造 AiTool、不执行任何动作。
// ============================================================================

import 'package:flutter/foundation.dart';

/// 单个工具的只读元数据。
@immutable
class ToolCatalogEntry {
  const ToolCatalogEntry({
    required this.name,
    required this.description,
    required this.write,
  });

  /// 工具名（与桌面 tool_schema.cpp 一致）。
  final String name;

  /// 一句话人读说明。
  final String description;

  /// true = 写动作（手动模式被拦截）；false = 只读测量/读取。
  final bool write;
}

/// 桌面端 77 个工具的只读目录（对齐 cpp/src/ai/tool_schema.cpp:registeredToolSpecs）。
/// 顺序与 write 标志逐字按 registeredToolSpecs() 注册顺序排列。
const List<ToolCatalogEntry> kDesktopToolCatalog = <ToolCatalogEntry>[
  // ---- 基础调谐 / 解调（注册序 #1-7）----
  ToolCatalogEntry(
      name: 'tune_frequency',
      description: '把接收机调到指定中心频率（Hz）。',
      write: true),
  ToolCatalogEntry(
      name: 'set_mode',
      description: '设置解调模式（AM/NFM/WFM/USB/LSB/CW）。',
      write: true),
  ToolCatalogEntry(
      name: 'start_recording',
      description: '开始录制原始 IQ 到 SigMF 文件。',
      write: true),
  ToolCatalogEntry(
      name: 'stop_recording',
      description: '停止录制。',
      write: true),
  ToolCatalogEntry(
      name: 'scan_band',
      description: '扫描一个频段并返回峰值信号。',
      write: true),
  ToolCatalogEntry(
      name: 'set_bandwidth',
      description: '设置信道滤波带宽（Hz，取命名档位）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_status',
      description: '读取当前接收机状态（频率/模式/带宽/采样率）。',
      write: false),
  // ---- 卫星 / 频率校准（#8-10）----
  ToolCatalogEntry(
      name: 'predict_passes',
      description: '只读：用本地新鲜 TLE 缓存预测卫星未来过境。',
      write: false),
  ToolCatalogEntry(
      name: 'calibrate_frequency',
      description: '只读测量：用已知精确参考信号估计本机晶振 ppm 误差。',
      write: false),
  ToolCatalogEntry(
      name: 'apply_frequency_correction',
      description: '写入并应用频率校正 ppm（保存到设置并下发接收机）。',
      write: true),
  // ---- 数字解码快照（只读，#11-15）----
  ToolCatalogEntry(
      name: 'get_pocsag_messages',
      description: '只读：读取已解码的 POCSAG 寻呼消息快照。',
      write: false),
  ToolCatalogEntry(
      name: 'get_m17_calls',
      description: '只读：读取已解码的 M17 呼叫/帧快照。',
      write: false),
  ToolCatalogEntry(
      name: 'get_vor_radial',
      description: '只读：读取 VOR 信道最新径向读数。',
      write: false),
  ToolCatalogEntry(
      name: 'get_acars_packets',
      description: '只读：读取已解码的 ACARS 航空报文快照。',
      write: false),
  ToolCatalogEntry(
      name: 'get_navtex_messages',
      description: '只读：读取已解码的 NAVTEX 海上安全报文快照。',
      write: false),
  // ---- IQ 导出（#16）----
  ToolCatalogEntry(
      name: 'export_iq_segment',
      description: '写入：即时抓取一段基带 IQ 导出为 cf32_le SigMF。',
      write: true),
  // ---- 网络音频 / 扫描活动链路（#17-21）----
  ToolCatalogEntry(
      name: 'set_network_audio_sink',
      description: '写入：配置网络音频流输出（UDP/TCP 镜像当前解调音频）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_network_audio_status',
      description: '只读：返回网络音频流状态（使能/端口/格式）。',
      write: false),
  ToolCatalogEntry(
      name: 'start_scan_link',
      description: '写入：启动扫描活动链路（扫描→命中→驻留→解码→录制）。',
      write: true),
  ToolCatalogEntry(
      name: 'stop_scan_link',
      description: '写入：停止扫描活动链路。',
      write: true),
  ToolCatalogEntry(
      name: 'get_scan_link_status',
      description: '只读：返回扫描活动链路状态（scanning/dwelling/hit）。',
      write: false),
  // ---- 静噪 / CTCSS 亚音 / 噪声抑制（#22-27）----
  ToolCatalogEntry(
      name: 'set_squelch',
      description: '写入：设置静噪（开关/门限/自动）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_squelch_status',
      description: '只读：返回静噪状态（开关/门限/open）。',
      write: false),
  ToolCatalogEntry(
      name: 'set_ctcss',
      description: '写入：设置 CTCSS 亚音（enabled 开关、frequency_hz 亚音频率 67.0–254.1 Hz）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_ctcss_status',
      description: '只读：返回 CTCSS 亚音状态（enabled/frequency_hz/active 是否检测到亚音）。',
      write: false),
  ToolCatalogEntry(
      name: 'set_cdcss',
      description: '写入：设置 CDCSS/DCS 数字亚音（enabled 开关、code 三位八进制 DCS 码 "023"–"754"、可选 gate_audio）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_cdcss_status',
      description: '只读：返回 CDCSS/DCS 数字亚音状态（enabled/code/active 是否检测到匹配 DCS 码）。',
      write: false),
  ToolCatalogEntry(
      name: 'set_ft8',
      description: '写入：开关 FT8 数字模式检测层（enabled 开关；检测层，C++ BP 解码留后续）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_ft8_status',
      description: '只读：返回 FT8 检测层状态（enabled/active 是否检出候选帧/freq_offset_hz/sync_quality）。无信号 active 诚实为 false。',
      write: false),
  ToolCatalogEntry(
      name: 'set_lrpt',
      description: '写入：开关 LRPT 卫星云图接收层（enabled 开关；状态控制层，C++ 解调/FEC 移植留后续第④轮，不编造图像）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_lrpt_status',
      description: '只读：返回 LRPT 接收层状态（enabled/sync_locked 是否锁定位同步/decoded_frames 行数）。C++ 解码器未移植前 sync_locked 诚实为 false、decoded_frames 为 0。',
      write: false),
  ToolCatalogEntry(
      name: 'set_vna_sweep',
      description: '写入：设置 NanoVNA 扫频范围（start_hz/stop_hz/points 必填，stop>start、points>0）。无设备时参数校验但不下发（applied=false）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_vna_data',
      description: '只读：返回当前扫频数据（frequencies、S11/S21 及派生 VSWR/回波损耗/阻抗）。无设备时各数组诚实为空。',
      write: false),
  ToolCatalogEntry(
      name: 'get_vna_status',
      description: '只读：返回 NanoVNA 状态（connected/model/version/cal/sweep）。未连接时 connected 诚实为 false。',
      write: false),
  ToolCatalogEntry(
      name: 'analyze_vna_resonance',
      description: '只读：对当前扫频 S11 做谐振分析（串联/并联谐振 fr、ESR、-3dB 带宽、有载 Q）。无设备/点数不足时 valid 诚实为 false。',
      write: false),
  ToolCatalogEntry(
      name: 'vna_tdr_cable',
      description: '写入：对当前扫频 S11 做 TDR 时域反射（velocity_factor 速度因子 0<vf<=1 必填），返回首反射峰距离与电缆长度。手动模式下被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'analyze_vna_filter',
      description: '只读：对当前扫频 S21 幅度做滤波器分析（自动识别 bandpass/bandstop/highpass/lowpass，-3dB 截止边/带宽/插损/阻带衰减）。无设备/平坦时 valid 诚实为 false。',
      write: false),
  ToolCatalogEntry(
      name: 'analyze_vna_equivalent',
      description: '只读：由当前扫频 S11 提取串联 RLC 等效电路（ESR=串联谐振点 |Z|，Q=fr/BW，L/C 谐振关系推出）。无设备/点数不足 valid 诚实为 false。',
      write: false),
  ToolCatalogEntry(
      name: 'analyze_vna_touchstone',
      description: '只读：解析 Touchstone s1p/s2p（content 或 path），返回频率表与 S11/S21 实虚部数据集。',
      write: false),
  ToolCatalogEntry(
      name: 'export_vna_snapshot',
      description: '写入：把当前 NanoVNA 扫频离屏渲染为 PNG 存到 path。手动模式下被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'export_spectrum_snapshot',
      description: '写入：把当前 SDR 频谱面板（频率轴+中心标记）离屏渲染为 PNG 存到 path。手动模式下被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'export_peaks_csv',
      description: '写入：把当前频谱峰值列表（freq_hz,db）按强度降序写成 CSV 存到 path。手动模式下被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'export_ft8_results',
      description: '写入：把当前 FT8 解码结果（含 SIC 分离）导出为文本/CSV 存到 path。手动模式下被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'analyze_vna_match',
      description: '只读：给定负载阻抗 load_r(+load_x) 与频率 f_hz，返回匹配网络 L/C 建议。topology 可选 series_shunt(默认)/xps/xs/xpl 三电抗拓扑，r_comp 为 R 补偿。负载电阻需 >50Ω（xs 相反）。',
      write: false),
  ToolCatalogEntry(
      name: 'set_noise_blanker',
      description: '写入：开关噪声抑制器。',
      write: true),
  ToolCatalogEntry(
      name: 'get_noise_blanker_status',
      description: '只读：返回噪声抑制器使能状态。',
      write: false),
  // ---- 书签（#28-31）----
  ToolCatalogEntry(
      name: 'list_bookmarks',
      description: '只读：列出书签（频率/名称/模式）。',
      write: false),
  ToolCatalogEntry(
      name: 'add_bookmark',
      description: '写入：添加书签。',
      write: true),
  ToolCatalogEntry(
      name: 'tune_to_bookmark',
      description: '写入：调谐到指定下标书签频率。',
      write: true),
  ToolCatalogEntry(
      name: 'delete_bookmark',
      description: '写入：删除指定下标书签。',
      write: true),
  // ---- VFO 信道（#32-39）----
  ToolCatalogEntry(
      name: 'list_vfos',
      description: '只读：列出全部 VFO 信道。',
      write: false),
  ToolCatalogEntry(
      name: 'add_vfo',
      description: '写入：新增一个 VFO 信道。',
      write: true),
  ToolCatalogEntry(
      name: 'switch_vfo',
      description: '写入：切换选中的 VFO 信道。',
      write: true),
  ToolCatalogEntry(
      name: 'rename_vfo',
      description: '写入：重命名指定 VFO 信道。',
      write: true),
  ToolCatalogEntry(
      name: 'set_vfo_armed',
      description: '写入：开启/关闭某 VFO 的后台并行解调。',
      write: true),
  ToolCatalogEntry(
      name: 'set_vfo_frequency',
      description: '写入：把指定 VFO 调谐到新频率。',
      write: true),
  ToolCatalogEntry(
      name: 'set_vfo_mode',
      description: '写入：切换指定 VFO 的解调模式。',
      write: true),
  ToolCatalogEntry(
      name: 'set_vfo_bandwidth',
      description: '写入：设置指定 VFO 的信道带宽。',
      write: true),
  // ---- 录制文件管理（#40-42）----
  ToolCatalogEntry(
      name: 'list_recordings',
      description: '只读：列出录制目录下已有录制文件。',
      write: false),
  ToolCatalogEntry(
      name: 'delete_recording',
      description: '写入：删除录制目录下指定文件。',
      write: true),
  ToolCatalogEntry(
      name: 'export_recording',
      description: '写入：把录制文件复制导出到目标路径。',
      write: true),
  // ---- 频谱参数 / 色板（#43-45）----
  ToolCatalogEntry(
      name: 'set_fft_params',
      description: '写入：设置 FFT 参数（点数/窗/平均）。',
      write: true),
  ToolCatalogEntry(
      name: 'set_color_map',
      description: '写入：保存瀑布图色板路径偏好。',
      write: true),
  ToolCatalogEntry(
      name: 'get_spectrum_status',
      description: '只读：返回频谱当前参数真实值。',
      write: false),
  // ---- 卫星多普勒 / 网络源 / 能力与录制态（#46-49）----
  ToolCatalogEntry(
      name: 'set_doppler_compensation',
      description: '写入：开关过境实时多普勒自动补偿（1Hz TLE 距离率重调 VFO）。',
      write: true),
  ToolCatalogEntry(
      name: 'connect_network_source',
      description: '写入：连接 rtl_tcp 网络接收机（host/port）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_capabilities',
      description: '只读：返回当前源真实能力（设备名/调谐范围/采样率/增益档）。',
      write: false),
  ToolCatalogEntry(
      name: 'get_recording_state',
      description: '只读：返回录制状态（是否录制/路径/值守录制/录制目录）。',
      write: false),
  // ---- NanoVNA 校准状态机（P0 #59，写）----
  ToolCatalogEntry(
      name: 'vna_calibration',
      description:
          '写入：NanoVNA 校准单工具状态机。step 为命令字（status/open/short/load/isolation/thru/done/save/load/scan/pause/resume），save/load 需 slot 1..4。写动作手动 gate 拦截。',
      write: true),
  // ---- NanoVNA 设备级门面（P1 #70，写）----
  ToolCatalogEntry(
      name: 'vna_device',
      description:
          '写入：NanoVNA 设备级杂项单工具状态机。action=status/vbat/freq_offset/edelay/averaging；读动作客观返回当前值，写动作手动 gate 拦截。',
      write: true),
  // ---- FT8 解码历史滚动窗口（P2 #71，读）----
  ToolCatalogEntry(
      name: 'get_ft8_history',
      description:
          '只读：返回最近 N 条 FT8 解码历史（WSJT-X 风格，时间倒序，字段 time_ms/freq_hz/demod/text）。无历史 history=[] 诚实空态。区别于 get_ft8_status（当前帧即时状态）与 export_ft8_results（导出文件）。',
      write: false),
  // ---- ADS-B 飞机快照（P2 #72，读）----
  ToolCatalogEntry(
      name: 'get_adsb_aircraft',
      description:
          '只读：返回最近 N 架已解码 ADS-B 飞机（按最后看到时间倒序，字段 icao/callsign/altitude_ft/lat/lon/groundspeed_kt/heading_deg/vertical_rate_fpm/df/crc_ok/last_seen_ms）。1090 MHz 无信号或未调谐 aircraft=[] 诚实空态。',
      write: false),
  // ---- 本地记忆族 + checkpoint writer 控制面（P3 #73/#74，SQLite FTS5）----
  ToolCatalogEntry(
      name: 'memory_add',
      description:
          '写入：向本地记忆库（SQLite FTS5，~/.mbdsdr/memory.db）新增一条记忆——入队即返回 queued=true，落库由后台 checkpoint writer 异步执行。scope 必填枚举 user/agent/session；scope_id 作用域实例标识；key 在 scope 内唯一。立即落库用 memory_flush。手动模式被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'memory_search',
      description:
          '只读：全文检索本地记忆库（中英文均可）。query 自然语言词串经安全转义走 FTS5 MATCH；可选 scope/scope_id 过滤；limit 1..100 默认 20。无命中 hits=[] 空数组附 note，不编造记忆。',
      write: false),
  ToolCatalogEntry(
      name: 'memory_update',
      description:
          '写入：按 (scope,key) upsert 记忆——存在则覆盖 content/scope_id 与时间戳，不存在等价新增；经后台 checkpoint writer 异步落库（queued=true）。scope 必填枚举 user/agent/session。立即落库用 memory_flush。手动模式被拦截。',
      write: true),
  ToolCatalogEntry(
      name: 'checkpoint_status',
      description:
          '只读：后台 checkpoint writer 状态——pending_count/queue_depth（待落库条目数）、last_flush_ms（最近落库墙钟毫秒，0=未落库）。主 agent 只读不写。',
      write: false),
  ToolCatalogEntry(
      name: 'memory_flush',
      description:
          '写入：同步排空记忆队列并落库，重写 ~/.mbdsdr/checkpoint.md 人类可读快照，返回 written 实际落库条数。手动模式被拦截。',
      write: true),
];

/// 与移动端 buildRadioTools() 当前注册工具**同名**的集合，用于在目录里诚实标注
/// 「移动端已接入」。注意：移动端另有的 set_frequency/set_gain/set_sample_rate
/// 命名与桌面不同（桌面为 tune_frequency 等），不在这里冒充对齐。
const Set<String> kMobileImplementedToolNames = <String>{
  'set_mode',
  'start_recording',
  'stop_recording',
  'set_squelch',
  'get_squelch_status',
  'get_status',
  'predict_passes',
};
