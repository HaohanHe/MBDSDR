// 小屏（360×640 dp，720×1280 物理 / dpr 2）天空页布局守卫。
//
// 2026-11-22 模拟器实机走查发现两处真问题：
//   1) 竖屏 4/7 分栏装不下固定卡片，Flutter 报 BOTTOM OVERFLOWED BY 71 PIXELS；
//   2) 顶部状态条把「本站 纬度, 经度」挤成「本站 39…」，读数被裁掉。
// 本文件钉住：竖屏整页可滚、无溢出异常、站址文字完整不省略。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';
import 'package:mbdsdr_mobile/widgets/compass_dial.dart';

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

class _NoOrientation implements OrientationService {
  @override
  Stream<DeviceOrientation> get stream =>
      Stream.value(DeviceOrientation.unavailable);
  @override
  void dispose() {}
}

/// 只回一颗 ISS TLE；GNSS 组回空（导航星走诚实空态，不影响布局）。
class _FakeTleClient extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async {
    if (group == TleGroup.gnss) return const <Tle>[];
    const l1 =
        '1 25544U 98067A   26300.50000000  .00016717  00000-0  10270-3 0  0864';
    const l2 =
        '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
    return <Tle>[Tle.fromLines(l1, l2)];
  }

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2026, 10, 27);
}

void main() {
  testWidgets('360×640 dp 竖屏：天空页不溢出，整页可滚，站址不省略',
      (WidgetTester tester) async {
    tester.view.physicalSize = const Size(720, 1280);
    tester.view.devicePixelRatio = 2.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(MaterialApp(
      home: SkyPage(
        tleClient: _FakeTleClient(),
        locationService: _AvailableLocation(),
        orientationService: _NoOrientation(),
        clock: () => DateTime.utc(2026, 10, 27, 12),
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    // 溢出会在布局期抛 FlutterError；有则在此取到。
    expect(tester.takeException(), isNull);

    // 竖屏走可滚容器（小屏不再硬塞进 4/7 分栏）。
    expect(find.byKey(const Key('sky-portrait-scroll')), findsOneWidget);

    // 站址完整可读：文字不设 ellipsis，坐标全文在树里。
    final Text station = tester.widget<Text>(find.textContaining('本站').first);
    expect(station.overflow, isNot(TextOverflow.ellipsis));
    expect(find.text('本站 31.25, 121.5（系统定位）'), findsOneWidget);
  });

  group('placeLabelAvoiding（导航星「预:」标签避让）', () {
    test('同一点第二个标签换方向而不是叠字', () {
      const size = Size(300, 300);
      const point = Offset(150, 150);
      final occupied = <Rect>[];
      final first = placeLabelAvoiding(
        point: point,
        labelWidth: 80,
        labelHeight: 14,
        canvasSize: size,
        occupied: occupied,
      );
      expect(first, isNotNull);
      occupied.add(first!.rect);
      final second = placeLabelAvoiding(
        point: point,
        labelWidth: 80,
        labelHeight: 14,
        canvasSize: size,
        occupied: occupied,
      );
      expect(second, isNotNull);
      expect(second!.rect.overlaps(first.rect), isFalse);
    });

    test('同一点 8 个方向占满后返回 null（宁缺不叠）', () {
      const size = Size(300, 300);
      const point = Offset(150, 150);
      final occupied = <Rect>[];
      var placed = 0;
      while (placed < 10) {
        final p = placeLabelAvoiding(
          point: point,
          labelWidth: 80,
          labelHeight: 14,
          canvasSize: size,
          occupied: occupied,
        );
        if (p == null) break;
        expect(occupied.any((r) => r.overlaps(p.rect)), isFalse);
        occupied.add(p.rect);
        placed += 1;
      }
      expect(placed, greaterThanOrEqualTo(2));
      expect(placed, lessThanOrEqualTo(8));
    });

    test('相邻点仍能各自找到位置（不是一刀切全禁）', () {
      const size = Size(300, 300);
      final occupied = <Rect>[];
      final a = placeLabelAvoiding(
        point: const Offset(150, 150),
        labelWidth: 80,
        labelHeight: 14,
        canvasSize: size,
        occupied: occupied,
      );
      occupied.add(a!.rect);
      final b = placeLabelAvoiding(
        point: const Offset(220, 150),
        labelWidth: 80,
        labelHeight: 14,
        canvasSize: size,
        occupied: occupied,
      );
      expect(b, isNotNull);
      expect(b!.rect.overlaps(a.rect), isFalse);
    });
  });
}
