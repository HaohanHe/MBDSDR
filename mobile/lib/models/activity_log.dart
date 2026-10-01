// 信号活动日志条目值对象：一条「真实观察到的信号」记录。
//
// 与桌面端 cpp/src/ui/activity_log.h 的 SignalActivity 语义对齐：
//   * 与书签严格区分——书签是用户手工收藏；活动日志是系统自动追加的观察记录；
//   * 字段：观察时刻(UTC epoch ms) / 频率(Hz) / 电平(dBFS，来自真实解调音频 RMS)
//     / 解调模式 / 来源（'squelch'=值守开门，后续可扩展 'scan'=扫描带）；
//   * 绝不内置任何假信号：无真实观察时列表为空（诚实空态）。
library;

/// 一条信号活动记录（不可变，newest-first 排序）。
class ActivityEntry {
  const ActivityEntry({
    required this.epochMsUtc,
    required this.frequencyHz,
    required this.levelDbfs,
    required this.mode,
    required this.source,
  });

  /// 观察时刻（UTC epoch 毫秒）。
  final int epochMsUtc;

  /// 观察时中心频率（Hz）。
  final int frequencyHz;

  /// 真实解调音频 RMS 峰值电平（dBFS），来自静噪门平滑电平。
  final double levelDbfs;

  /// 观察时解调模式字符串（'nfm' / 'wfm'）；未知为空串。
  final String mode;

  /// 来源：'squelch'（值守静噪门开门）/ 'scan'（扫描带，预留）等。
  final String source;

  /// 本地显示时刻。
  DateTime get observedAt =>
      DateTime.fromMillisecondsSinceEpoch(epochMsUtc, isUtc: true).toLocal();

  /// 面向 UI 的来源短标签。
  String get sourceLabel => switch (source) {
        'squelch' => '值守',
        'scan' => '扫描',
        _ => source,
      };

  /// 落盘 JSON。
  Map<String, dynamic> toJson() => <String, dynamic>{
        'epochMsUtc': epochMsUtc,
        'frequencyHz': frequencyHz,
        'levelDbfs': levelDbfs,
        'mode': mode,
        'source': source,
      };

  /// 从 JSON 读回；缺关键字段/非法频率返回 null，由上层丢弃。
  static ActivityEntry? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final t = raw['epochMsUtc'];
    final hz = raw['frequencyHz'];
    if (t is! num || hz is! num || hz.toInt() <= 0) return null;
    final level = raw['levelDbfs'];
    final mode = raw['mode'];
    final src = raw['source'];
    return ActivityEntry(
      epochMsUtc: t.toInt(),
      frequencyHz: hz.toInt(),
      levelDbfs: level is num ? level.toDouble() : 0,
      mode: mode is String ? mode.trim().toLowerCase() : '',
      source: src is String && src.trim().isNotEmpty ? src.trim() : 'squelch',
    );
  }
}
