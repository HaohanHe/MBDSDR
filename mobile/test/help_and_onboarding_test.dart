// UI 轴验收：应用内帮助页（sdroxide F1 手册模式）+ 首跑引导横幅
// （OnAir 首跑向导轻量版）。
//
// 钉死：
//   * 帮助页五个分块齐全，且从外壳 AppBar 帮助图标可达；
//   * 首跑引导：未看过时挂出、可消除、消除写持久化标记、再看不再现；
//   * 「去设置」把外壳切到设置页；
//   * 看过引导后 AppBar 帮助入口仍在（帮助随时可查）。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/home_shell.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/pages/help_page.dart';
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

/// 测试壳固定源语言 zh（gen-l10n delegates + 显式 locale），
/// 既有引导文案断言与源语言真值一致。
Widget _wrap(SettingsService settings, RadioController radio) => MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('zh'),
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

  Future<SettingsService> settingsWith({bool guideSeen = false}) async {
    final kv = _MemKv();
    if (guideSeen) await kv.setBool('firstRunGuideSeen', true);
    final s = SettingsService(kv: kv, secure: _MemSecure());
    await s.load();
    return s;
  }

  // ---------------------------------------------------------------- 帮助页本体

  testWidgets('帮助页五分部齐全且诚实标注边界', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: HelpPage()));
    await tester.pump();

    for (final section in <String>[
      '快速开始',
      '每个页面做什么',
      '解调模式怎么选',
      '常见问题',
      '术语速查',
    ]) {
      // ListView 懒加载：逐段滚到可见再断言。
      await tester.scrollUntilVisible(
        find.text(section),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text(section), findsOneWidget, reason: '缺少分块 $section');
    }
    // 关键内容抽查：FAQ 与术语是模型/用户最高频疑问，必须在页内自足回答。
    expect(find.textContaining('连不上电台？'), findsOneWidget);
    expect(find.textContaining('多普勒'), findsWidgets);
  });

  // ---------------------------------------------------------------- 入口可达性

  testWidgets('AppBar 帮助图标可达帮助页', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = await settingsWith(guideSeen: true);
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    expect(find.byIcon(Icons.help_outline), findsOneWidget);
    await tester.tap(find.byIcon(Icons.help_outline));
    await tester.pumpAndSettle();

    expect(find.byType(HelpPage), findsOneWidget);
    // 返回后外壳仍在。
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(HomeShell), findsOneWidget);
  });

  // ---------------------------------------------------------------- 首跑引导

  testWidgets('未看过引导：横幅挂出，「知道了」后永不再现', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = await settingsWith(guideSeen: false);
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    expect(find.text('第一次使用？三步上路'), findsOneWidget);

    await tester.tap(find.text('知道了'));
    await tester.pump();

    // 横幅消失 + 持久化标记落盘。
    expect(find.text('第一次使用？三步上路'), findsNothing);
    expect(settings.firstRunGuideSeen, isTrue);
  });

  testWidgets('看过引导（持久化）：不再挂横幅', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = await settingsWith(guideSeen: true);
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    expect(find.text('第一次使用？三步上路'), findsNothing);
    // 帮助入口不受引导状态影响，始终可达。
    expect(find.byIcon(Icons.help_outline), findsOneWidget);
  });

  testWidgets('引导「去设置」切到设置页', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = await settingsWith(guideSeen: false);
    final radio = RadioController();

    await tester.pumpWidget(_wrap(settings, radio));
    await tester.pump();

    await tester.tap(find.text('去设置'));
    await tester.pump();

    // 引导仍在（去设置不清标记），但外壳已切到设置页（底栏设置项高亮）。
    final bar = tester.widget<BottomNavigationBar>(
      find.byType(BottomNavigationBar),
    );
    expect(bar.items[bar.currentIndex].label, '设置');
  });
}
