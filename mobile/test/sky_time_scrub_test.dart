import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';

// 国际空间站 TLE（与 passes_test / sgp4_test 同一颗）。
const _issL1 =
    '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
const _issL2 =
    '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
const Station _station = Station(lat: 40.0, lon: -100.0);

class _FakeTle extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async => [Tle.fromLines(_issL1, _issL2)];

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2024);
}

class _EmptyTle extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async => const [];
  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2024);
}

class _NoopLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.available;
  @override
  Station? get station => _station;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async => LocationStatus.available;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

class _NoopOrientation implements OrientationService {
  @override
  Stream<DeviceOrientation> get stream =>
      Stream.value(DeviceOrientation.unavailable);
  @override
  void dispose() {}
}

SkyController _makeController(DateTime fixedNow, {TleClient? tle}) {
  final c = SkyController(
    tleClient: tle ?? _FakeTle(),
    locationService: _NoopLocation(),
    orientationService: _NoopOrientation(),
    manualStation: _station,
    clock: () => fixedNow,
  );
  c.start();
  return c;
}

void main() {
  final iss = Tle.fromLines(_issL1, _issL2);
  final fixedNow = iss.epoch.add(const Duration(hours: 2)).toUtc();
  // 等待节流窗口（trailing timer）触发。
  final settle =
      SkyController.previewThrottle + const Duration(milliseconds: 40);

  group('时间预览滑条', () {
    test('默认实时：geometryTime == now，isPreview 为假', () async {
      final c = _makeController(fixedNow);
      await c.refresh();
      expect(c.isPreview, isFalse);
      expect(c.previewOffset, Duration.zero);
      expect(c.geometryTime, fixedNow);
      expect(c.hasTle, isTrue);
    });

    test('拖拽到预览时刻：卫星位置 == 独立 SGP4 传播结果', () async {
      final c = _makeController(fixedNow);
      await c.refresh();

      c.seekPreview(const Duration(minutes: -10));
      // 节流窗口内尚未重算。
      expect(c.isPreview, isFalse);
      await Future<void>.delayed(settle);

      expect(c.isPreview, isTrue);
      final previewTime =
          fixedNow.subtract(const Duration(minutes: 10));
      expect(c.geometryTime, previewTime);

      // 独立传播：不走 controller 的 visibleAt，直接 Sgp4 + azElAt。
      final expected = azElAt(Sgp4(iss), previewTime, _station);
      final v = c.visible.singleWhere((v) => v.name == iss.name);
      expect(v.az, moreOrLessEquals(expected.az, epsilon: 1e-6));
      expect(v.el, moreOrLessEquals(expected.el, epsilon: 1e-6));
      expect(v.range, moreOrLessEquals(expected.range, epsilon: 1e-6));
    });

    test('节流：连续拖拽合并，窗口后只取最新偏移', () async {
      final c = _makeController(fixedNow);
      await c.refresh();

      c.seekPreview(const Duration(minutes: -5));
      c.seekPreview(const Duration(minutes: -10));
      c.seekPreview(const Duration(minutes: -20));
      // 立即：timer 未触发，仍停留在实时。
      expect(c.isPreview, isFalse);
      expect(c.geometryTime, fixedNow);

      await Future<void>.delayed(settle);
      expect(c.isPreview, isTrue);
      // 应用的是最后一次 -20，而非中间的 -5/-10。
      expect(
          c.geometryTime, fixedNow.subtract(const Duration(minutes: 20)));
    });

    test('松手：endPreview 回到实时并按 now 重算', () async {
      final c = _makeController(fixedNow);
      await c.refresh();

      c.seekPreview(const Duration(minutes: -10));
      await Future<void>.delayed(settle);
      expect(c.isPreview, isTrue);

      c.endPreview();
      expect(c.isPreview, isFalse);
      expect(c.previewOffset, Duration.zero);
      expect(c.geometryTime, fixedNow);

      final live = azElAt(Sgp4(iss), fixedNow, _station);
      final v = c.visible.singleWhere((v) => v.name == iss.name);
      expect(v.az, moreOrLessEquals(live.az, epsilon: 1e-6));
      expect(v.el, moreOrLessEquals(live.el, epsilon: 1e-6));
    });

    test('无 TLE：seek/end 不崩溃，hasTle 为假', () async {
      final c = _makeController(fixedNow, tle: _EmptyTle());
      await c.refresh();
      expect(c.hasTle, isFalse);
      expect(c.visible, isEmpty);

      c.seekPreview(const Duration(minutes: -10));
      await Future<void>.delayed(settle);
      expect(c.visible, isEmpty);
      c.endPreview();
    });

    test('预览偏移被夹到 ±30 分钟窗口', () async {
      final c = _makeController(fixedNow);
      await c.refresh();
      c.seekPreview(const Duration(minutes: 60)); // 超出 +30
      await Future<void>.delayed(settle);
      expect(c.previewOffset, const Duration(minutes: 30));
      c.endPreview();
    });
  });

  testWidgets('手填站 + fake TLE：滑条渲染实时徽标与 Slider', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: SkyPage(
        manualStation: _station,
        orientationService: _NoopOrientation(),
        tleClient: _FakeTle(),
        locationService: _NoopLocation(),
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.byType(Slider), findsOneWidget);
    expect(find.text('实时'), findsOneWidget);
  });
}
