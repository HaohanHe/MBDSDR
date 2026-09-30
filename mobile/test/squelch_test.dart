// 静噪门控单测：用真实形态的音频帧（正弦/静音）注入 SquelchGate，
// 由真实 RMS dBFS 计算驱动开合判定，不 mock 判定结果本身。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/squelch.dart';

const double _rate = 48000.0;

/// 一帧 N 样本的等幅正弦，RMS = amp/sqrt2（真实形态电平）。
Float32List _sine(double amp, int n) {
  final x = Float32List(n);
  for (var k = 0; k < n; k++) {
    x[k] = amp * math.sin(2 * math.pi * k / n);
  }
  return x;
}

/// 一帧静音。
Float32List _silence(int n) => Float32List(n);

void main() {
  // 块长取 100ms（4800 样本），使 attack/decay/hangover 时间常数可观测。
  const int n = 4800;

  group('默认与开关', () {
    test('默认门限 = 噪声底 + 裕量，落在 token 区间内', () {
      expect(AppTokens.squelchDefaultThresholdDb,
          AppTokens.squelchNoiseFloorDb + AppTokens.squelchDefaultAboveNoiseDb);
      expect(AppTokens.squelchDefaultThresholdDb,
          inInclusiveRange(AppTokens.squelchThresholdMinDb, AppTokens.squelchThresholdMaxDb));
    });

    test('关闭时恒开门：无论电平强弱都直通', () {
      final gate = SquelchGate()..enabled = false;
      expect(gate.process(_silence(n), audioSampleRateHz: _rate), isTrue);
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);
      expect(gate.open, isTrue);
    });
  });

  group('门控开合判定（真实 RMS 电平）', () {
    test('静音 → 关门；强信号 → 开门；信号消失经 hangover 后关门', () {
      final gate = SquelchGate()
        ..enabled = true
        ..thresholdDb = -50
        ..reset();

      // 静音：远低于门限，应关门。
      expect(gate.process(_silence(n), audioSampleRateHz: _rate), isFalse);
      expect(gate.open, isFalse);
      expect(gate.levelDb, lessThan(gate.thresholdDb));

      // 强信号：RMS ≈ -13.5 dB，远高于 -50 → 立即开门。
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);
      expect(gate.open, isTrue);
      expect(gate.levelDb, greaterThan(gate.thresholdDb));

      // 信号消失：第一帧静音仍在 hangover(200ms) 内，保持开门。
      expect(gate.process(_silence(n), audioSampleRateHz: _rate), isTrue);
      expect(gate.open, isTrue);

      // 第二帧静音：hangover 耗尽 → 关门。
      expect(gate.process(_silence(n), audioSampleRateHz: _rate), isFalse);
      expect(gate.open, isFalse);
    });

    test('弱信号（电平在门限之下）保持关门，不跳开', () {
      final gate = SquelchGate()
        ..enabled = true
        ..thresholdDb = -50
        ..reset();

      // amp=0.004 → RMS≈0.00283 → dB≈-51，略低于门限。
      for (var i = 0; i < 5; i++) {
        expect(gate.process(_sine(0.004, n), audioSampleRateHz: _rate), isFalse);
      }
      expect(gate.open, isFalse);
    });

    test('提高门限使强信号也关门（门限越严越不开）', () {
      final gate = SquelchGate()
        ..enabled = true
        ..thresholdDb = -50
        ..reset();

      // 先让强信号开门。
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);

      // 把门限拉到比信号还高（-10 dB，很严）。电平虽仍高，但已不满足
      // 「电平 >= 门限」，开始消耗 hangover：第一块 hangover 内仍开门。
      gate.thresholdDb = -10;
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);
      // 第二块 hangover(200ms) 耗尽 → 关门。
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isFalse);
      expect(gate.open, isFalse);
    });
  });

  group('rmsDbfs 量测', () {
    test('空/静音 → 下限；等幅正弦 → 期望 RMS dB', () {
      expect(SquelchGate.rmsDbfs(Float32List(0)), SquelchGate.minMeasuredDb);
      expect(SquelchGate.rmsDbfs(_silence(n)), SquelchGate.minMeasuredDb);
      // amp=1.0 正弦 RMS=1/sqrt2≈0.707 → dB≈-3.01。
      expect(SquelchGate.rmsDbfs(_sine(1.0, n)), closeTo(-3.01, 0.5));
    });
  });
}
