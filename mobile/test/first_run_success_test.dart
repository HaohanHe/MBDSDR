// P1「首次成功」端到端（手机端路径）：冷启动 → 看见真实星空。
//
// 断言目标（spec P1，手机端）：
//   1. 冷启动默认页是频谱，但「看见真实星空」只需 1 次点击（底栏「天空」）；
//   2. 全程零文本输入，不需要先填任何技术参数；
//   3. 用户交互 ≤3 步（点「天空」= 1 步，定位/TLE 拉取是系统等待，不算交互）；
//   4. 定位被拒时诚实空态：说真实原因，不画星点、不画假星空；
//   5. 定位成功后本站坐标可交代来源（状态条标「系统定位」）。
//
// 判「已画星」的口径不是页面标题，而是 SkyRadar 的 visible 列表里有真实
// TLE + SGP4 算出的在视卫星（el > 0），以及过境表标注「预测·SGP4」。
//
// 诚实边界：桌面默认形态仍是 IDE（需用户拍板），所以 P1 目前只证手机端路径。

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/home_shell.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';
import 'package:mbdsdr_mobile/widgets/compass_dial.dart';
import 'package:provider/provider.dart';

class _MemKv implements KvStore {
  final Map<String, Object?> d = <String, Object?>{};
  @override
  String? getString(String k) => d[k] as String?;
  @override
  Future<void> setString(String k, String v) async => d[k] = v;
  @override
  double? getDouble(String k) => d[k] as double?;
  @override
  Future<void> setDouble(String k, double v) async => d[k] = v;
  @override
  int? getInt(String k) => d[k] as int?;
  @override
  Future<void> setInt(String k, int v) async => d[k] = v;
  @override
  bool? getBool(String k) => d[k] as bool?;
  @override
  Future<void> setBool(String k, bool v) async => d[k] = v;
  @override
  Future<void> remove(String k) async => d.remove(k);
}

class _MemSecure implements SecureStore {
  @override
  Future<String?> get(String k) async => null;
  @override
  Future<void> set(String k, String v) async {}
  @override
  Future<void> delete(String k) async {}
}

/// 固定站址的系统定位（中性示例坐标 0,0）。
class _SystemLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.available;
  @override
  Station? get station => _neutralStation;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async => LocationStatus.available;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

/// 权限被永久拒绝：无站址，只能引导去系统设置。
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

/// 真实 TLE（ISS，历元 2026-10-27），只替换网络拉取，SGP4 传播仍走真实现。
class _FakeTleClient extends TleClient {
  static const String l1 =
      '1 25544U 98067A   26300.50000000  .00016717  00000-0  10270-3 0  0864';
  static const String l2 =
      '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';

  @override
  Future<List<Tle>> fetch(TleGroup group) async => [Tle.fromLines(l1, l2)];

  @override
  DateTime? lastUpdated(TleGroup group) => DateTime.utc(2026, 10, 27);
}

/// 中性示例站址（公开可用的测试坐标，不是任何真实位置）。
const Station _neutralStation = Station(lat: 0.0, lon: 0.0);

/// 在 TLE 历元附近找一个 ISS 仰角 > 20° 的 UTC 时刻，保证天空页确有
/// 可见卫星（真实 SGP4 传播），状态栏时钟仍是真实墙钟。
DateTime _findVisibleTime() {
  final Tle tle = Tle.fromLines(_FakeTleClient.l1, _FakeTleClient.l2);
  final Sgp4 sat = Sgp4(tle);
  final DateTime base = DateTime.utc(2026, 10, 27, 12);
  for (var m = 0; m < 24 * 4; m++) {
    final DateTime t = base.add(Duration(minutes: 15 * m));
    try {
      if (azElAt(sat, t, _neutralStation).el > 20) return t;
    } on Sgp4Exception {
      continue;
    }
  }
  return base;
}

Widget _wrap(
  SettingsService settings,
  RadioController radio, {
  required LocationService location,
  required TleClient tleClient,
  required DateTime Function() clock,
}) =>
    MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('zh'),
      home: MultiProvider(
        providers: <ChangeNotifierProvider<ChangeNotifier>>[
          ChangeNotifierProvider<SettingsService>.value(value: settings),
          ChangeNotifierProvider<RadioController>.value(value: radio),
        ],
        child: HomeShell(
          skyPageBuilder: (Station? manualStation, RadioApi? radio) => SkyPage(
            manualStation: manualStation,
            radio: radio,
            locationService: location,
            tleClient: tleClient,
            orientationService: _UnavailableOrientation(),
            clock: clock,
          ),
        ),
      ),
    );

/// 冷启动 → 等定位/TLE 落地（系统等待，不是用户交互）。
Future<void> _pumpColdStart(WidgetTester tester, Widget app) async {
  await tester.pumpWidget(app);
  await tester.pump(); // post-frame 触发天空页 refresh（定位 + TLE）
  await tester.pump(const Duration(milliseconds: 100));
  await tester.pump(const Duration(milliseconds: 100));
}

Future<SettingsService> _newSettings() async {
  final SettingsService settings =
      SettingsService(kv: _MemKv(), secure: _MemSecure());
  await settings.load(); // 冷启动：无手填站址、首跑引导未看过
  return settings;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final DateTime visibleTime = _findVisibleTime();

  testWidgets('冷启动默认频谱；1 次点击「天空」即见真实星空', (tester) async {
    tester.view.physicalSize = const Size(420, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = await _newSettings();
    await _pumpColdStart(
      tester,
      _wrap(
        settings,
        RadioController(),
        location: _SystemLocation(),
        tleClient: _FakeTleClient(),
        clock: () => visibleTime,
      ),
    );

    // 冷启动默认页 = 频谱（外壳 IndexedStack 第 0 项），天空内容尚不可见。
    // 频谱页里还有一个 DropdownButton 内部的 IndexedStack，这里取
    // SpectrumPage 最近的 IndexedStack 祖先 = 外壳的页面栈，避免误取。
    expect(find.byType(SpectrumPage), findsOneWidget);
    final IndexedStack shellStack = tester.widget<IndexedStack>(
      find
          .ancestor(
            of: find.byType(SpectrumPage),
            matching: find.byType(IndexedStack),
          )
          .first,
    );
    expect(shellStack.index, 0);
    expect(shellStack.children.length, 6);
    expect(
      tester.widget<BottomNavigationBar>(find.byType(BottomNavigationBar))
          .currentIndex,
      0,
    );
    expect(find.text('天空过境'), findsNothing);

    // P1：点一次底栏「天空」。
    await tester.tap(find.text('天空'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('天空过境'), findsOneWidget);
    // 真星空判据：极坐标图里的可见卫星来自真实 TLE + SGP4（不是标题/占位）。
    final SkyRadar radar = tester.widget<SkyRadar>(find.byType(SkyRadar));
    expect(
      radar.visible.where((SatVisibility v) => v.el > 0).isNotEmpty,
      isTrue,
      reason: '真实 TLE+SGP4 应算出在视卫星；空列表说明没画真星空',
    );
    // 极坐标画布确实在页面上（不是只有数据对象）。
    expect(
      find.descendant(
        of: find.byType(SkyRadar),
        matching: find.byType(CustomPaint),
      ),
      findsOneWidget,
    );
    // 过境表同为 SGP4 预测，且带来源标注。
    expect(find.textContaining('预测·SGP4'), findsWidgets);
  });

  testWidgets('全程零文本输入：不要求先填任何技术参数', (tester) async {
    tester.view.physicalSize = const Size(420, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = await _newSettings();
    await _pumpColdStart(
      tester,
      _wrap(
        settings,
        RadioController(),
        location: _SystemLocation(),
        tleClient: _FakeTleClient(),
        clock: () => visibleTime,
      ),
    );

    // 进入天空前：没有任何输入框获得焦点、键盘未弹。
    expect(
      find.byWidgetPredicate(
        (Widget w) => w is EditableText && w.focusNode.hasFocus,
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(tester.testTextInput.isVisible, isFalse);
    final int inputCallsBefore = tester.testTextInput.log.length;

    await tester.tap(find.text('天空'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('天空过境'), findsOneWidget);
    // 进入天空后仍无任何文本输入事件（不填频率/坐标/技术参数）。
    expect(
      tester.testTextInput.log.length,
      inputCallsBefore,
      reason: '从冷启动到看见真星空不得出现任何文本输入事件',
    );
    expect(tester.testTextInput.isVisible, isFalse);
    expect(
      find.byWidgetPredicate(
        (Widget w) => w is EditableText && w.focusNode.hasFocus,
        skipOffstage: false,
      ),
      findsNothing,
    );
  });

  testWidgets('交互次数 ≤3：点「天空」= 1 步，其余为定位等待', (tester) async {
    tester.view.physicalSize = const Size(420, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    // 逐步记账：只有显式点按/输入才算用户交互；定位、TLE 拉取是系统等待。
    final List<String> userSteps = <String>[];

    final SettingsService settings = await _newSettings();
    await _pumpColdStart(
      tester,
      _wrap(
        settings,
        RadioController(),
        location: _SystemLocation(),
        tleClient: _FakeTleClient(),
        clock: () => visibleTime,
      ),
    );

    // 点星空 = 1 步；定位与 TLE 拉取在等待中完成，不额外消耗交互。
    userSteps.add('点底栏「天空」');
    await tester.tap(find.text('天空'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('天空过境'), findsOneWidget);
    // 步数内确实看到了真实在视卫星，而不只是切到了标题。
    final SkyRadar radar = tester.widget<SkyRadar>(find.byType(SkyRadar));
    expect(radar.visible.where((SatVisibility v) => v.el > 0), isNotEmpty);
    expect(
      userSteps.length,
      lessThanOrEqualTo(3),
      reason: 'P1 要求 ≤3 步看见真实星空，实际 ${userSteps.length} 步',
    );
    expect(userSteps, <String>['点底栏「天空」']);
  });

  testWidgets('定位被永久拒绝：真实原因空态，不画星点/假星空', (tester) async {
    tester.view.physicalSize = const Size(420, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = await _newSettings();
    await _pumpColdStart(
      tester,
      _wrap(
        settings,
        RadioController(),
        location: _DeniedLocation(),
        tleClient: _FakeTleClient(),
        clock: () => visibleTime,
      ),
    );

    await tester.tap(find.text('天空'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    // 诚实空态：说真实原因，并给出去系统设置的入口。
    expect(find.textContaining('定位权限被永久拒绝'), findsOneWidget);
    expect(find.text('开启定位'), findsOneWidget);
    // 不得出现极坐标星空（星点/假星空），也不得编造本站坐标。
    expect(find.byType(SkyRadar), findsNothing);
    expect(find.textContaining('本站'), findsNothing);
  });

  testWidgets('定位成功后站址来源可交代：状态条标「系统定位」', (tester) async {
    tester.view.physicalSize = const Size(420, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = await _newSettings();
    await _pumpColdStart(
      tester,
      _wrap(
        settings,
        RadioController(),
        location: _SystemLocation(),
        tleClient: _FakeTleClient(),
        clock: () => visibleTime,
      ),
    );

    await tester.tap(find.text('天空'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('本站 0.0, 0.0（系统定位）'), findsOneWidget);
  });
}
