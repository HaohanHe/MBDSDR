// ============================================================================
// AI 工具能力清单（只读参考目录）—— 对齐桌面 cpp/src/ai/tool_schema.cpp。
// ----------------------------------------------------------------------------
// 诚实性说明：
//   * 这是**只读参考目录**，不是"移动端现在能调这些工具"的声明。
//   * 桌面端（C++/Qt）注册了 35 个 Agent 工具；移动端 AiClient 实际接入的工具
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

/// 桌面端 35 个工具的只读目录（对齐 cpp/src/ai/tool_schema.cpp:registeredToolSpecs）。
const List<ToolCatalogEntry> kDesktopToolCatalog = <ToolCatalogEntry>[
  // ---- 基础调谐/解调 ----
  ToolCatalogEntry(
      name: 'tune_frequency',
      description: '把接收机调到指定中心频率（Hz）。',
      write: true),
  ToolCatalogEntry(
      name: 'set_mode',
      description: '设置解调模式（AM/NFM/WFM/USB/LSB/CW）。',
      write: true),
  ToolCatalogEntry(
      name: 'set_bandwidth',
      description: '设置信道滤波带宽（Hz，取命名档位）。',
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
      name: 'get_status',
      description: '读取当前接收机状态（频率/模式/带宽/采样率）。',
      write: false),
  // ---- 卫星 / 频率校准 ----
  ToolCatalogEntry(
      name: 'predict_passes',
      description: '只读：用本地新鲜 TLE 预测卫星未来过境。',
      write: false),
  ToolCatalogEntry(
      name: 'calibrate_frequency',
      description: '只读测量：用已知精确参考信号估计晶振 ppm 误差。',
      write: false),
  ToolCatalogEntry(
      name: 'apply_frequency_correction',
      description: '写入并应用频率校正 ppm。',
      write: true),
  // ---- 数字解码快照（只读）----
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
  // ---- IQ 导出 ----
  ToolCatalogEntry(
      name: 'export_iq_segment',
      description: '写入：即时抓取一段基带 IQ 导出为 SigMF。',
      write: true),
  // ---- 网络音频 / 扫描活动链路 ----
  ToolCatalogEntry(
      name: 'set_network_audio_sink',
      description: '写入：配置网络音频流输出（UDP/TCP 镜像）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_network_audio_status',
      description: '只读：返回网络音频流状态。',
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
      description: '只读：返回扫描活动链路状态。',
      write: false),
  // ---- 静噪 ----
  ToolCatalogEntry(
      name: 'set_squelch',
      description: '写入：设置静噪（开关/门限/自动）。',
      write: true),
  ToolCatalogEntry(
      name: 'get_squelch_status',
      description: '只读：返回静噪状态（开关/门限/open）。',
      write: false),
  // ---- 书签 ----
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
  // ---- VFO 信道 ----
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
  // ---- 录制文件管理 ----
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
  // ---- 频谱参数 / 色板 ----
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
