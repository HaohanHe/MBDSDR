import 'dart:typed_data';

import 'pcm_sink.dart';

/// 丢弃所有帧的空实现（No-op）。
///
/// 用途：
///  - 用户关闭扬声器 / 尚未配置真实输出时，注入它避免空引用；
///  - 单元测试里作为「不发声」的默认 sink。
///
/// 所有方法都是安全空操作，任何调用都不抛异常。
class NoOpSink implements PcmSink {
  bool _started = false;

  /// 最近一次 [start] 是否成功（这里恒记为 true）。
  bool get isStarted => _started;

  @override
  Future<void> start({int sampleRateHz = 48000, int channels = 1}) async {
    _started = true;
  }

  @override
  void write(Float32List frame) {
    // 主动丢弃，什么都不做。
  }

  @override
  void setVolume(double v) {
    // no-op
  }

  @override
  void setMuted(bool m) {
    // no-op
  }

  @override
  Future<void> dispose() async {
    _started = false;
  }
}
