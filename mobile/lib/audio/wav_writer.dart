// RIFF/WAVE (WAV) 写盘器：把有符号 16-bit 小端 PCM 写成自洽的 .wav 文件。
//
// 纯 dart:io 实现，无需原生，云内可单测。格式契约（小端）：
//
//   偏移   字段              说明
//   ----   -----------------  --------------------------------------------
//   0      "RIFF"             魔数
//   4      chunkSize u32 LE   = 36 + dataBytes（文件总长 - 8）
//   8      "WAVE"             类型
//   12     "fmt "             子块标签
//   16     subchunk1Size u32  = 16（PCM 头部长度）
//   20     audioFormat u16    = 1（PCM，无压缩）
//   22     numChannels u16    声道数（默认 1 = 单声道）
//   24     sampleRate u32     采样率 Hz（默认 48000）
//   28     byteRate u32       = sampleRate * channels * bitsPerSample/8
//   32     blockAlign u16     = channels * bitsPerSample/8
//   34     bitsPerSample u16  = 16
//   36     "data"             数据子块标签
//   40     subchunk2Size u32  = dataBytes（实际采样字节数）
//   44     <PCM 采样数据>
//
// 单声道 16-bit：byteRate = sampleRate*2，blockAlign = 2。
// 落盘的 16-bit 字节由上层用 [floatTo16BitPcm] 量化（与出声同一路径），
// 本 writer 只负责按 RIFF/WAVE 容器封装，不再做任何量化/缩放。
library;

import 'dart:io';
import 'dart:typed_data';

/// 一次 WAV 写会话：先占位写 44 字节头，逐块追加 PCM，[close] 时回写头字段。
///
/// 用法：
/// ```dart
/// final out = File('rec.wav').openWrite(); // 或 RandomAccessFile
/// final w = WavWriter(file.openSync(), sampleRateHz: 48000, channels: 1);
/// w.write16BitPcm(some16bitLeBytes);
/// await w.close();
/// ```
class WavWriter {
  WavWriter(
    RandomAccessFile out, {
    this.sampleRateHz = 48000,
    this.channels = 1,
    this.bitsPerSample = 16,
  })  : _out = out,
        _header = ByteData(44) {
    _writeHeader(_header, dataBytes: 0);
    _out.writeFromSync(_header.buffer.asUint8List());
  }

  final RandomAccessFile _out;
  final int sampleRateHz;
  final int channels;
  final int bitsPerSample;

  /// 占位头（44 字节），[close] 时回写 chunkSize/subchunk2Size。
  final ByteData _header;

  int _dataBytes = 0;
  bool _closed = false;

  /// 已写入的 PCM 字节数（不含 44 字节头）。
  int get dataBytes => _dataBytes;

  /// 已写入的样本数（每样本 bitsPerSample/8 字节）。
  int get sampleCount => _dataBytes ~/ (bitsPerSample ~/ 8);

  /// 音频时长（毫秒）。单声道 16-bit 下 = sampleCount / sampleRateHz * 1000。
  int get durationMs =>
      sampleRateHz == 0 ? 0 : (sampleCount * 1000 / sampleRateHz).round();

  /// 追加一帧 16-bit 小端 PCM 字节。[close] 后调用抛 [StateError]。
  void write16BitPcm(Uint8List pcm) {
    if (_closed) {
      throw StateError('WavWriter 已 close，不能再 write16BitPcm');
    }
    _out.writeFromSync(pcm);
    _dataBytes += pcm.length;
  }

  /// 回写头里的 chunkSize(=36+dataBytes) 与 subchunk2Size(=dataBytes)，再关闭文件。
  ///
  /// 空录音（dataBytes==0）允许 close，产出 44 字节合法头、dataSize=0。
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    // 回写 RIFF chunkSize（偏移 4，u32 LE）与 data subchunk2Size（偏移 40）。
    _writeHeader(_header, dataBytes: _dataBytes);
    _out
      ..setPositionSync(4)
      ..writeFromSync(_header.buffer.asUint8List(4, 4))
      ..setPositionSync(40)
      ..writeFromSync(_header.buffer.asUint8List(40, 4));
    await _out.close();
  }

  /// 按当前格式把 44 字节 RIFF/WAVE 头写进 [hdr]（[dataBytes] 控制两个长度字段）。
  void _writeHeader(ByteData hdr, {required int dataBytes}) {
    final view = hdr.buffer.asUint8List();
    // 魔数与标签（ASCII）。
    view.setRange(0, 4, _ascii('RIFF'));
    view.setRange(8, 12, _ascii('WAVE'));
    view.setRange(12, 16, _ascii('fmt '));
    view.setRange(36, 40, _ascii('data'));
    hdr
      ..setUint32(4, 36 + dataBytes, Endian.little) // RIFF chunkSize
      ..setUint32(16, 16, Endian.little) // PCM fmt 子块长度
      ..setUint16(20, 1, Endian.little) // audioFormat = 1 (PCM)
      ..setUint16(22, channels, Endian.little)
      ..setUint32(24, sampleRateHz, Endian.little)
      ..setUint32(
          28,
          sampleRateHz * channels * (bitsPerSample ~/ 8),
          Endian.little) // byteRate
      ..setUint16(32, channels * (bitsPerSample ~/ 8), Endian.little)
      ..setUint16(34, bitsPerSample, Endian.little)
      ..setUint32(40, dataBytes, Endian.little); // data subchunk2Size
  }

  static List<int> _ascii(String s) => s.codeUnits;
}
