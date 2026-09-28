// FFT 频率轴与峰值定位自检（合成复正弦，无需硬件）。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';

void main() {
  const fs = 1.024e6;
  const N = 1024;

  // 生成复正弦 e^{i 2π f n/fs}。
  (Float64List, Float64List) tone(double freq, int n) {
    final i = Float64List(n);
    final q = Float64List(n);
    for (var k = 0; k < n; k++) {
      final ph = 2 * math.pi * freq * k / fs;
      i[k] = math.cos(ph);
      q[k] = math.sin(ph);
    }
    return (i, q);
  }

  test('+100 kHz 偏移峰值落在期望频率一个 bin 内', () {
    final p = FftProcessor(fftSize: N);
    final (i, q) = tone(100000, N);
    final frame = p.compute(i, q, centerFreqHz: 0, sampleRateHz: fs);

    // 找峰值 bin。
    var peakBin = 0;
    var peakDb = double.negativeInfinity;
    for (var k = 0; k < frame.db.length; k++) {
      if (frame.db[k] > peakDb) {
        peakDb = frame.db[k];
        peakBin = k;
      }
    }
    final peakHz = frame.binToHz(peakBin);
    expect((peakHz - 100000).abs(), lessThanOrEqualTo(frame.binWidthHz));
  });

  test('频率轴两端正确', () {
    final p = FftProcessor(fftSize: N);
    final (i, q) = tone(0, N);
    final frame = p.compute(i, q, centerFreqHz: 100e6, sampleRateHz: fs);
    // 最低 bin = center - fs/2；最高 bin = center + fs/2 - binWidth。
    expect(frame.binToHz(0), closeTo(100e6 - fs / 2, 1e-6));
    expect(
      frame.binToHz(N - 1),
      closeTo(100e6 + fs / 2 - fs / N, 1e-6),
    );
  });

  test('直流信号在中心 bin', () {
    final p = FftProcessor(fftSize: N);
    final i = Float64List(N)
      ..setAll(0, List.filled(N, 1.0));
    final q = Float64List(N);
    final frame = p.compute(i, q, centerFreqHz: 100e6, sampleRateHz: fs);
    var peakBin = 0;
    var peakDb = double.negativeInfinity;
    for (var k = 0; k < frame.db.length; k++) {
      if (frame.db[k] > peakDb) {
        peakDb = frame.db[k];
        peakBin = k;
      }
    }
    expect(peakBin, N ~/ 2);
    expect(frame.binToHz(peakBin), closeTo(100e6, 1e-6));
  });

  test('非法 FFT 大小抛错', () {
    expect(() => FftProcessor(fftSize: 1000), throwsArgumentError);
  });
}
