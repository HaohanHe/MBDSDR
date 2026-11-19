import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';

class _DeniedLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.deniedForever;
  @override
  Station? get station => null;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async => LocationStatus.deniedForever;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

class _AvailableLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.available;
  @override
  Station? get station => const Station(lat: 31.25, lon: 121.5);
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async => LocationStatus.available;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

class _UnavailableOrientation implements OrientationService {
  @override
  Stream<DeviceOrientation> get stream =>
      Stream.value(DeviceOrientation.unavailable);
  @override
  void dispose() {}
}

class _FakeTleClient extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async {
    // 历元取近当前日期，保证 fake TLE 能解出过境（2008 年历元会因衰减被跳过）。
    const l1 =
        '1 25544U 98067A   26300.50000000  .00016717  00000-0  10270-3 0  0864';
    const l2 =
        '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
    return [Tle.fromLines(l1, l2)];
  }

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2024);
}

Widget _wrap(Widget child) =>
    MaterialApp(home: child);

void main() {
  testWidgets('无定位权限：显示定位说明文案', (tester) async {
    await tester.pumpWidget(_wrap(SkyPage(
      locationService: _DeniedLocation(),
      orientationService: _UnavailableOrientation(),
      tleClient: _FakeTleClient(),
    )));
    await tester.pump();
    expect(find.textContaining('定位权限被永久拒绝'), findsOneWidget);
    expect(find.text('开启定位'), findsOneWidget);
  });

  testWidgets('手填站 + fake TLE：页面渲染过境列表', (tester) async {
    // 选中过境会展开引导卡；给足纵向空间避免矮视口下的既有布局溢出干扰。
    tester.view.physicalSize = const Size(800, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_wrap(SkyPage(
      manualStation: const Station(lat: 40, lon: -100),
      orientationService: _UnavailableOrientation(),
      tleClient: _FakeTleClient(),
      // locationService 不会被用到（manualStation 非空）。
      locationService: _DeniedLocation(),
    )));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.byType(SkyPage), findsOneWidget);
    // 分组 chip 出现。
    expect(find.text('stations'), findsOneWidget);
    // 读数来源（P2）：设备时钟与手填本站坐标都带后缀。
    expect(find.textContaining('设备时钟（未校准）'), findsOneWidget);
    expect(find.text('本站 40.0, -100.0（手填）'), findsOneWidget);
    // 过境表数值来自 SGP4 预测。
    expect(find.textContaining('预测·SGP4'), findsWidgets);

    // 选中一条过境后，引导卡目标 az/el 也标「预测」。
    // 过境行贴近视口底缘，直接触发 ListTile.onTap，避免命中被遮挡。
    final passTile = tester.widget<ListTile>(find.byType(ListTile).first);
    passTile.onTap!.call();
    await tester.pump();
    expect(find.textContaining('目标 az'), findsOneWidget);
    expect(
      find.textContaining(RegExp(r'目标 az \d+° / el -?\d+°（预测）')),
      findsOneWidget,
    );
  });

  testWidgets('系统定位：本站坐标标「系统定位」', (tester) async {
    await tester.pumpWidget(_wrap(SkyPage(
      locationService: _AvailableLocation(),
      orientationService: _UnavailableOrientation(),
      tleClient: _FakeTleClient(),
    )));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('本站 31.25, 121.5（系统定位）'), findsOneWidget);
  });
}
