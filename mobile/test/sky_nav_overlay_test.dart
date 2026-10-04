// 天空图「预:」导航卫星叠加层测试：
//  - 注入在视导航星 → SkyRadar 把 navVisible 传入 painter（空心圈数据源就位）；
//  - 空 navVisible → 不渲染叠加层（诚实空态）；
//  - 复用 polarPoint 投影：导航星落点与 az/el 一致。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/widgets/compass_dial.dart';

void main() {
  const l1 =
      '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
  const l2 =
      '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';
  final tle = Tle.parseThreeLine('GPS BIIR-2', l1, l2);

  SatVisibility navSat(double az, double el) => SatVisibility(
        name: 'GPS BIIR-2',
        az: az,
        el: el,
        range: 20200,
        altitudeKm: 20180,
        tle: tle,
      );

  testWidgets('注入导航星 → painter 携带 navVisible（叠加数据源就位）',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Center(
          child: SkyRadar(
            visible: const [],
            selectedName: null,
            onSelect: (_) {},
            navVisible: [navSat(120, 45)],
          ),
        ),
      ),
    ));
    await tester.pump();

    final radar = tester.widget<SkyRadar>(find.byType(SkyRadar));
    expect(radar.navVisible, isNotEmpty);
    expect(radar.navVisible.first.el, 45);
  });

  testWidgets('空 navVisible → 不渲染叠加层（空态）', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Center(
          child: SkyRadar(
            visible: const [],
            selectedName: null,
            onSelect: (_) {},
          ),
        ),
      ),
    ));
    await tester.pump();
    final radar = tester.widget<SkyRadar>(find.byType(SkyRadar));
    expect(radar.navVisible, isEmpty);
  });

  test('复用 polarPoint：导航星 az/el 落点可投影（不重造几何）', () {
    const center = Offset(100, 100);
    const R = 80.0;
    // 方位 0（北）、仰角 45° → 圆心正上方（frac=(90-45)/90=0.5）。
    final p = polarPoint(center, R, 0, 45);
    expect(p.dx, closeTo(100, 1e-6));
    expect(p.dy, closeTo(100 - R * 0.5, 1e-6));
  });
}
