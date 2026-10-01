// 固定标记交互（点选 / 拖动 snap / 键盘微调 / 删除 / 持久化回调）与步长纯函数测试。
//
// 帧为测试专用合成帧（仅离屏渲染，不进产品路径）。回调断言验证「交互即持久化」：
// onMarkChanged(old,new) / onDeleteMark(hz) 被立即触发。
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/widgets/spectrum_display.dart';

SpectrumFrame _frame() {
  const n = 512;
  final db = Float64List(n);
  for (var i = 0; i < n; i++) {
    db[i] = (-80.0 + 20.0 * (i == n ~/ 2 ? 1 : 0)).clamp(-100.0, 0.0);
  }
  return SpectrumFrame(
    db: db,
    centerFreqHz: 145e6,
    sampleRateHz: 2.048e6,
    fftSize: n,
  );
}

Widget _wrap({
  required List<double> marks,
  void Function(double hz)? onTapFreq,
  void Function(double oldHz, double newHz)? onMarkChanged,
  void Function(double hz)? onDeleteMark,
}) {
  return MaterialApp(
    home: Scaffold(
      body: RepaintBoundary(
        child: SpectrumDisplay(
          frame: _frame(),
          onTapFrequency: onTapFreq,
          fixedMarksHz: marks,
          onMarkChanged: onMarkChanged,
          onDeleteMark: onDeleteMark,
        ),
      ),
    ),
  );
}

/// 固定视口，使绘图区从原点开始，坐标可按 gutter 几何推算。
void _useView(WidgetTester tester) {
  tester.view.physicalSize = const Size(360, 300);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('缩放粒度步长（纯函数）', () {
    test('adaptiveFreqStepHz 选最细可放下步长；snap 对齐网格', () {
      // 2.048MHz 扫宽、288px 宽：500k 刚好放下（>=64px），250k 放不下。
      expect(adaptiveFreqStepHz(2.048e6, 288), 5e5);
      expect(snapToFreqStep(145.123e6, 5e5), 145.0e6);
      expect(snapToFreqStep(145.6e6, 5e5), 145.5e6);
    });
  });

  testWidgets('点空白处调 VFO；点中标记不调 VFO（进入选中态）', (tester) async {
    _useView(tester);
    double? tapped;
    await tester.pumpWidget(_wrap(
      marks: const [145e6],
      onTapFreq: (hz) => tapped = hz,
    ));
    await tester.pump();

    // 绘图区原点 x = 左 gutter(48)，中心标记在 plot 中心 = 48+144=192。
    // 点空白（远离中心）→ 调 VFO。
    await tester.tapAt(const Offset(60, 72));
    await tester.pump();
    expect(tapped, isNotNull);

    // 点中心标记 → 不调 VFO。
    tapped = null;
    await tester.tapAt(const Offset(192, 72));
    await tester.pump();
    expect(tapped, isNull, reason: '命中标记应选中而非调 VFO');
  });

  testWidgets('选中后键盘 → 微调一步（=缩放粒度）并立即回调持久化', (tester) async {
    _useView(tester);
    double? movedFrom;
    double? movedTo;
    await tester.pumpWidget(_wrap(
      marks: const [145e6],
      onMarkChanged: (o, n) {
        movedFrom = o;
        movedTo = n;
      },
    ));
    await tester.pump();

    // 选中中心标记。
    await tester.tapAt(const Offset(192, 72));
    await tester.pump();

    // 键盘右移一步（步长 500k）。
    await tester.sendKeyDownEvent(LogicalKeyboardKey.arrowRight);
    await tester.pump();
    expect(movedFrom, 145e6);
    expect(movedTo, 145e6 + adaptiveFreqStepHz(2.048e6, 288));
  });

  testWidgets('选中后 Delete 删除并立即回调', (tester) async {
    _useView(tester);
    double? deleted;
    await tester.pumpWidget(_wrap(
      marks: const [145e6],
      onDeleteMark: (hz) => deleted = hz,
    ));
    await tester.pump();

    await tester.tapAt(const Offset(192, 72));
    await tester.pump();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.delete);
    await tester.pump();
    expect(deleted, 145e6);
  });

  testWidgets('拖动标记：落点 snap 到步长并连续回调', (tester) async {
    _useView(tester);
    final moves = <List<double>>[];
    await tester.pumpWidget(_wrap(
      marks: const [145e6],
      onMarkChanged: (o, n) => moves.add([o, n]),
    ));
    await tester.pump();

    // 从中心标记向右水平拖过触滑，落点 snap 到 500k 网格。
    await tester.dragFrom(const Offset(192, 72), const Offset(108, 0));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    expect(moves, isNotEmpty, reason: '拖动应触发持久化回调');
    // 落点频率被 snap 到 500k 整数倍。
    final last = moves.last;
    expect(last[1] % 5e5, closeTo(0, 1e-6));
  });
}
