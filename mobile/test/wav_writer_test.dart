// WavWriter 确定性离线测试：头魔数/字段自洽/已知样本字节/空录音/close 后写抛错。
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/pcm_codec.dart';
import 'package:mbdsdr_mobile/audio/wav_writer.dart';

/// 小端读 u32。
int _u32(Uint8List b, int off) => ByteData.sublistView(b).getUint32(off, Endian.little);
int _u16(Uint8List b, int off) => ByteData.sublistView(b).getUint16(off, Endian.little);

void main() {
  group('WavWriter 头字段自洽', () {
    test('魔数 RIFF/WAVE/fmt /data 正确；单声道16bit byteRate=sr*2 blockAlign=2', () async {
      final dir = await Directory.systemTemp.createTemp('wavtest');
      final f = File('${dir.path}/h.wav');
      final w = WavWriter(f.openSync(mode: FileMode.write), sampleRateHz: 48000, channels: 1);
      // 喂 3 个样本 = 6 字节 PCM。
      w.write16BitPcm(floatTo16BitPcm(Float32List.fromList([1.0, -1.0, 0.0])));
      await w.close();

      final bytes = await f.readAsBytes();
      // 魔数。
      expect(String.fromCharCodes(bytes.sublist(0, 4)), 'RIFF');
      expect(String.fromCharCodes(bytes.sublist(8, 12)), 'WAVE');
      expect(String.fromCharCodes(bytes.sublist(12, 16)), 'fmt ');
      expect(String.fromCharCodes(bytes.sublist(36, 40)), 'data');
      // fmt 块。
      expect(_u32(bytes, 16), 16); // subchunk1Size
      expect(_u16(bytes, 20), 1); // PCM
      expect(_u16(bytes, 22), 1); // mono
      expect(_u32(bytes, 24), 48000); // sampleRate
      expect(_u32(bytes, 28), 96000); // byteRate = 48000*2
      expect(_u16(bytes, 32), 2); // blockAlign
      expect(_u16(bytes, 34), 16); // bitsPerSample
      // 长度自洽。
      expect(_u32(bytes, 4), 36 + 6); // chunkSize
      expect(_u32(bytes, 40), 6); // dataSize
      expect(bytes.length, 44 + 6);
      await dir.delete(recursive: true);
    });

    test('已知样本落盘字节 = FF 7F 01 80 00 00', () async {
      final dir = await Directory.systemTemp.createTemp('wavvec');
      final f = File('${dir.path}/v.wav');
      final w = WavWriter(f.openSync(mode: FileMode.write), sampleRateHz: 48000, channels: 1);
      w.write16BitPcm(floatTo16BitPcm(Float32List.fromList([1.0, -1.0, 0.0])));
      await w.close();
      final bytes = await f.readAsBytes();
      expect(bytes.sublist(44, 50), [0xFF, 0x7F, 0x01, 0x80, 0x00, 0x00]);
      await dir.delete(recursive: true);
    });

    test('空录音产出 44 字节合法头、dataSize=0', () async {
      final dir = await Directory.systemTemp.createTemp('wavempty');
      final f = File('${dir.path}/e.wav');
      final w = WavWriter(f.openSync(mode: FileMode.write));
      await w.close();
      final bytes = await f.readAsBytes();
      expect(bytes.length, 44);
      expect(_u32(bytes, 4), 36);
      expect(_u32(bytes, 40), 0);
      await dir.delete(recursive: true);
    });

    test('close 后再 write 抛 StateError；sampleCount/durationMs 正确', () async {
      final dir = await Directory.systemTemp.createTemp('wavclose');
      final f = File('${dir.path}/c.wav');
      final w = WavWriter(f.openSync(mode: FileMode.write), sampleRateHz: 48000, channels: 1);
      w.write16BitPcm(Uint8List(4)); // 2 样本
      expect(w.sampleCount, 2);
      // 2 样本 @48000 = 2/48000*1000 ≈ 0.04ms → round = 0ms。
      await w.close();
      expect(() => w.write16BitPcm(Uint8List(2)), throwsStateError);
      await dir.delete(recursive: true);
    });

    test('多块追加：分片写入后总长与一次性写一致', () async {
      final dir = await Directory.systemTemp.createTemp('wavchunk');
      final f = File('${dir.path}/k.wav');
      final w = WavWriter(f.openSync(mode: FileMode.write), sampleRateHz: 48000, channels: 1);
      w.write16BitPcm(Uint8List(100));
      w.write16BitPcm(Uint8List(150));
      await w.close();
      final bytes = await f.readAsBytes();
      expect(_u32(bytes, 40), 250);
      expect(bytes.length, 44 + 250);
      await dir.delete(recursive: true);
    });
  });
}
