import 'dart:typed_data';

import 'pcm_sink.dart';

/// 把每帧喂入的 Float32List 记录在内存里的测试用 sink。
///
/// 不做任何平台播放，只保留观测状态，供单测断言「解调 → 喂入 sink」链路：
///
///  - [recorded]：按写入顺序保存的每一帧（深拷贝，避免调用方复用缓冲被改写）；
///  - [sampleRateHz] / [channels]：最近一次 [start] 的参数；
///  - [volume] / [muted]：最近一次 [setVolume] / [setMuted] 的状态；
///  - [started]：是否已 [start]、是否已 [dispose]。
///
/// 注意：这里记录的是**调用方原样喂入的 Float32 帧**，不在内部应用音量/静音——
/// 音量/静音的量化由 [floatTo16BitPcm] 负责并单独单测。
class RecordingPcmSink implements PcmSink {
  final List<Float32List> _recorded = [];

  /// 按写入顺序记录的每一帧（不可变视图）。
  List<Float32List> get recorded => List.unmodifiable(_recorded);

  /// 已 start 的采样率（默认 48000）。
  int sampleRateHz = 48000;

  /// 已 start 的声道数（默认 1 = 单声道）。
  int channels = 1;

  /// 当前音量（默认 1.0）。
  double volume = 1.0;

  /// 当前是否静音（默认 false）。
  bool muted = false;

  /// 是否已 start（dispose 后置回 false）。
  bool started = false;

  @override
  Future<void> start({int sampleRateHz = 48000, int channels = 1}) async {
    this.sampleRateHz = sampleRateHz;
    this.channels = channels;
    started = true;
  }

  @override
  void write(Float32List frame) {
    // 深拷贝一份，防止上游复用同一缓冲导致后续帧覆盖历史。
    _recorded.add(Float32List.fromList(frame));
  }

  @override
  void setVolume(double v) => volume = v.clamp(0.0, 1.0).toDouble();

  @override
  void setMuted(bool m) => muted = m;

  @override
  Future<void> dispose() async {
    started = false;
  }
}
