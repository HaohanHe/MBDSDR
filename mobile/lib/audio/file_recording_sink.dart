// 真实文件录音 sink：把解调后的 Float32 帧量化成 16-bit 小端 PCM 写成本地 .wav，
// 并在结束时产出 sidecar JSON 与一条 [RecordingMeta] 索引。
//
// 设计要点（对齐 B3 方案 A）：
//  - 与 [RecordingPcmSink] 一致，**录干净解调音频**：此处不应用 volume/muted，
//    音量/静音只影响外放（delegate），不影响落盘；
//  - 若注入 [delegate]（如 PlatformPcmSink），录制不打断实时收听——帧同时落盘与出声；
//  - [start] 即开新会话（开时间戳文件名的 .wav）；[dispose] 关 WAV、写 sidecar、
//    把结果存进 [result]（供上层 SettingsService.addRecording）。
//
// 纯 dart:io，云内可单测；目录由调用方注入（真机传 RecordingStore 的录音目录）。
library;

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../models/recording.dart';
import 'pcm_codec.dart';
import 'pcm_sink.dart';
import 'wav_writer.dart';

/// 一次文件录音会话 sink。
///
/// 典型用法（端到端）：
/// ```dart
/// final sink = FileRecordingSink(
///   dir: await store.recordingsDir(),
///   metaFactory: () => RecordingMeta(
///     startedAtEpochMs: DateTime.now().millisecondsSinceEpoch,
///     frequencyHz: 145800000, mode: 'wfm', deviceSource: 'connected'),
/// );
/// await sink.start();
/// sink.write(frame);            // 逐帧落盘（并转发 delegate）
/// await sink.dispose();
/// final meta = sink.result!;     // 含 sampleCount/durationMs/wavFileName
/// settings.addRecording(meta);
/// ```
class FileRecordingSink implements PcmSink {
  FileRecordingSink({
    required Directory dir,
    required RecordingMeta Function() metaFactory,
    PcmSink? delegate,
  })  : _dir = dir,
        _metaFactory = metaFactory,
        _delegate = delegate;

  final Directory _dir;
  final RecordingMeta Function() _metaFactory;
  final PcmSink? _delegate;

  WavWriter? _writer;
  String? _wavPath;
  String? _stem;
  int _sampleRateHz = 48000;
  int _channels = 1;

  /// 本次会话结束后产出的元数据（含 sidecar 扩展字段）；未 [dispose] 完成前为 null。
  RecordingMeta? result;

  /// 已开始写盘的 WAV 绝对路径（调试/回放用）。
  String? get wavPath => _wavPath;

  @override
  Future<void> start({int sampleRateHz = 48000, int channels = 1}) async {
    _sampleRateHz = sampleRateHz;
    _channels = channels;
    await _delegate?.start(sampleRateHz: sampleRateHz, channels: channels);

    // 时间戳文件名：<epochms>_<freq>_<mode>.wav。
    final base = _metaFactory();
    final stamp = base.startedAtEpochMs;
    final safeMode = base.mode.replaceAll(RegExp(r'[^A-Za-z0-9_]'), '');
    _stem = '${stamp}_${base.frequencyHz}_$safeMode';
    _wavPath = '${_dir.path}/$_stem.wav';
    if (!_dir.existsSync()) await _dir.create(recursive: true);
    final file = File(_wavPath!);
    final raf = file.openSync(mode: FileMode.write);
    _writer = WavWriter(
      raf,
      sampleRateHz: sampleRateHz,
      channels: channels,
      bitsPerSample: 16,
    );
  }

  @override
  void write(Float32List frame) {
    // 转发 delegate（实时收听）；录制本身即便 delegate 未 start 也安全。
    _delegate?.write(frame);
    final w = _writer;
    if (w == null) return; // 未 start：安全丢弃。
    // 录干净信号：volume/muted 默认 1.0/false，不缩放、不静音。
    w.write16BitPcm(floatTo16BitPcm(frame));
  }

  @override
  void setVolume(double v) => _delegate?.setVolume(v);

  @override
  void setMuted(bool m) => _delegate?.setMuted(m);

  @override
  Future<void> dispose() async {
    final w = _writer;
    if (w == null) {
      await _delegate?.dispose();
      return;
    }
    _writer = null;
    await w.close();
    await _delegate?.dispose();

    // 用 metaFactory() 取基础字段，再补落盘统计，写 sidecar。
    final base = _metaFactory();
    final meta = RecordingMeta(
      startedAtEpochMs: base.startedAtEpochMs,
      frequencyHz: base.frequencyHz,
      mode: base.mode,
      note: base.note,
      sampleRateHz: _sampleRateHz,
      channels: _channels,
      bitsPerSample: 16,
      sampleCount: w.sampleCount,
      durationMs: w.durationMs,
      wavFileName: '$_stem.wav',
      deviceSource: RecordingSource.normalize(base.deviceSource) ??
          RecordingSource.connected,
    );
    final sidecar = File('${_dir.path}/$_stem.json');
    await sidecar.writeAsString(jsonEncode(meta.toJson()));
    result = meta;
  }
}
