// 录音元数据值对象：一次录制会话的可读索引 + sidecar JSON。
//
// 诚实性说明（务必保留）：
//   * 真实文件录制链路已就绪：FileRecordingSink（audio/file_recording_sink.dart）
//     把解调后的 Float32 量化成 16-bit 小端 PCM 写成本地 .wav，并产出本元数据
//     的 sidecar JSON。录制结束由上层调 SettingsService.addRecording 进索引。
//   * 无录制时列表恒为空，绝不预置假条目；deviceSource 只取真实连接状态
//     （"connected" / "test_signal"），不编第三类、不编空串。
library;

/// 录音音频来源枚举（字符串，落盘 sidecar 用）。
///
/// 只有两类：
///  - [RecordingSource.connected]：真实 rtl_tcp 收音；
///  - [RecordingSource.testSignal]：内部测试信号源。
/// 无第三类，禁止空串冒充。
abstract final class RecordingSource {
  static const String connected = 'connected';
  static const String testSignal = 'test_signal';

  /// 仅当 [s] 是两类之一才接受，否则返回 null（调用方丢弃非法侧car）。
  static String? normalize(String? s) {
    switch (s) {
      case connected:
      case testSignal:
        return s;
      default:
        return null;
    }
  }
}

/// 一次录音会话的元数据（不含音频采样本身，音频在配对的 .wav 文件里）。
///
/// 前四个字段（[startedAtEpochMs]/[frequencyHz]/[mode]/[note]）是旧索引结构；
/// 后七个可选字段是真实落盘 sidecar 的扩展（向后兼容：旧 JSON 缺这些键时为 null）。
class RecordingMeta {
  const RecordingMeta({
    required this.startedAtEpochMs,
    required this.frequencyHz,
    required this.mode,
    this.note = '',
    this.sampleRateHz,
    this.channels,
    this.bitsPerSample,
    this.sampleCount,
    this.durationMs,
    this.wavFileName,
    this.deviceSource,
  });

  /// 录制开始时刻（UTC epoch 毫秒）。
  final int startedAtEpochMs;

  /// 录制时中心频率（Hz）。
  final int frequencyHz;

  /// 录制时解调模式字符串（'nfm' / 'wfm'）。
  final String mode;

  /// 备注（如卫星名）；可空。
  final String note;

  // ---------------- 以下为真实落盘 sidecar 扩展字段（可空，旧索引没有） ----------------

  /// WAV 采样率（Hz）。
  final int? sampleRateHz;

  /// WAV 声道数（默认单声道 1）。
  final int? channels;

  /// 采样位深（16）。
  final int? bitsPerSample;

  /// 样本总数（= PCM 字节数 / 2）。
  final int? sampleCount;

  /// 时长（毫秒）。
  final int? durationMs;

  /// 配对 WAV 文件名（仅文件名，不含目录；落在 RecordingStore 录音目录内）。
  final String? wavFileName;

  /// 音频来源：'connected'（真实 rtl_tcp）/ 'test_signal'（内部测试源）。
  final String? deviceSource;

  /// 开始时刻（本地显示用）。
  DateTime get startedAt =>
      DateTime.fromMillisecondsSinceEpoch(startedAtEpochMs, isUtc: true)
          .toLocal();

  /// 落盘 JSON（sidecar）。扩展字段仅在非 null 时写入，保持旧索引兼容。
  Map<String, dynamic> toJson() => <String, dynamic>{
        'startedAtEpochMs': startedAtEpochMs,
        'frequencyHz': frequencyHz,
        'mode': mode,
        'note': note,
        if (sampleRateHz != null) 'sampleRateHz': sampleRateHz,
        if (channels != null) 'channels': channels,
        if (bitsPerSample != null) 'bitsPerSample': bitsPerSample,
        if (sampleCount != null) 'sampleCount': sampleCount,
        if (durationMs != null) 'durationMs': durationMs,
        if (wavFileName != null) 'wavFileName': wavFileName,
        if (deviceSource != null) 'deviceSource': deviceSource,
      };

  /// 从 JSON 读回；缺关键字段/非法频率返回 null，由上层丢弃。
  ///
  /// 只读自己认识的键：sidecar 多出的键被安全忽略（向后兼容旧索引）；
  /// 非法 [deviceSource]（非两类之一）也接受为 null，不拒绝整条记录。
  static RecordingMeta? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final t = raw['startedAtEpochMs'];
    final hz = raw['frequencyHz'];
    if (t is! num || hz is! num || hz.toInt() <= 0) return null;
    final mode = raw['mode'];
    final note = raw['note'];

    int? asInt(Object? v) => v is num ? v.toInt() : null;
    return RecordingMeta(
      startedAtEpochMs: t.toInt(),
      frequencyHz: hz.toInt(),
      mode: mode is String ? mode.trim().toLowerCase() : '',
      note: note is String ? note : '',
      sampleRateHz: asInt(raw['sampleRateHz']),
      channels: asInt(raw['channels']),
      bitsPerSample: asInt(raw['bitsPerSample']),
      sampleCount: asInt(raw['sampleCount']),
      durationMs: asInt(raw['durationMs']),
      wavFileName: raw['wavFileName'] is String
          ? raw['wavFileName'] as String
          : null,
      deviceSource: RecordingSource.normalize(raw['deviceSource'] is String
          ? raw['deviceSource'] as String
          : null),
    );
  }
}
