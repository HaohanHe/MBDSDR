// W2a 渲染器对齐修复的确定性 widget / 纯函数测试。
//
// 覆盖冻结决策：
//  ① trace 与瀑布共用同一 bin-center 映射（binCentreX，去 500 点重采样+三点平滑）；
//  ② 占位 flex 由 5:4 改 1:1（对齐桌面 traceShare_=0.5）；
//  ④ BW 阴影带 trace+瀑布双画（bandBandEdges 共用公式，瀑布 painter 收到 frame）；
//  ③ 余晖 on→off 清空历史（行为不抛异常；衰减纯函数见 spectrum_persistence_test）。
// 合成帧仅用于离屏渲染断言，不进入产品路径（产品只吃真实 rtl_tcp IQ 流）。
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/widgets/spectrum_display.dart';

SpectrumFrame _frame() {
  const n = 256;
  final db = Float64List(n);
  for (var i = 0; i < n; i++) {
    // 一个尖峰落在 bin 100，其余噪声底。
    db[i] = (i == 100) ? 0.0 : -90.0;
  }
  return SpectrumFrame(
    db: db,
    centerFreqHz: 145e6,
    sampleRateHz: 2.048e6,
    fftSize: n,
  );
}

Widget _wrap(SpectrumFrame frame,
        {SpectrumPersistence p = SpectrumPersistence.off}) =>
    MaterialApp(
      home: Scaffold(
        body: RepaintBoundary(
          child: SpectrumDisplay(
            key: const Key('sd'),
            frame: frame,
            persistence: p,
            channelBandwidthHz: 12500,
            fixedMarksHz: const [],
          ),
        ),
      ),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('bin-center 映射（trace 与瀑布共用）', () {
    test('binCentreX 取 bin 中心而非左边缘', () {
      const n = 256.0;
      const w = 300.0;
      // bin 0 的中心应落在 0.5/n*w，而不是 0（边缘映射）。
      expect(binCentreX(0, n.toInt(), w), closeTo(0.5 / n * w, 1e-9));
      // 末 bin 中心不越过右缘。
      expect(binCentreX(255, n.toInt(), w), closeTo(255.5 / n * w, 1e-9));
    });

    test('binCentreX 与瀑布逐列写入的列中心一致（同一 x）', () {
      const n = 256;
      const width = 300.0; // 逻辑宽
      const dpr = 3.0;
      const wdev = width * dpr; // 瀑布缓冲设备列数 _w
      // 瀑布 ingest: bin=floor(x_dev*n/_w)。bin p 占据列区间中心：
      //   x_dev = (p+0.5)*_w/n → 逻辑 = (p+0.5)/n*width = binCentreX(p,n,width)。
      for (final p in const [0, 50, 100, 200, 255]) {
        final colCenterLogical = (p + 0.5) / n * width;
        expect(binCentreX(p, n, width), closeTo(colCenterLogical, 1e-9),
            reason: 'bin $p 的 trace 顶点 x 必须落在瀑布列中心');
        // 且该设备列确实被染成 bin p（floor 映射一致）。
        final xDev = (p + 0.5) * wdev / n;
        expect((xDev * n / wdev).floor(), p);
      }
    });
  });

  group('BW 阴影带双画', () {
    test('bandBandEdges 居中对称、在绘图区内', () {
      final edges = bandBandEdges(
        channelBandwidthHz: 12500,
        binWidthHz: 2.048e6 / 256,
        n: 256,
        plotWidth: 300,
      );
      expect(edges.r, greaterThan(edges.l));
      expect((edges.l + edges.r) / 2, closeTo(150, 1e-9)); // 中心 = 绘图区中线
      expect(edges.l, greaterThanOrEqualTo(0));
      expect(edges.r, lessThanOrEqualTo(300));
    });

    testWidgets('瀑布 painter 存在并随 frame 渲染（BW 带可画在瀑布上）', (tester) async {
      tester.view.physicalSize = const Size(390, 420);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(_wrap(_frame()));
      await tester.pump();
      expect(tester.takeException(), isNull);

      // 找到 _WaterfallPainter（运行时类型名），确认它随 frame 挂载。
      final paints =
          tester.widgetList<CustomPaint>(find.byType(CustomPaint)).toList();
      CustomPaint? waterfall;
      for (final cp in paints) {
        if (cp.painter.runtimeType.toString() == '_WaterfallPainter') {
          waterfall = cp;
        }
      }
      expect(waterfall, isNotNull,
          reason: '瀑布 CustomPaint 必须存在（BW 带双画的承载层）');
    });
  });

  group('占位比例 1:1', () {
    testWidgets('trace 与瀑布 Expanded flex 相等（=1）', (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(_wrap(_frame()));
      await tester.pump();
      expect(tester.takeException(), isNull);

      // build 根是 Column[ Expanded(trace), SizedBox(strip), Expanded(falls) ]。
      final col = tester.widget<Column>(find
          .descendant(
              of: find.byType(SpectrumDisplay),
              matching: find.byType(Column))
          .first);
      final expanded = col.children.whereType<Expanded>().toList();
      expect(expanded.length, 2, reason: '根 Column 应有 trace/瀑布两个 Expanded');
      expect(expanded[0].flex, expanded[1].flex,
          reason: 'trace:瀑布 必须 1:1（flex 相等）');
      expect(expanded[0].flex, 1);
    });
  });

  group('余晖行为', () {
    testWidgets('high→off→high：切换不抛异常（on→off 清空历史路径）', (tester) async {
      tester.view.physicalSize = const Size(390, 420);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final f = _frame();
      await tester.pumpWidget(_wrap(f, p: SpectrumPersistence.high));
      await tester.pump();
      // 切 off（应清空 _history）。
      await tester.pumpWidget(_wrap(f, p: SpectrumPersistence.off));
      await tester.pump();
      // 再切回 high（旧残影不应复活，且不抛异常）。
      await tester.pumpWidget(_wrap(f, p: SpectrumPersistence.high));
      await tester.pump();
      expect(tester.takeException(), isNull);
    });
  });
}
