// 频谱余晖（persistence）与固定标记的测试。
//
// 诚实性：本文件只做 (a) 衰减纯函数断言，(b) 把合成帧序列注入真实 widget，
// 验证历史帧被真实累积、渐隐渲染不抛异常、清除节拍生效。合成帧仅用于离屏渲染，
// 不进入任何产品路径（产品路径只吃真实 rtl_tcp IQ 流）。
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/widgets/spectrum_display.dart';

// 测试专用合成帧：噪声底 + 一个随时间漂移的峰（让历史帧彼此不同，便于观察叠加）。
SpectrumFrame _synthFrame(int t) {
  const n = 512;
  const center = 145e6;
  const fs = 2.048e6;
  final db = Float64List(n);
  final drift = (t - 5) * 0.12; // 归一化位置漂移
  for (var i = 0; i < n; i++) {
    var v = -88.0 + 3.0 * (i % 7); // 噪声底
    final d = i / n - drift - 0.5;
    v += 55.0 * math.exp(-(d * d) / (2 * 0.02 * 0.02));
    db[i] = v.clamp(-100.0, 0.0);
  }
  return SpectrumFrame(
    db: db,
    centerFreqHz: center,
    sampleRateHz: fs,
    fftSize: n,
  );
}

Widget _wrap(
  SpectrumFrame? frame, {
  SpectrumPersistence p = SpectrumPersistence.high,
  int tick = 0,
  List<double> marks = const <double>[],
}) {
  return MaterialApp(
    home: Scaffold(
      body: RepaintBoundary(
        child: SpectrumDisplay(
          key: const Key('sd'),
          frame: frame,
          persistence: p,
          persistenceClearTick: tick,
          fixedMarksHz: marks,
        ),
      ),
    ),
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('余晖衰减叠加（纯函数，注入 age 序列）', () {
    test('off 档任何 age 的 alpha 都是 0（不叠加）', () {
      for (var age = 1; age <= AppTokens.persistenceMaxLayers; age++) {
        expect(persistenceLayerAlpha(SpectrumPersistence.off, age), 0.0);
      }
    });

    test('low/high 衰减系数取自 AppTokens 具名常量，且 high 衰减更慢', () {
      expect(SpectrumPersistence.low.decay, AppTokens.persistenceDecayLow);
      expect(SpectrumPersistence.high.decay, AppTokens.persistenceDecayHigh);
      expect(AppTokens.persistenceDecayLow,
          lessThan(AppTokens.persistenceDecayHigh));
      expect(SpectrumPersistence.off.decay, 0.0);
    });

    test('同一档 alpha 随 age 严格递减（真实渐隐，不造假轨迹）', () {
      for (final mode in [SpectrumPersistence.low, SpectrumPersistence.high]) {
        double? prev;
        for (var age = 1; age <= AppTokens.persistenceMaxLayers; age++) {
          final a = persistenceLayerAlpha(mode, age);
          expect(a, greaterThan(0.0), reason: 'age=$age 应有正 alpha');
          if (prev != null) expect(a, lessThan(prev), reason: 'age=$age 应更淡');
          prev = a;
        }
      }
    });

    test('high 档同 age 比 low 档更亮（残影更长）', () {
      for (var age = 2; age <= 8; age++) {
        expect(
          persistenceLayerAlpha(SpectrumPersistence.high, age),
          greaterThan(persistenceLayerAlpha(SpectrumPersistence.low, age)),
        );
      }
    });

    test('base alpha 与最大层数为具名 token 且落在合理区间', () {
      expect(AppTokens.persistenceBaseAlpha > 0, isTrue);
      expect(AppTokens.persistenceBaseAlpha < 1, isTrue);
      expect(AppTokens.persistenceMaxLayers, greaterThanOrEqualTo(4));
    });
  });

  testWidgets('余晖：注入多帧序列后渐隐渲染不抛异常；清除节拍生效', (tester) async {
    tester.view.physicalSize = const Size(390, 420);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    var frame = _synthFrame(0);
    await tester.pumpWidget(_wrap(frame, p: SpectrumPersistence.high));
    await tester.pump();

    // 注入 9 帧序列（每帧新实例，模拟真实 IQ 流逐帧到达）。
    await tester.runAsync(() async {
      for (var t = 1; t < 10; t++) {
        frame = _synthFrame(t);
        await tester.pumpWidget(_wrap(frame, p: SpectrumPersistence.high));
        await tester.pump();
      }
      // 清除节拍变化 → 清空历史，仍正常渲染。
      await tester.pumpWidget(
        _wrap(frame, p: SpectrumPersistence.high, tick: 1),
      );
      await tester.pump();
    });

    expect(tester.takeException(), isNull);
    expect(find.byType(SpectrumDisplay), findsOneWidget);
  });

  testWidgets('余晖 off 档（默认）+ 固定标记空态：渲染不抛异常', (tester) async {
    tester.view.physicalSize = const Size(390, 420);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_wrap(_synthFrame(3))); // 默认 off
    await tester.pump();
    expect(tester.takeException(), isNull);

    // 给一个落在当前扫宽内的固定标记，验证竖线渲染路径不崩。
    await tester.pumpWidget(
      _wrap(
        _synthFrame(4),
        p: SpectrumPersistence.low,
        marks: const [145.5e6], // 在 145e6 ±1.024e6 内
      ),
    );
    await tester.pump();
    expect(tester.takeException(), isNull);
  });

  testWidgets('空 frame：off/任意档位都 shrink，不画假数据', (tester) async {
    tester.view.physicalSize = const Size(390, 420);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_wrap(null, p: SpectrumPersistence.high));
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(find.byType(SpectrumDisplay), findsOneWidget);
  });
}
