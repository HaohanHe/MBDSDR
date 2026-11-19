/// 读数来源标注（P2）：上屏的每个数值都要能说清「从哪来」。
///
/// 与桌面 `desktop/status_panel.py` 的 `_provenance()` 保持同一口径：
/// 文案统一由 [withSource] 追加，页面各处只传常量，不再各自拼后缀。
library;

/// 手机端读数来源常量（与桌面同名同义）。
class ReadoutSources {
  /// 设备本地时钟：未与任何时间基准校准。
  static const String deviceClock = '设备时钟（未校准）';

  /// 设置中手填的本站坐标。
  static const String manualStation = '手填';

  /// 系统定位（GNSS/网络）返回的本站坐标。
  static const String systemLocation = '系统定位';

  /// 没有可核实的来源；不猜测。
  static const String unknown = '未知';

  /// 过境表：TLE + SGP4 传播预测。
  static const String sgp4Prediction = '预测·SGP4';

  /// 指天引导的目标 az/el：预测值。
  static const String prediction = '预测';

  /// 调谐设定值（请求值，非回读）。
  static const String setpoint = '设定值';

  /// 频谱帧实测峰值。
  static const String measuredPeak = '实测峰值';

  /// 设备/后端报告值。
  static const String deviceReport = '设备报告';
}

/// 给读数追加中文来源后缀；空态原样返回，不把空值包装成读数。
///
/// 空串、「—」或来源为空时不做包装——宁可少一个后缀，也不把
/// 「没有读数」写成「—（实测）」。重复调用同一来源不会叠加。
String withSource(String text, String source) {
  final txt = text.trim();
  final src = source.trim();
  if (txt.isEmpty || txt == '—' || src.isEmpty || txt == src) return text;
  final suffix = '（$src）';
  if (txt.endsWith(suffix)) return text;
  return '$text$suffix';
}
