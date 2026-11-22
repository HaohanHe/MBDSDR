/// 指天视图：姿态 → 视场、目标指令、诚实空态。
///
/// 覆盖点
/// ------
/// * 纯函数指令：方位差折算「左/右转」，仰角差折算「抬头/低头」，±5° 判对准；
/// * 航向波动：圆周统计口径、跨 0°/360° 不算大波动、样本不足给 null；
/// * 手动对齐：会话内 ±5° 偏移参与指令/绘制，复位归零，状态条写「手动对齐」；
/// * 页面：有站址 + 有罗盘 → 画布 + 状态条 + 指令行；
/// * 诚实空态：无站址 / 无航向 / 资产读失败 都显示真实原因，不用 0° 假装朝北；
/// * 视场按钮按 60→25→8 循环。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/asterism_overlay.dart';
import 'package:mbdsdr_mobile/astro/sky_assets.dart';
import 'package:mbdsdr_mobile/astro/sky_transform.dart';
import 'package:mbdsdr_mobile/astro/star_names.dart';
import 'package:mbdsdr_mobile/pages/pointing_page.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';

/// 三颗星的小星表 + 一条星官线：够验证「有东西可画」，不拖慢测试。
SkyAssets _tinyAssets() {
  return const SkyAssets(
    stars: <SkyStar>[
      SkyStar(raDeg: 10.0, decDeg: 20.0, vmag: 1.0, bv: 0.2, hip: 1),
      SkyStar(raDeg: 12.0, decDeg: 22.0, vmag: 2.0, bv: 0.9, hip: 2),
      SkyStar(raDeg: 14.0, decDeg: 24.0, vmag: 3.0, bv: 1.4, hip: 3),
    ],
    catalog: AsterismCatalog(
      asterisms: <Asterism>[
        Asterism(name: '测试官', regionName: '紫微垣', polylines: <List<int>>[
          <int>[1, 2, 3],
        ]),
      ],
      regionNames: <String, String>{'ziwei': '紫微垣'},
      singleStarNames: <String>[],
      license: 'CC BY 4.0（测试）',
    ),
    license: 'CC BY 4.0（测试）',
  );
}

class _FakeAssetSource implements SkyAssetSource {
  _FakeAssetSource(this.assets);
  final SkyAssets assets;

  @override
  Future<SkyAssets> load() async => assets;
}

class _BrokenAssetSource implements SkyAssetSource {
  @override
  Future<SkyAssets> load() async {
    throw const SkyAssetException(kStarsAssetPath, '测试注入：文件缺失');
  }
}

DeviceOrientation _attitude({
  double? heading = 123,
  double? pitch = 45,
  double? roll = 0,
  bool hasCompass = true,
  bool hasImu = true,
}) =>
    DeviceOrientation(
      heading: heading,
      pitch: pitch,
      roll: roll,
      hasCompass: hasCompass,
      hasImu: hasImu,
    );

Widget _page({
  required Stream<DeviceOrientation> stream,
  SkySite? site = const SkySite(latDeg: 45, lonDeg: 120),
  SkyAssetSource? source,
  double? targetAz = 130,
  double? targetEl = 50,
}) {
  return MaterialApp(
    home: PointingPage(
      site: site,
      orientationStream: stream,
      assetSource: source ?? _FakeAssetSource(_tinyAssets()),
      targetAzDeg: targetAz,
      targetElDeg: targetEl,
      targetLabel: 'ISS',
      clock: () => DateTime.utc(2026, 11, 18, 12),
      minFrame: Duration.zero,
      sensorTimeout: const Duration(seconds: 10),
    ),
  );
}

/// 只匹配「指天视图自己的画布」，不把 Material/进度条自带的 CustomPaint 算进来。
Finder _pointingCanvas() => find.byWidgetPredicate(
      (Widget w) => w is CustomPaint && w.painter is PointingPainter,
    );

/// 连续喂入一串航向样本（每个事件走一次重建），再等两帧让窗口生效。
Future<void> _feedHeadings(
  WidgetTester tester,
  StreamController<DeviceOrientation> controller,
  List<double> headings, {
  double pitch = 45,
}) async {
  for (final double heading in headings) {
    controller.add(_attitude(heading: heading, pitch: pitch));
  }
  await tester.pump();
  await tester.pump();
}

/// 测试固定站址/时刻：与 `_page` 的 `clock` 一致，中心星才能算得出来。
const SkySite _testSite = SkySite(latDeg: 45, lonDeg: 120);
final DateTime _testWhen = DateTime.utc(2026, 11, 18, 12);

/// 造一颗「此刻位于 (az, alt)」的 J2000 恒星（用地平→J2000 逆变换）。
SkyStar _starAt({
  required double az,
  double alt = 45,
  required int hip,
  double vmag = 0.0,
}) {
  final ({double raDeg, double decDeg}) radec = j2000FromAltAz(
    azDeg: az,
    altDeg: alt,
    latDeg: _testSite.latDeg,
    lonDeg: _testSite.lonDeg,
    jdUt1: julianDateFromUtc(_testWhen),
  );
  return SkyStar(
    raDeg: radec.raDeg,
    decDeg: radec.decDeg,
    vmag: vmag,
    bv: 0.0,
    hip: hip,
  );
}

SkyAssets _assetsWith({
  required List<SkyStar> stars,
  Map<int, StarNameEntry> names = const <int, StarNameEntry>{},
  String namesError = '',
}) {
  return SkyAssets(
    stars: stars,
    catalog: const AsterismCatalog(
      asterisms: <Asterism>[],
      regionNames: <String, String>{},
      singleStarNames: <String>[],
      license: 'CC BY 4.0（测试）',
    ),
    license: 'CC BY 4.0（测试）',
    names: names,
    namesError: namesError,
  );
}

void main() {
  group('指令文案（纯函数）', () {
    test('目标在地平线下：不指挥，只说明等它升起', () {
      expect(
        pointingGuidanceText(
            headingDeg: 0, pitchDeg: 0, targetAzDeg: 90, targetElDeg: -3),
        contains('地平线下'),
      );
    });

    test('方位差正=向右、负=向左；仰角差正=抬头、负=低头', () {
      expect(
        pointingGuidanceText(
            headingDeg: 0, pitchDeg: 10, targetAzDeg: 30, targetElDeg: 30),
        '向右转 30° · 抬头 20°',
      );
      expect(
        pointingGuidanceText(
            headingDeg: 10, pitchDeg: 30, targetAzDeg: 350, targetElDeg: 10),
        '向左转 20° · 低头 20°',
      );
    });

    test('跨 0°/360° 取最短弧，不走 340° 远路', () {
      expect(
        pointingGuidanceText(
            headingDeg: 350, pitchDeg: 45, targetAzDeg: 10, targetElDeg: 45),
        '向右转 20° · 仰角已对准',
      );
    });

    test('±5° 内判「已对准」', () {
      expect(
        pointingGuidanceText(
            headingDeg: 100, pitchDeg: 45, targetAzDeg: 105, targetElDeg: 41),
        '方位已对准 · 仰角已对准',
      );
    });
  });

  group('航向波动（纯函数，圆周统计）', () {
    test('恒定航向 → 波动为 0', () {
      expect(headingSpreadDeg(List<double>.filled(20, 90.0)), 0.0);
    });

    test('已知抖动序列 → 落在合理区间（±2° 抖动给小角度波动）', () {
      final double? spread =
          headingSpreadDeg(<double>[100, 102, 98, 101, 99, 100]);
      expect(spread, isNotNull);
      expect(spread!, greaterThan(0.5));
      expect(spread, lessThan(3.0));
    });

    test('跨 0°/360° 的样本不被算成大波动', () {
      final double? spread =
          headingSpreadDeg(<double>[359, 1, 358, 2, 0, 359, 1, 0]);
      expect(spread, isNotNull);
      expect(spread!, lessThan(3.0));
    });

    test('样本 < 5 或含非有限值 → null，不拿一两个点编数字', () {
      expect(headingSpreadDeg(<double>[10, 11, 12, 13]), isNull);
      expect(
        headingSpreadDeg(<double>[10, 11, 12, double.nan, 14]),
        isNull,
      );
    });
  });

  group('页面', () {
    testWidgets('有站址 + 有罗盘：画布、状态条、指令行都在', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      controller.add(_attitude());
      await tester.pump();

      expect(find.text('指天视图'), findsOneWidget);
      expect(find.textContaining('方位 123°'), findsOneWidget);
      expect(find.textContaining('仰角 45°'), findsOneWidget);
      expect(find.textContaining('星 '), findsOneWidget);
      expect(_pointingCanvas(), findsOneWidget);
      expect(find.text('向右转 7° · 仰角已对准'), findsOneWidget);
    });

    testWidgets('无站址：诚实空态，不画天空', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream, site: null));
      await tester.pump();

      expect(find.text('未设置本站坐标'), findsOneWidget);
      expect(_pointingCanvas(), findsNothing);
    });

    testWidgets('没有航向角：明说罗盘不可用，不用 0° 假装朝北', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      controller.add(_attitude(heading: null, pitch: null, roll: null));
      await tester.pump();

      expect(find.text('罗盘不可用'), findsOneWidget);
      expect(_pointingCanvas(), findsNothing);
    });

    testWidgets('姿态还没来：先显示「正在读取」，超时后才判不可用', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();

      expect(find.text('正在读取罗盘/姿态…'), findsOneWidget);
      expect(find.text('罗盘不可用'), findsNothing);
    });

    testWidgets('资产读失败：显示真实原因，不退回空星表', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        source: _BrokenAssetSource(),
      ));
      await tester.pump();

      expect(find.text('星表资产不可用'), findsOneWidget);
      expect(find.textContaining('文件缺失'), findsOneWidget);
    });

    testWidgets('视场按钮按 60→25→8 循环', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      controller.add(_attitude());
      await tester.pump();

      final Finder button = find.byKey(const Key('pointing-fov-button'));
      expect(
        find.descendant(of: button, matching: find.text('视场 60°')),
        findsOneWidget,
      );
      await tester.tap(button);
      await tester.pump();
      expect(
        find.descendant(of: button, matching: find.text('视场 25°')),
        findsOneWidget,
      );
      await tester.tap(button);
      await tester.pump();
      expect(
        find.descendant(of: button, matching: find.text('视场 8°')),
        findsOneWidget,
      );
      await tester.tap(button);
      await tester.pump();
      expect(
        find.descendant(of: button, matching: find.text('视场 60°')),
        findsOneWidget,
      );
    });

    testWidgets('状态条的方位/仰角标注来源（罗盘观测 / 姿态观测）',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      controller.add(_attitude());
      await tester.pump();

      expect(find.text('方位 123°（罗盘观测）'), findsOneWidget);
      expect(find.text('仰角 45°（姿态观测）'), findsOneWidget);
    });

    testWidgets('样本不足：状态条写「罗盘精度：样本不足」', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.text('罗盘精度：样本不足'), findsOneWidget);
      expect(find.textContaining('近 2 s 波动'), findsNothing);
    });

    testWidgets('样本足够：状态条给观测波动，不写「误差/精度保证」', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      await _feedHeadings(
        tester,
        controller,
        <double>[122, 124, 123, 125, 121, 122, 124, 123],
      );

      expect(find.textContaining('近 2 s 波动'), findsOneWidget);
      expect(find.textContaining('误差'), findsNothing);
      expect(find.text('罗盘精度：样本不足'), findsNothing);
    });

    testWidgets('「右 5°」后指令随偏移变化；状态条出现「手动对齐 +5°」',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);
      expect(find.text('向右转 7° · 仰角已对准'), findsOneWidget);
      expect(find.text('未手动对齐'), findsOneWidget);

      await tester.tap(find.byKey(const Key('pointing-align-right')));
      await tester.pump();

      expect(find.text('向右转 7° · 仰角已对准'), findsNothing);
      expect(find.text('仰角已对准'), findsNothing);
      expect(find.text('手动对齐 +5°'), findsOneWidget);
      expect(find.textContaining('已进入目标天区'), findsOneWidget);
    });

    testWidgets('「左 5°」把方位差推大：向右转 7° → 向右转 12°',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      await tester.tap(find.byKey(const Key('pointing-align-left')));
      await tester.pump();

      expect(find.text('向右转 12° · 仰角已对准'), findsOneWidget);
      expect(find.text('手动对齐 -5°'), findsOneWidget);
    });

    testWidgets('「复位」回到原始航向与「未手动对齐」', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(stream: controller.stream));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      await tester.tap(find.byKey(const Key('pointing-align-right')));
      await tester.pump();
      await tester.tap(find.byKey(const Key('pointing-align-reset')));
      await tester.pump();

      expect(find.text('向右转 7° · 仰角已对准'), findsOneWidget);
      expect(find.text('未手动对齐'), findsOneWidget);
      expect(find.textContaining('手动对齐 +'), findsNothing);
    });

    testWidgets('对准只承诺天区：出现「已进入目标天区」，不出现「精准」',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: 126,
        targetEl: 47,
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.textContaining('已进入目标天区'), findsOneWidget);
      expect(find.textContaining('精准'), findsNothing);
      expect(find.textContaining('已锁定'), findsNothing);
    });

    testWidgets('对准且样本足够：天区提示里带上观测波动', (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: 126,
        targetEl: 47,
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123, 123, 123, 123, 123]);

      expect(find.text('已进入目标天区（±5°），罗盘波动 ±0°'), findsOneWidget);
      expect(find.textContaining('精准'), findsNothing);
    });
  });

  group('准星中心识别（指哪认哪）', () {
    testWidgets('命中命名星：显示中文名（西名）+ 距中心角距',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: null,
        targetEl: null,
        source: _FakeAssetSource(_assetsWith(
          stars: <SkyStar>[_starAt(az: 123, hip: 91262, vmag: 0.03)],
          names: const <int, StarNameEntry>{
            91262: StarNameEntry(zh: '织女一', en: 'Vega'),
          },
        )),
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.byKey(const Key('pointing-center-id')), findsOneWidget);
      expect(find.textContaining('织女一（Vega）'), findsOneWidget);
      expect(find.textContaining('距中心 0.0°'), findsOneWidget);
    });

    testWidgets('中心附近无亮星：明说「附近无亮星」，不拿远处星凑数',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      // 星在天的另一侧（方位 243°），准星附近确实没有星。
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: null,
        targetEl: null,
        source: _FakeAssetSource(_assetsWith(
          stars: <SkyStar>[_starAt(az: 243, hip: 1, vmag: 1.0)],
          names: const <int, StarNameEntry>{1: StarNameEntry(zh: '别处星')},
        )),
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.text('中心附近无亮星'), findsOneWidget);
      expect(find.textContaining('别处星'), findsNothing);
    });

    testWidgets('命中未收录名字的星：只报 HIP/星等，绝不编名',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: null,
        targetEl: null,
        source: _FakeAssetSource(_assetsWith(
          stars: <SkyStar>[
            _starAt(az: 123, hip: 1, vmag: 1.0),
            _starAt(az: 243, hip: 2, vmag: 2.0),
          ],
          // 有星名目录（hasStarNames=true），但中心这颗未收录。
          names: const <int, StarNameEntry>{
            2: StarNameEntry(zh: '别处星'),
          },
        )),
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.textContaining('HIP 1'), findsOneWidget);
      expect(find.textContaining('距中心 0.0°'), findsOneWidget);
      expect(find.textContaining('别处星'), findsNothing);
    });

    testWidgets('星名不可用：状态条与准星都明确写「不可用」',
        (WidgetTester tester) async {
      final StreamController<DeviceOrientation> controller =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(controller.close);
      await tester.pumpWidget(_page(
        stream: controller.stream,
        targetAz: null,
        targetEl: null,
        source: _FakeAssetSource(_assetsWith(
          stars: <SkyStar>[_starAt(az: 123, hip: 91262, vmag: 0.03)],
          namesError: '星名目录读取失败：测试注入',
        )),
      ));
      await tester.pump();
      await _feedHeadings(tester, controller, <double>[123]);

      expect(find.text('星名数据不可用，无法识别中心星'), findsOneWidget);
      expect(find.textContaining('星名不可用：星名目录读取失败'), findsOneWidget);
    });
  });
}
