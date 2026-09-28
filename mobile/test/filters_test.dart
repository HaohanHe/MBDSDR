// 滤波器通带/阻带能量自检（合成单音，无需硬件）。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/filters.dart';

void main() {
  const fs = 1.024e6;

  Float64x2List tone(double normFreq, int n) {
    final out = Float64x2List(n);
    for (var k = 0; k < n; k++) {
      final ph = 2 * math.pi * normFreq * k;
      out[k] = Float64x2(math.cos(ph), math.sin(ph));
    }
    return out;
  }

  double energy(Float64x2List x) {
    var e = 0.0;
    for (final v in x) {
      e += v.x * v.x + v.y * v.y;
    }
    return e;
  }

  test('低通：通带通过、阻带显著衰减', () {
    // 截止 0.1（归一化），129 抽头。
    final taps = firLowpassTaps(cutoffNorm: 0.1, numTaps: 129);
    final chan = Channelizer(
      taps: taps,
      decimation: 1,
      sampleRateHz: fs,
    );

    const n = 8192;
    // 通带内 0.05，阻带 0.30。
    final passIn = tone(0.05, n);
    final stopIn = tone(0.30, n);

    final passOut = chan.process(passIn);
    final stopOut = Channelizer(taps: taps, decimation: 1, sampleRateHz: fs)
        .process(stopIn);

    // 跳过滤波建立期（前 taps 个）。
    final passE = energy(passOut.sublist(200));
    final stopE = energy(stopOut.sublist(200));

    // 通带能量应明显大于阻带（>= 100 倍，即 20dB 以上）。
    expect(passE / stopE, greaterThan(100));
  });

  test('FIR 抽头直流增益归一化到 1', () {
    final taps = firLowpassTaps(cutoffNorm: 0.1, numTaps: 65);
    var sum = 0.0;
    for (final t in taps) {
      sum += t;
    }
    expect(sum, closeTo(1.0, 1e-6));
  });
}
