// 响应式外壳：窄屏底部导航收敛为 5 项（Material 3 建议 ≤5），
// 「活动记录」收进 AppBar 历史入口；宽屏 NavigationRail 保留 6 项。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/home_shell.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';
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

Widget _wrap(SettingsService settings, RadioController radio) => MaterialApp(
      debugShowCheckedModeBanner: false,
      home: MultiProvider(
        providers: <ChangeNotifierProvider<ChangeNotifier>>[
          ChangeNotifierProvider<SettingsService>.value(value: settings),
          ChangeNotifierProvider<RadioController>.value(value: radio),
        ],
        child: const HomeShell(),
      ),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('窄屏(360)底栏 5 项，不含「活动」，AppBar 有历史入口', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    // 窄屏走 BottomNavigationBar。
    final bar = tester.widget<BottomNavigationBar>(
      find.byType(BottomNavigationBar),
    );
    expect(bar.items.length, 5, reason: '窄屏底栏应收敛为 5 项');
    expect(bar.items.any((BottomNavigationBarItem i) => i.label == '活动'),
        isFalse,
        reason: '「活动记录」应从窄屏底栏移除，收进 AppBar');

    // AppBar 历史入口存在（窄屏进入活动记录的通道）。
    expect(find.byIcon(Icons.history), findsOneWidget);
  });

  testWidgets('宽屏(800)走 NavigationRail，保留 6 个目的地', (tester) async {
    tester.view.physicalSize = const Size(800, 600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    expect(find.byType(NavigationRail), findsOneWidget);
    final rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    expect(rail.destinations.length, 6,
        reason: '宽屏 Rail 不拥挤，保留全部 6 个目的地');
    // 宽屏不出现底栏。
    expect(find.byType(BottomNavigationBar), findsNothing);
  });
}
