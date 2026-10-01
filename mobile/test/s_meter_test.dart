// S-meter 纯函数与渲染测试：S 单位映射、峰值保持慢回落、有/无数据渲染与空态。
// 电平来源 = 真实频谱帧（dBFS），测试用合成帧仅喂离屏渲染。
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/widgets/s_meter.dart';

SpectrumFrame _frame(List<double> db) {
  return SpectrumFrame(
    db: Float64List.fromList(db),
    centerFreqHz: 145e6,
    sampleRateHz: 2.048e6,
    fftSize: db.length,
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('spectrumLevelFromFrame（真实量测）', () {
    test('信号=峰值、噪声底=中位数', () {
      // 10 个 bin：多数在 -80，一个尖峰 -40，一个底噪 -95。
      final lvl = spectrumLevelFromFrame(_frame([
        -80, -80, -80, -80, -80, -80, -80, -80, -95, -40,
      ]));
      expect(lvl.signalDbfs, -40);
      expect(lvl.noiseFloorDbfs, -80);
    });
  });

  group('sUnitsAboveNoise（标准 6 dB/档，截断 0..9）', () {
    test('高出噪声底 12 dB → S2；低于噪声底 → S0', () {
      expect(sUnitsAboveNoise(-68, -80), 2);
      expect(sUnitsAboveNoise(-82, -80), 0);
    });
    test('强信号截断到 S9', () {
      expect(sUnitsAboveNoise(0, -80), 9); // 80 dB → 远超 9 档。
    });
  });

  group('PeakHold（克制慢回落）', () {
    test('更强则刷新；更弱则按 dB/s 回落但不低于当前信号', () {
      final h = PeakHold(decayDbPerSec: 4);
      expect(h.update(-40, 0), -40);
      expect(h.update(-60, 1), closeTo(-44, 1e-9)); // 回落 4 dB。
      expect(h.update(-60, 5), -60); // 落到当前信号即止。
    });
    test('reset 后清零', () {
      final h = PeakHold();
      h.update(-30, 0);
      h.reset();
      expect(h.update(-80, 0), -80);
    });
  });

  testWidgets('无信号 → 诚实空态「无信号」', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: SMeter()),
    ));
    expect(find.text('无信号'), findsOneWidget);
    expect(find.textContaining('dBFS'), findsNothing);
  });

  testWidgets('有数据 → 显示 S 档与 dBFS 读数', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: SMeter(signalDbfs: -68, noiseFloorDbfs: -80),
      ),
    ));
    await tester.pump(const Duration(milliseconds: 400)); // 触发回落 tick。
    expect(find.text('S2'), findsOneWidget);
    expect(find.textContaining('dBFS'), findsOneWidget);
    // 卸载停 ticker。
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
