// 文件回放器：复用 mbdsdr/audio 通道，从磁盘 .wav（或裸 16-bit 小端 PCM）回放。
//
// ---------------------------------------------------------------
// 契约（在 PlatformPcmSink 的实时流 start/write/... 之上追加）
// ---------------------------------------------------------------
// | method        | 参数 map                                          | 返回 |
// |---------------|---------------------------------------------------|------|
// | `startFile`   | `{"path":String,"sampleRate":int,"channels":int}` | {}   |
// | `pauseFile`   | 无                                                | {}   |
// | `resumeFile`  | 无                                                | {}   |
// | `stopFile`    | 无                                                | {}   |
// | `filePosition`| 无                                                | {"positionMs":int,"durationMs":int,"playing":bool} |
//
// 原生 → Dart 事件（原生经同一通道 invokeMethod 推给 Dart 注册的 handler）：
//  - `onPosition`：`{positionMs,durationMs,playing}` 进度更新；
//  - `onComplete`：`{}` 文件自然播完。
//
// [云未编译·真机待验]：Android（MainActivity.kt 用第二个 AudioTrack MODE_STREAM
// 读文件 PCM）与 iOS（AppDelegate.swift 用 AVAudioFile + AVAudioPlayerNode）原生实现
// 已按本契约写入，但云 VM 无原生工具链，未编译/未链接/未真机出声。Dart 侧契约与
// 单测（mock 通道）已就绪。
library;

import 'dart:async';

import 'package:flutter/services.dart';

/// 回放进度/状态快照。
class PlaybackState {
  const PlaybackState({
    required this.positionMs,
    required this.durationMs,
    required this.playing,
    this.completed = false,
  });

  /// 当前播放位置（毫秒）。
  final int positionMs;

  /// 文件总时长（毫秒）；未知为 0。
  final int durationMs;

  /// 是否正在播放（暂停中为 false）。
  final bool playing;

  /// 是否已自然播完。
  final bool completed;

  double get progress =>
      durationMs <= 0 ? 0 : (positionMs / durationMs).clamp(0.0, 1.0);

  static const PlaybackState idle =
      PlaybackState(positionMs: 0, durationMs: 0, playing: false);
}

/// 文件回放器：封装上述通道方法与原生进度事件。
class FilePlayer {
  FilePlayer({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('mbdsdr/audio') {
    // 接收原生主动推来的 onPosition / onComplete。
    _channel.setMethodCallHandler(_onNativeCall);
  }

  final MethodChannel _channel;
  final _stateCtrl = StreamController<PlaybackState>.broadcast();
  PlaybackState _state = PlaybackState.idle;

  /// 当前状态（最新一次进度/完成事件）。
  PlaybackState get state => _state;

  /// 状态流（进度更新 / 播完）。
  Stream<PlaybackState> get onState => _stateCtrl.stream;

  Future<dynamic> _onNativeCall(MethodCall call) async {
    switch (call.method) {
      case 'onPosition':
        final a = (call.arguments as Map?) ?? const {};
        _push(PlaybackState(
          positionMs: (a['positionMs'] as num?)?.toInt() ?? 0,
          durationMs: (a['durationMs'] as num?)?.toInt() ?? 0,
          playing: a['playing'] == true,
        ));
      case 'onComplete':
        _push(PlaybackState(
          positionMs: _state.durationMs,
          durationMs: _state.durationMs,
          playing: false,
          completed: true,
        ));
    }
  }

  void _push(PlaybackState s) {
    _state = s;
    if (!_stateCtrl.isClosed) _stateCtrl.add(s);
  }

  /// 打开文件并开始播放。[path] 为本应用录音目录下的 .wav 绝对路径。
  Future<void> startFile({
    required String path,
    required int sampleRate,
    int channels = 1,
  }) async {
    await _channel.invokeMethod<void>('startFile', {
      'path': path,
      'sampleRate': sampleRate,
      'channels': channels,
    });
    _push(const PlaybackState(
      positionMs: 0,
      durationMs: 0,
      playing: true,
    ));
  }

  Future<void> pauseFile() async =>
      await _channel.invokeMethod<void>('pauseFile');

  Future<void> resumeFile() async =>
      await _channel.invokeMethod<void>('resumeFile');

  Future<void> stopFile() async {
    await _channel.invokeMethod<void>('stopFile');
    _push(PlaybackState.idle);
  }

  /// 查询进度。返回原生上报的 positionMs/durationMs/playing。
  Future<PlaybackState> queryPosition() async {
    final r = await _channel.invokeMethod<Map<Object?, Object?>>('filePosition');
    final a = r ?? const {};
    final s = PlaybackState(
      positionMs: (a['positionMs'] as num?)?.toInt() ?? 0,
      durationMs: (a['durationMs'] as num?)?.toInt() ?? 0,
      playing: a['playing'] == true,
    );
    _push(s);
    return s;
  }

  /// 释放事件流（可在 dispose 时调用；原生播放资源由 stopFile 释放）。
  Future<void> dispose() => _stateCtrl.close();
}
