import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/null_pcm_sink.dart';
import 'package:mbdsdr_mobile/audio/pcm_codec.dart';
import 'package:mbdsdr_mobile/audio/recording_pcm_sink.dart';

/// 以小端读出第 i 个有符号 16-bit 样本。
int _readI16Le(Uint8List pcm, int i) =>
    ByteData.sublistView(pcm).getInt16(i * 2, Endian.little);

void main() {
  group('floatTo16BitPcm（Float32 → 有符号 16-bit 小端 PCM）', () {
    test('字节数 = 样本数 * 2', () {
      final samples = Float32List.fromList([0.0, 0.1, -0.2, 0.9, -1.0]);
      final pcm = floatTo16BitPcm(samples);
      expect(pcm.length, samples.length * 2);
    });

    test('+1 / -1 样本 clamp 到 ±32767', () {
      final pcm = floatTo16BitPcm(Float32List.fromList([1.0, -1.0]));
      expect(_readI16Le(pcm, 0), 32767);
      expect(_readI16Le(pcm, 1), -32767);
    });

    test('超出 [-1,1] 的样本同样 clamp，不溢出 16-bit', () {
      final pcm = floatTo16BitPcm(Float32List.fromList([2.0, -2.0, 10.0]));
      expect(_readI16Le(pcm, 0), 32767);
      expect(_readI16Le(pcm, 1), -32767);
      expect(_readI16Le(pcm, 2), 32767);
    });

    test('0 样本映射为 0', () {
      final pcm = floatTo16BitPcm(Float32List.fromList([0.0]));
      expect(_readI16Le(pcm, 0), 0);
      expect(pcm[0], 0);
      expect(pcm[1], 0);
    });

    test('volume=0.5 线性缩放', () {
      // volume=1：0.5 -> round(0.5*32767)=round(16383.5)=16384
      final full = floatTo16BitPcm(Float32List.fromList([0.5]));
      expect(_readI16Le(full, 0), 16384);
      // volume=0.5：0.5*0.5=0.25 -> round(0.25*32767)=round(8191.75)=8192 ≈ 一半
      final half = floatTo16BitPcm(Float32List.fromList([0.5]), volume: 0.5);
      expect(_readI16Le(half, 0), 8192);
    });

    test('muted=true 时整块全零', () {
      final samples = Float32List.fromList([0.8, -0.6, 0.3, 1.0]);
      final pcm = floatTo16BitPcm(samples, muted: true);
      expect(pcm.length, samples.length * 2);
      for (var b in pcm) {
        expect(b, 0);
      }
    });

    test('小端字节序正确（已知样本）', () {
      // 1.0 -> 32767 = 0x7FFF -> 小端 [0xFF, 0x7F]
      final pos = floatTo16BitPcm(Float32List.fromList([1.0]));
      expect(pos[0], 0xFF);
      expect(pos[1], 0x7F);

      // -1.0 -> -32767 = 0x8001 -> 小端 [0x01, 0x80]
      final neg = floatTo16BitPcm(Float32List.fromList([-1.0]));
      expect(neg[0], 0x01);
      expect(neg[1], 0x80);
    });
  });

  group('RecordingPcmSink', () {
    test('start 记录采样率/声道；write 逐帧记录长度与内容；状态可观测', () async {
      final sink = RecordingPcmSink();
      expect(sink.started, false);

      await sink.start(sampleRateHz: 48000, channels: 1);
      expect(sink.started, true);
      expect(sink.sampleRateHz, 48000);
      expect(sink.channels, 1);
      expect(sink.volume, 1.0);
      expect(sink.muted, false);

      final f1 = Float32List.fromList([0.1, 0.2, 0.3]);
      final f2 = Float32List.fromList([0.4, 0.5]);
      final f3 = Float32List.fromList(List.filled(256, 0.0));
      sink.write(f1);
      sink.write(f2);
      sink.write(f3);

      expect(sink.recorded.length, 3);
      expect(sink.recorded[0].length, 3);
      expect(sink.recorded[1].length, 2);
      expect(sink.recorded[2].length, 256);
      expect(sink.recorded[0], f1);

      sink.setVolume(0.4);
      sink.setMuted(true);
      expect(sink.volume, 0.4);
      expect(sink.muted, true);

      await sink.dispose();
      expect(sink.started, false);
    });

    test('write 是深拷贝：改写原始缓冲不影响已记录帧', () {
      final sink = RecordingPcmSink();
      final f = Float32List.fromList([0.1, 0.2, 0.3]);
      sink.write(f);
      f[0] = 9.9; // 改写原始
      // 记录仍是旧值（float32 精度内比较），且不是改写后的 9.9。
      expect(sink.recorded.single[0], closeTo(0.1, 1e-6));
      expect(sink.recorded.single[0], isNot(9.9));
    });
  });

  group('解调 → PCM 缓冲喂入 sink 链路', () {
    test('模拟 NFM/WFM 解调输出：codec → RecordingPcmSink，格式与量程正确', () async {
      // 模拟一段解调后的单声道 Float32 音频（48kHz），含正/负/满幅/超幅样本。
      final demodOut = Float32List.fromList([
        0.0, 0.25, -0.25, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 0.99,
      ]);

      final sink = RecordingPcmSink();
      await sink.start(sampleRateHz: 48000, channels: 1);
      sink.write(demodOut);

      // 断言 sink 侧观测到的格式：mono + 48kHz。
      expect(sink.channels, 1);
      expect(sink.sampleRateHz, 48000);
      expect(sink.recorded.length, 1);

      // 对记录到的帧做 16-bit 小端量化。
      final frame = sink.recorded.single;
      final pcm = floatTo16BitPcm(frame);

      // 16bit 小端 mono：字节数 = 样本数*2。
      expect(pcm.length, frame.length * 2);

      // 所有样本量化后不溢出 16-bit（[-32767, 32767]）。
      for (var i = 0; i < frame.length; i++) {
        final q = _readI16Le(pcm, i);
        expect(q, inInclusiveRange(-32767, 32767), reason: '样本 $i 溢出');
      }

      // 已知点抽查：0 → 0；1.5（超幅）clamp 到 32767。
      expect(_readI16Le(pcm, 0), 0); // 0.0
      expect(_readI16Le(pcm, 5), 32767); // 1.0
      expect(_readI16Le(pcm, 6), -32767); // -1.0
      expect(_readI16Le(pcm, 7), 32767); // 1.5 clamp
      expect(_readI16Le(pcm, 8), -32767); // -1.5 clamp
    });
  });

  group('NoOpSink', () {
    test('start/write/setVolume/setMuted/dispose 均不抛异常', () async {
      final sink = NoOpSink();
      await sink.start(sampleRateHz: 48000, channels: 1);
      sink.write(Float32List.fromList([0.0, 0.5, -0.5]));
      sink.setVolume(0.7);
      sink.setMuted(true);
      await sink.dispose();
      // 走到这里即视为通过（无异常）。
      expect(sink.isStarted, false);
    });
  });
}
