// NFM/WFM 解调自检（合成 FM 信号 → 解调 → FFT 找 1 kHz 音频主峰）。
// 标注：合成信号自检，非硬件。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:fftea/fftea.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/demod.dart';

/// 生成 FM 复信号：m(t)=sin(2π·fm·t)，φ=2π·fd·∫m dt，IQ=e^{iφ}。
Float64x2List fmSignal(double fs, double fd, double fm, int n) {
  final out = Float64x2List(n);
  for (var k = 0; k < n; k++) {
    final t = k / fs;
    // ∫m dt = -cos(2π fm t)/(2π fm)
    final phase = 2 * math.pi * fd * (-math.cos(2 * math.pi * fm * t) / (2 * math.pi * fm));
    out[k] = Float64x2(math.cos(phase), math.sin(phase));
  }
  return out;
}

/// 在音频里找主峰频率（Hz），跳直流与建立段。
(double peakHz, double peakMag) findPeak(Float32List audio, double rateHz) {
  // 去前几百个样本（滤波建立）。
  final trimmed = audio.sublist(audio.length > 500 ? 500 : 0);
  // 取 2 的幂长度。
  var n = 1;
  while (n * 2 <= trimmed.length) {
    n *= 2;
  }
  final reals = Float64List(n);
  for (var k = 0; k < n; k++) {
    reals[k] = trimmed[k];
  }
  final fft = FFT(n);
  final c = Float64x2List(n);
  for (var k = 0; k < n; k++) {
    c[k] = Float64x2(reals[k], 0);
  }
  fft.inPlaceFft(c);
  final mags = c.magnitudes();
  var peakBin = 1;
  var peak = 0.0;
  for (var k = 1; k < n ~/ 2; k++) {
    if (mags[k] > peak) {
      peak = mags[k];
      peakBin = k;
    }
  }
  return (peakBin * rateHz / n, peak);
}

void main() {
  test('NFM 合成信号自检，非硬件：恢复出 1 kHz 调制音', () {
    const fs = 1.024e6;
    final demod = NfmDemod(inputRateHz: fs);
    final sig = fmSignal(fs, 2500, 1000, (fs * 0.05).round());
    final audio = demod.process(sig);

    expect(audio.length, greaterThan(1000));
    // NFM 第一级抽取率约 51.2 ksps。
    final (peakHz, peakMag) = findPeak(audio, 51200);
    expect((peakHz - 1000).abs(), lessThanOrEqualTo(50),
        reason: '主峰应在 1000Hz±50Hz，实际 ${peakHz.toStringAsFixed(1)}Hz');
    expect(peakMag, greaterThan(0.01), reason: '主峰幅度应合理');
  });

  test('WFM 合成信号自检，非硬件：恢复出 1 kHz 调制音', () {
    const fs = 2.048e6;
    final demod = WfmDemod(inputRateHz: fs);
    final sig = fmSignal(fs, 50000, 1000, (fs * 0.05).round());
    final audio = demod.process(sig);

    expect(audio.length, greaterThan(500));
    // WFM 末级约 51.2 ksps。
    final (peakHz, peakMag) = findPeak(audio, 51200);
    expect((peakHz - 1000).abs(), lessThanOrEqualTo(50),
        reason: '主峰应在 1000Hz±50Hz，实际 ${peakHz.toStringAsFixed(1)}Hz');
    expect(peakMag, greaterThan(0.001), reason: '主峰幅度应合理');
  });
}
