// 录音元数据值对象：一次录制会话的可读索引。
//
// 诚实性说明（务必保留）：
//   * 移动端目前**没有真实文件录制能力**——lib/audio/recording_pcm_sink.dart
//     只是单测用的内存 sink，不写盘。因此本模型只是一个「就绪的索引结构」：
//     一旦将来接入真实文件录制，录制结束时把会话元数据追加进来即可。
//   * 生产代码当前**不会**写入任何 RecordingMeta，录音列表在真机上恒为空态；
//     绝不预置假录音条目。测试可注入一条元数据来验证列表渲染。
library;

/// 一次录音会话的元数据（不含音频文件本身）。
class RecordingMeta {
  const RecordingMeta({
    required this.startedAtEpochMs,
    required this.frequencyHz,
    required this.mode,
    this.note = '',
  });

  /// 录制开始时刻（UTC epoch 毫秒）。
  final int startedAtEpochMs;

  /// 录制时中心频率（Hz）。
  final int frequencyHz;

  /// 录制时解调模式字符串（'nfm' / 'wfm'）。
  final String mode;

  /// 备注（如卫星名）；可空。
  final String note;

  /// 开始时刻（本地显示用）。
  DateTime get startedAt =>
      DateTime.fromMillisecondsSinceEpoch(startedAtEpochMs, isUtc: true)
          .toLocal();

  /// 落盘 JSON。
  Map<String, dynamic> toJson() => <String, dynamic>{
        'startedAtEpochMs': startedAtEpochMs,
        'frequencyHz': frequencyHz,
        'mode': mode,
        'note': note,
      };

  /// 从 JSON 读回；缺关键字段/非法频率返回 null，由上层丢弃。
  static RecordingMeta? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final t = raw['startedAtEpochMs'];
    final hz = raw['frequencyHz'];
    if (t is! num || hz is! num || hz.toInt() <= 0) return null;
    final mode = raw['mode'];
    final note = raw['note'];
    return RecordingMeta(
      startedAtEpochMs: t.toInt(),
      frequencyHz: hz.toInt(),
      mode: mode is String ? mode.trim().toLowerCase() : '',
      note: note is String ? note : '',
    );
  }
}
