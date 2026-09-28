import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/services.dart';

import 'pcm_codec.dart';
import 'pcm_sink.dart';

/// 真实设备出声的 [PcmSink]。
///
/// ---------------------------------------------------------------
/// 选型说明（为什么用 MethodChannel 而不是 pub 的 flutter_pcm_player）
/// ---------------------------------------------------------------
/// 曾尝试 `flutter pub add flutter_pcm_player`：该包 0.0.1 可解析、确实能流式
/// `feed(Uint8List)`（Android 走 `AudioTrack.MODE_STREAM` 阻塞写，iOS 走
/// `AVAudioEngine` + `AVAudioPlayerNode` 环形缓冲）。但综合判断**不适合**直接采用：
///   1. 版本 0.0.1，Android 包命名空间仍是脚手架默认的 `com.example.flutter_pcm_player`，
///      README 也是「A new Flutter project」样板，属未成熟/无人维护状态；
///   2. 其 16-bit 格式标注为「native endianness」，未对小端做显式契约；
///   3. 没有 first-party `setMuted`，生命周期需要额外的 play/pause/resume 状态机；
/// 因此退回**自有的 MethodChannel 契约**（channel = `mbdsdr/audio`），Dart 侧统一用
/// [floatTo16BitPcm] 把 Float32 量化成有符号 16-bit **小端** PCM 后再喂给原生。
///
/// ---------------------------------------------------------------
/// Dart → 原生 MethodChannel 契约（channel = `mbdsdr/audio`）
/// ---------------------------------------------------------------
/// | method      | 参数 map                                  | 说明 |
/// |-------------|-------------------------------------------|------|
/// | `start`     | `{"sampleRate": int, "channels": int}`    | 初始化并启动输出。约定 48000 / 单声道。 |
/// | `write`     | `{"pcm": Uint8List}`                      | 一帧有符号 16-bit **小端** PCM（Dart 侧已量化并应用 volume/muted）。 |
/// | `setVolume` | `{"volume": double}`（0.0..1.0）          | 设置播放增益。 |
/// | `setMuted`  | `{"muted": bool}`                         | 静音（原生可映射为 gain=0）。 |
/// | `dispose`   | 无                                        | 停止并释放输出资源。 |
///
/// 原生侧**尚未要求** Dart 返回值；原生可用任意 MethodChannel.Result 应答。
///
/// ---------------------------------------------------------------
/// Android（Kotlin/Java，在 MainActivity 的 configureFlutterEngine 注册）
/// ---------------------------------------------------------------
/// - 用 `android.media.AudioTrack`，模式 `MODE_STREAM` 流式写入；
/// - 配置：采样率 48000、声道 `CHANNEL_OUT_MONO`、编码 `ENCODING_PCM_16BIT`；
/// - 音频流类型 `AudioManager.STREAM_MUSIC`；
/// - `write`：`audioTrack.write(bytes, 0, bytes.size, AudioTrack.WRITE_BLOCKING)`；
/// - `setVolume`：`audioTrack.setVolume(gain)`；`setMuted`：gain=0；
/// - `start` 时 `play()`，`dispose` 时 `stop()` + `release()`；
/// - 播放本身无需危险权限；如要兼容旧设备可声明 `MODIFY_AUDIO_SETTINGS`。
///
/// ---------------------------------------------------------------
/// iOS（Swift，在 AppDelegate 的 didFinishLaunchingWithOptions 注册）
/// ---------------------------------------------------------------
/// - 用 `AVAudioEngine` + `AVAudioPlayerNode`（等价的 AudioQueue 亦可）；
/// - 输出格式：`AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48000,
///   channels: 1, interleaved: false)`；
/// - 启动前：`AVAudioSession.sharedInstance()` 设 `.playback` category 并 `setActive(true)`；
/// - `write`：把 `Uint8List` 包成 `AVAudioPCMBuffer` 后 `playerNode.scheduleBuffer(...)` 入队；
/// - `setVolume`：`playerNode.volume = Float(0..1)`；`setMuted`：volume=0；
/// - `dispose`：停止 node、断开 engine、deactivate session。
///
/// > 真机待验：本仓库云测环境无法编译/运行 Android/iOS 原生工程，上述 Kotlin/Swift
/// > 端实现尚未落地、真机出声与原生构建均未验证。Dart 侧契约、量化与单测已就绪，
/// > 原生侧由后续集成方按本文件契约实现并在真机验证。
class PlatformPcmSink implements PcmSink {
  PlatformPcmSink({MethodChannel? channel})
      : _channel = channel ?? _defaultChannel;

  static const MethodChannel _defaultChannel = MethodChannel('mbdsdr/audio');
  final MethodChannel _channel;

  int _sampleRateHz = 48000;
  int _channels = 1;
  double _volume = 1.0;
  bool _muted = false;
  bool _started = false;

  /// 最近一次 [start] 的采样率。
  int get sampleRateHz => _sampleRateHz;

  /// 最近一次 [start] 的声道数（默认 1 = 单声道）。
  int get channels => _channels;

  /// 当前音量（0..1）。
  double get volume => _volume;

  /// 当前是否静音。
  bool get muted => _muted;

  /// 是否已 start（dispose 后为 false）。
  bool get isStarted => _started;

  @override
  Future<void> start({int sampleRateHz = 48000, int channels = 1}) async {
    _sampleRateHz = sampleRateHz;
    _channels = channels;
    await _channel.invokeMethod<void>('start', {
      'sampleRate': sampleRateHz,
      'channels': channels,
    });
    _started = true;
  }

  @override
  void write(Float32List frame) {
    // 未启动则安全丢弃，不向原生发送。
    if (!_started) return;

    // Dart 侧统一量化：Float32 → 有符号 16-bit 小端，并在此应用 volume/muted。
    final pcm = floatTo16BitPcm(frame, volume: _volume, muted: _muted);

    // fire-and-forget：原生侧为阻塞写，不 await 以免拖慢解调回调线程。
    unawaited(_channel.invokeMethod<void>('write', {'pcm': pcm}));
  }

  @override
  void setVolume(double v) {
    _volume = v.clamp(0.0, 1.0).toDouble();
    if (_started) {
      unawaited(_channel.invokeMethod<void>('setVolume', {'volume': _volume}));
    }
  }

  @override
  void setMuted(bool m) {
    _muted = m;
    if (_started) {
      unawaited(_channel.invokeMethod<void>('setMuted', {'muted': m}));
    }
  }

  @override
  Future<void> dispose() async {
    if (!_started) return;
    _started = false;
    await _channel.invokeMethod<void>('dispose');
  }
}
