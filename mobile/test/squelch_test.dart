// 静噪门控单测：用真实形态的音频帧（正弦/静音）注入 SquelchGate，
// 由真实 RMS dBFS 计算驱动开合判定，不 mock 判定结果本身。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/squelch.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';

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

  group('自动门限（同域噪声底跟踪）', () {
    // amp=0.01 正弦 RMS≈-43 dBFS：作「安静噪声底」参考电平。
    // amp=0.3  正弦 RMS≈-13.5 dBFS：作「强信号」参考电平。
    test('噪声底向安静快随、向响瞬变慢爬（真实信号不抬底）', () {
      final gate = SquelchGate()
        ..enabled = true
        ..reset();

      // 先建立 ≈ -43 dB 的安静底。
      for (var i = 0; i < 20; i++) {
        gate.process(_sine(0.01, n), audioSampleRateHz: _rate);
      }
      expect(gate.noiseFloorDb, inInclusiveRange(-46, -40));

      // 强信号过门限：底向响慢爬（alphaUp=0.005），10 块几乎不动。
      for (var i = 0; i < 10; i++) {
        gate.process(_sine(0.3, n), audioSampleRateHz: _rate);
      }
      expect(gate.noiseFloorDb, greaterThan(-42.5),
          reason: '真实信号不应明显抬升噪声底');

      // 转静音：底向安静快随（alphaDown=0.20），6 块内大幅回落。
      for (var i = 0; i < 6; i++) {
        gate.process(_silence(n), audioSampleRateHz: _rate);
      }
      expect(gate.noiseFloorDb, lessThan(-90),
          reason: '安静背景应快速被跟踪为新噪声底');
    });

    test('自动门限 = 实测噪声底 + 裕量：安静信道关门、强信号过门限开门', () {
      final gate = SquelchGate()
        ..enabled = true
        ..autoThreshold = true
        ..reset();

      // 建立安静噪声底 ≈ -43 dB。
      for (var i = 0; i < 20; i++) {
        gate.process(_sine(0.01, n), audioSampleRateHz: _rate);
      }
      expect(gate.noiseFloorDb, inInclusiveRange(-46, -40));
      // 自动门限 = 底 + 8dB ≈ -35 dB（clamp 到 token 区间）。
      expect(gate.thresholdDb,
          closeTo(gate.noiseFloorDb + AppTokens.squelchAutoMarginDb, 0.5));
      expect(gate.thresholdDb,
          inInclusiveRange(AppTokens.squelchThresholdMinDb, AppTokens.squelchThresholdMaxDb));

      // 安静噪声（-43）低于自动门限（-35）→ 保持关门。
      expect(gate.open, isFalse);

      // 强信号（-13.5）高于自动门限 → 开门。
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);
      expect(gate.open, isTrue);
    });

    test('关闭自动门限后回到手动门限（不再随噪声底改写）', () {
      final gate = SquelchGate()
        ..enabled = true
        ..autoThreshold = false
        ..thresholdDb = -50
        ..reset();

      // 手动门限固定 -50：强信号（-13.5）开门。
      expect(gate.process(_sine(0.3, n), audioSampleRateHz: _rate), isTrue);
      // 转安静后手动门限仍是 -50（不被自动逻辑改写）。
      gate.process(_silence(n), audioSampleRateHz: _rate);
      expect(gate.thresholdDb, -50);
    });
  });

  group('RadioController 自动门限接线', () {
    test('开启自动；手动拖门限即退出自动跟随', () {
      final c = RadioController();
      addTearDown(c.dispose);

      c.setSquelchAuto(true);
      expect(c.squelchAuto, isTrue);
      // 无音频时噪声底=下限(-120)，自动门限 clamp 到区间下限。
      expect(c.squelchThresholdDb, AppTokens.squelchThresholdMinDb);

      // 用户手动设门限 → 退出自动（避免噪声底与拖杆打架）。
      c.setSquelchThresholdDb(-55);
      expect(c.squelchAuto, isFalse, reason: '手动设门限即退出自动跟随');
      expect(c.squelchThresholdDb, -55);
    });
  });
}
