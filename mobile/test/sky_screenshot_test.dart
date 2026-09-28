import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';

/// Offscreen 截图：pump SkyPage（fake TLE + 手工站坐标），导出
/// mobile/scratch/sky_polar.png（竖屏）与 sky_wide.png（宽屏）。
///
/// 为让画面里确实有可见卫星点，先用真实 SGP4 扫描一个仰角较高的过境时刻，
/// 通过时钟 seam 注入；状态栏仍显示真实墙钟。

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
  static const l1 =
      '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
  static const l2 =
      '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';

  @override
  Future<List<Tle>> fetch(TleGroup group) async => [Tle.fromLines(l1, l2)];

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2024);
}

const _station = Station(lat: 39.9, lon: -100.0);

/// 扫描未来 24h，找一个 ISS 仰角 > 30° 的 UTC 时刻。
/// TLE 历元为 2008-09-20（08264），须在历元附近评估 SGP4 才有效。
DateTime _findVisibleTime() {
  final tle = Tle.fromLines(_FakeTleClient.l1, _FakeTleClient.l2);
  final sat = Sgp4(tle);
  final base = DateTime.utc(2008, 9, 20, 0, 0);
  var best = base;
  var bestEl = -90.0;
  for (var m = 0; m < 24 * 4; m++) {
    final t = base.add(Duration(minutes: 15 * m));
    try {
      final a = azElAt(sat, t, _station);
      if (a.el > bestEl) {
        bestEl = a.el;
        best = t;
      }
      if (a.el > 40) return t;
    } on Sgp4Exception {
      continue;
    }
  }
  // ignore: avoid_print
  print('best el over window = ${bestEl.toStringAsFixed(1)}° at $best');
  return best;
}

Future<void> _capture(WidgetTester tester, String path) async {
  final boundary = tester
      .renderObject(find.byKey(const ValueKey('sky_capture'))) as RenderRepaintBoundary;
  // toImage 走引擎回调，必须逃出 fake-async 区域。
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 2.0);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File(path);
    await file.writeAsBytes(bytes!.buffer.asUint8List());
    // ignore: avoid_print
    print('wrote $path (${file.lengthSync()} bytes)');
  });
}

Widget _app(DateTime fixedTime) {
  return RepaintBoundary(
    key: const ValueKey('sky_capture'),
    child: MaterialApp(
      debugShowCheckedModeBanner: false,
      home: SkyPage(
        manualStation: _station,
        orientationService: _UnavailableOrientation(),
        tleClient: _FakeTleClient(),
        locationService: _DeniedLocation(),
        clock: () => fixedTime,
      ),
    ),
  );
}

void main() {
  final fixed = _findVisibleTime();
    // ignore: avoid_print
  print('using geometry time $fixed (UTC)');

  testWidgets('竖屏极坐标截图', (tester) async {
    tester.view.physicalSize = const Size(420, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_app(fixed));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(milliseconds: 500));

    await _capture(tester, 'scratch/sky_polar.png');
  });

  testWidgets('宽屏并排截图', (tester) async {
    tester.view.physicalSize = const Size(960, 540);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(_app(fixed));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(milliseconds: 500));

    await _capture(tester, 'scratch/sky_wide.png');
  });
}
