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
    const l1 =
        '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
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
  });
}
