import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/pointing_page.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/satellite_capture.dart';
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

/// 记录 resolve 次数：切 TLE 分组不应重复解析定位。
class _CountingLocation implements LocationService {
  int resolves = 0;

  @override
  LocationStatus get status => LocationStatus.available;
  @override
  Station? get station => const Station(lat: 31.25, lon: 121.5);
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async {
    resolves += 1;
    return LocationStatus.available;
  }

  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

/// 首次定位永不返回：页面必须说「正在获取位置…」，不得谎称未开启。
class _HangingLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.locating;
  @override
  Station? get station => null;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() => Completer<LocationStatus>().future;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

/// 服务/权限正常但这次拿不到位置：应给「重试定位」而不是系统设置。
class _UnavailableLocation implements LocationService {
  int resolves = 0;

  @override
  LocationStatus get status => LocationStatus.unavailable;
  @override
  Station? get station => null;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async {
    resolves += 1;
    return LocationStatus.unavailable;
  }

  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

/// 只有 stations 组给 TLE；其余组为空（模拟空分组/无星历）。
class _GroupedTleClient extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async {
    if (group != TleGroup.stations) return const <Tle>[];
    const l1 =
        '1 25544U 98067A   26300.50000000  .00016717  00000-0  10270-3 0  0864';
    const l2 =
        '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
    return <Tle>[Tle.fromLines(l1, l2)];
  }

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2026, 10, 27);
}

/// ISS（ZARYA，编目号 25544，命中真实下行目录 145.800 MHz）的 fake TLE；
/// 历元即测试时钟。（NOAA POES 星座已于 2025 年全部退役，目录已改收录 ISS。）
class _IssTleClient extends TleClient {
  static const String l1 =
      '1 25544U 98067A   26300.50000000  .00016717  00000-0  10270-3 0  0864';
  static const String l2 =
      '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';

  @override
  Future<List<Tle>> fetch(TleGroup group) async =>
      group == TleGroup.gnss ? const <Tle>[] : <Tle>[Tle.fromLines(l1, l2)];

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2026, 10, 27);
}

/// 记录调谐的最小射频 fake。
class _FakeRadio implements RadioApi {
  int? tunedHz;
  DemodMode? tunedMode;

  @override
  ConnectionStatus get status => ConnectionStatus.connected;

  @override
  int get freqHz => tunedHz ?? 144000000;

  @override
  Future<void> setFrequencyHz(int hz) async {
    tunedHz = hz;
  }

  @override
  void setMode(DemodMode m) {
    tunedMode = m;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError(invocation.memberName.toString());
}

/// 只提供列表/捕获的控制器 fake：用于构造「az/el 不可计算」的诚实分支。
class _FakeSkyController extends SkyController {
  _FakeSkyController(this._pass)
      : super(
          tleClient: _IssTleClient(),
          locationService: _DeniedLocation(),
          orientationService: _UnavailableOrientation(),
        );

  final Pass _pass;
  int captureCalls = 0;

  @override
  void start() {}

  @override
  Future<void> refresh() async {}

  @override
  List<Pass> get passes => <Pass>[_pass];

  @override
  Station? get station => const Station(lat: 40, lon: -100);

  @override
  LocationStatus get locStatus => LocationStatus.available;

  @override
  bool get hasTle => true;

  @override
  bool hasDownlink(Pass p) => true;

  @override
  Future<CaptureOutcome> capture(Pass p) async {
    captureCalls += 1;
    return const CaptureApplied(
      label: 'ISS 语音/SSTV',
      downlinkHz: 145.8e6,
      centerHz: 145800000,
      dopplerHz: 0,
      mode: DemodMode.nfm,
    );
  }

  @override
  CapturePointingTarget currentPointingTarget(Pass p) =>
      (target: null, reason: '缺少该卫星 TLE，无法计算当前方位/仰角');
}

const Station _station = Station(lat: 40, lon: -100);
final DateTime _captureTime = DateTime.utc(2026, 10, 27, 12);

Pass _fakePass() => Pass(
      name: 'NORAD 25544',
      catalogNumber: 25544,
      riseTime: _captureTime,
      riseAz: 20,
      setTime: _captureTime.add(const Duration(minutes: 10)),
      setAz: 300,
      maxEl: 40,
      maxTime: _captureTime.add(const Duration(minutes: 5)),
    );

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

  testWidgets('首次定位未返回：说「正在获取位置…」，不谎称服务未开启',
      (tester) async {
    await tester.pumpWidget(_wrap(SkyPage(
      locationService: _HangingLocation(),
      orientationService: _UnavailableOrientation(),
      tleClient: _FakeTleClient(),
    )));
    await tester.pump();
    expect(find.text('正在获取位置…'), findsOneWidget);
    expect(find.textContaining('定位服务未开启'), findsNothing);
  });

  testWidgets('服务开但拿不到位置：如实说明并给「重试定位」', (tester) async {
    final loc = _UnavailableLocation();
    await tester.pumpWidget(_wrap(SkyPage(
      locationService: loc,
      orientationService: _UnavailableOrientation(),
      tleClient: _FakeTleClient(),
    )));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    expect(find.textContaining('定位服务已开启，但这一次拿不到位置'), findsOneWidget);
    expect(find.text('重试定位'), findsOneWidget);
    expect(find.text('开启定位'), findsNothing);

    await tester.tap(find.text('重试定位'));
    await tester.pump();
    expect(loc.resolves, 2, reason: '重试要再次真正取点');
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

  testWidgets('捕获成功：按当前 az/el 打开指天视图，带上目标名', (tester) async {
    tester.view.physicalSize = const Size(800, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final radio = _FakeRadio();
    await tester.pumpWidget(_wrap(SkyPage(
      manualStation: _station,
      orientationService: _UnavailableOrientation(),
      tleClient: _IssTleClient(),
      locationService: _DeniedLocation(),
      radio: radio,
      clock: () => _captureTime,
    )));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    final Finder captureBtn = find.widgetWithText(TextButton, '捕获');
    expect(captureBtn, findsWidgets, reason: '有下行频率的过境必须可捕获');
    tester.widget<TextButton>(captureBtn.first).onPressed!.call();
    await tester.pump(); // 完成 capture future 并发起 push
    await tester.pump(); // 路由首帧
    await tester.pump(const Duration(milliseconds: 500)); // 转场

    expect(radio.tunedHz, isNotNull, reason: '真实下发 ISS 下行频率');
    expect((radio.tunedHz! - 145800000).abs(), lessThan(10e3),
        reason: '中心频率落在一次性多普勒偏置范围内');
    final PointingPage page =
        tester.widget<PointingPage>(find.byType(PointingPage));
    expect(page.targetLabel, 'NORAD 25544');

    final AzEl expected = azElAt(
      Sgp4(Tle.fromLines(_IssTleClient.l1, _IssTleClient.l2)),
      _captureTime,
      _station,
    );
    expect(page.targetAzDeg, closeTo(expected.az, 1e-6));
    expect(page.targetElDeg, closeTo(expected.el, 1e-6));
  });

  testWidgets('当前 az/el 算不出：不跳指天视图，SnackBar 说明真实原因', (tester) async {
    tester.view.physicalSize = const Size(800, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final controller = _FakeSkyController(_fakePass());
    await tester.pumpWidget(_wrap(SkyPage(
      controller: controller,
      orientationService: _UnavailableOrientation(),
    )));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    final Finder captureBtn = find.widgetWithText(TextButton, '捕获');
    expect(captureBtn, findsOneWidget);
    tester.widget<TextButton>(captureBtn).onPressed!.call();
    await tester.pump();
    await tester.pump();

    expect(controller.captureCalls, 1);
    expect(find.byType(PointingPage), findsNothing,
        reason: '算不出当前 az/el 就绝不打开指天视图');
    expect(find.textContaining('未打开指天视图'), findsOneWidget);
    expect(find.textContaining('缺少该卫星 TLE'), findsOneWidget);
  });

  test('切换 TLE 分组：不重复解析定位，空分组清空上一组过境', () async {
    final loc = _CountingLocation();
    final controller = SkyController(
      tleClient: _GroupedTleClient(),
      locationService: loc,
      orientationService: _UnavailableOrientation(),
    );
    addTearDown(controller.dispose);
    controller.start();

    await controller.refresh();
    expect(loc.resolves, 1);
    expect(controller.passes, isNotEmpty, reason: 'stations 组应有 ISS 过境');

    controller.setGroup(TleGroup.weather);
    // setGroup 内部 refresh() 异步执行；让微任务跑完。
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);

    expect(loc.resolves, 1, reason: '已有站址时切分组不得再次解析定位');
    expect(controller.passes, isEmpty,
        reason: '空分组必须清空上一组过境，不能继续挂 stations 的 ISS');
  });
}
