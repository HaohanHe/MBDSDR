// SPDX-License-Identifier: MIT
// UI 国际化（官方 gen-l10n）机制测试。
//
// 钉死：
//   * 三语 arb（zh 源 / en / ja）键集合完全一致，翻译不许有缺口；
//   * 三语抽样键值对得上（delegate.load 直查）；
//   * en locale 下 ChatPage 诚实空态渲染英文文案，且中文文案不出现；
//   * en locale 下外壳 Tab / 首跑引导走英文；
//   * 设置页语言选择 → SettingsService 持久化 → MaterialApp locale 跟随重建。
// 既有测试的中文断言由源语言 zh 保证（见各测试壳显式 locale: zh）。
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/home_shell.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/pages/chat_page.dart';
import 'package:mbdsdr_mobile/pages/settings_page.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';
import 'package:provider/provider.dart';

/// 测试用内存 KV 存储。
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

/// 测试用内存安全存储。
class _MemSecure implements SecureStore {
  @override
  Future<String?> get(String k) async => null;

  @override
  Future<void> set(String k, String v) async {}

  @override
  Future<void> delete(String k) async {}
}

/// 本地化测试壳：delegates + 显式 locale（与 main.dart 同一套 gen-l10n 接线）。
Widget _wrapLocale(Locale locale, Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: locale,
      home: home,
    );

/// 读取 arb 的非元数据键集合（@ 开头为元数据）。
Set<String> _arbKeys(String locale) {
  final File f = File('lib/l10n/app_$locale.arb');
  expect(f.existsSync(), isTrue, reason: '缺少 arb 档 app_$locale.arb');
  final Map<String, Object?> doc =
      jsonDecode(f.readAsStringSync()) as Map<String, Object?>;
  return doc.keys.where((String k) => !k.startsWith('@')).toSet();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('gen-l10n 三语覆盖钉扎', () {
    test('zh/en/ja 键集合完全一致，翻译不许有缺口', () {
      final Set<String> zh = _arbKeys('zh');
      expect(zh, isNotEmpty);
      for (final String locale in kSupportedUiLocales) {
        expect(
          _arbKeys(locale),
          zh,
          reason: '$locale 的键集合必须与源语言 zh 完全一致',
        );
      }
    });

    test('三语抽样键值对得上（同一键名 → 各语言真值）', () async {
      final AppLocalizations zh =
          await AppLocalizations.delegate.load(const Locale('zh'));
      final AppLocalizations en =
          await AppLocalizations.delegate.load(const Locale('en'));
      final AppLocalizations ja =
          await AppLocalizations.delegate.load(const Locale('ja'));

      expect(zh.navSettings, '设置');
      expect(en.navSettings, 'Settings');
      expect(ja.navSettings, '設定');

      expect(zh.chatUnconfiguredTitle, '尚未配置 AI API key');
      expect(en.chatUnconfiguredTitle, 'AI API key not configured yet');
      expect(ja.chatUnconfiguredTitle, 'AI API key が未設定です');

      // 参数化消息：占位符在三语里都真正被替换。
      expect(zh.chatFoldMarker(1), '〔已折叠 1 轮早期对话为占位；原文仍可在上下回看，未删除〕');
      expect(en.chatFoldMarker(1), contains('1'));
      expect(ja.chatFoldMarker(1), contains('1'));
    });
  });

  group('en locale 渲染', () {
    testWidgets('ChatPage 未配置空态渲染英文文案，中文文案不出现', (tester) async {
      await tester.pumpWidget(_wrapLocale(
        const Locale('en'),
        ChatPage(
          isConfigured: false,
          clientFactory: () => AiClient(apiKey: 'x'),
          onOpenSettings: () {},
        ),
      ));
      await tester.pump();

      expect(find.text('AI API key not configured yet'), findsOneWidget);
      expect(find.text('Open settings'), findsOneWidget);
      expect(find.text('尚未配置 AI API key'), findsNothing);
    });

    testWidgets('外壳 Tab 与首跑引导走英文', (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final SettingsService settings = SettingsService(
        kv: _MemKv(),
        secure: _MemSecure(),
      );
      await settings.load();
      final RadioController radio = RadioController();

      await tester.pumpWidget(_wrapLocale(
        const Locale('en'),
        MultiProvider(
          providers: <ChangeNotifierProvider<ChangeNotifier>>[
            ChangeNotifierProvider<SettingsService>.value(value: settings),
            ChangeNotifierProvider<RadioController>.value(value: radio),
          ],
          child: const HomeShell(),
        ),
      ));
      await tester.pump();

      expect(find.text('First time? Three steps to get going'), findsOneWidget);
      expect(find.text('频谱'), findsNothing);

      final BottomNavigationBar bar = tester.widget<BottomNavigationBar>(
        find.byType(BottomNavigationBar),
      );
      expect(
        bar.items.any((BottomNavigationBarItem i) => i.label == 'Spectrum'),
        isTrue,
      );
      expect(
        bar.items.any((BottomNavigationBarItem i) => i.label == 'Settings'),
        isTrue,
      );
    });
  });

  testWidgets('设置页语言选择：切换即持久化，locale 跟随重建', (tester) async {
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final _MemKv kv = _MemKv();
    final SettingsService settings = SettingsService(
      kv: kv,
      secure: _MemSecure(),
    );
    await settings.load();
    final RadioController radio = RadioController();

    // 与 main.dart 同构：locale 取自 SettingsService，变更即重建 MaterialApp。
    await tester.pumpWidget(ListenableBuilder(
      listenable: settings,
      builder: (BuildContext context, _) => MaterialApp(
        debugShowCheckedModeBanner: false,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        locale: Locale(settings.uiLocaleCode),
        home: Scaffold(body: SettingsPage(settings: settings, radio: radio)),
      ),
    ));
    await tester.pump();

    // 语言分区在页面下部：滚到可见（ListView 懒构建）。
    await tester.scrollUntilVisible(
      find.text('语言'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('简体中文'), findsOneWidget);
    expect(find.text('English'), findsOneWidget);
    expect(find.text('日本語'), findsOneWidget);

    await tester.tap(find.text('English'));
    await tester.pump();

    // 落盘 + 设置生效 + locale 跟随重建（分区标题已换英文）。
    expect(settings.uiLocaleCode, 'en');
    expect(kv.d['uiLocale'], 'en');
    expect(find.text('Language'), findsOneWidget);
    expect(find.text('语言'), findsNothing);
  });
}
