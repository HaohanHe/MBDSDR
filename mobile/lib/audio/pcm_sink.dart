import 'dart:typed_data';

/// 48kHz 单声道 PCM 输出 sink 抽象。
///
/// 解调后的音频是单声道、约 48kHz（NFM≈51.2k，WFM≈48k）的 [Float32List]，
/// 样本取值约 [-1, 1]。本接口把「喂入一帧 Float32 音频」这件事抽象出来，
/// 与具体如何出声解耦：
///
///  - [start]：打开输出，采样率/声道可配（默认 48kHz / 单声道）；
///  - [write]：喂入一帧单声道 Float32 样本；**静音与音量由实现内部应用**，
///    调用方无需自己缩放；
///  - [setVolume] / [setMuted]：运行时调节增益与静音；
///  - [dispose]：释放底层音频资源，之后不应再 [write]。
///
/// 实现侧：
///  - [PlatformPcmSink]：真实设备出声（MethodChannel → 原生 AudioTrack/AVAudioEngine）；
///  - [RecordingPcmSink]：测试用，把帧记录在内存里供断言；
///  - [NoOpSink]：丢弃所有帧，用于未配置输出或测试。
///
/// 注意：本层只负责「把解调后的 Float32 送到可播放的地方」，不负责解调本身
/// （解调在 `lib/dsp/demod.dart`，音频流由 `RadioController.audioStream` 产出）。
abstract class PcmSink {
  /// 打开输出。
  ///
  /// [sampleRateHz] 默认 48000，[channels] 默认 1（单声道）。
  /// 可重复调用：实现应先释放旧资源、再以新参数重启。
  Future<void> start({int sampleRateHz = 48000, int channels = 1});

  /// 喂入一帧单声道 Float32 样本（取值约 [-1, 1]）。
  ///
  /// 静音与音量由实现内部应用。未 [start] 时调用应为安全空操作（不抛异常）。
  void write(Float32List frame);

  /// 设置音量，取值 [0, 1]。
  void setVolume(double v);

  /// 静音开关：true 时输出应静音。
  void setMuted(bool m);

  /// 释放底层资源，之后不应再调用 [write]。
  Future<void> dispose();
}
