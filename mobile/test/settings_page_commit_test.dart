// 设置页输入落盘回归（缺陷现场：模拟器实测，2026-11-22）。
//
// 在 rtl_tcp 主机填 10.0.2.2 后按 Android 返回键收起键盘，频谱页连接按钮
// 仍显示「设置 rtl_tcp 地址」——旧实现只在 onSubmitted / onEditingComplete
// 写回，而返回键收起键盘不触发提交。修复后 onChanged 即静默落盘；
// 端口越界/半截输入不落盘也不抛异常（等用户改完整）。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/pages/settings_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';

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

Widget _wrap(Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('zh'),
      home: home,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('rtl_tcp / ControlHub 主机与端口：输入即落盘，不依赖键盘提交',
      (tester) async {
    // 拉高视口让四个输入框一次全部构建（ListView 懒构建）。
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final _MemKv kv = _MemKv();
    final SettingsService settings =
        SettingsService(kv: kv, secure: _MemSecure());
    await settings.load();

    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(settings: settings, radio: RadioController()),
    )));
    await tester.pump();

    final Finder rtlHostField = find.widgetWithIcon(TextField, Icons.lan_outlined);
    final Finder chHostField = find.widgetWithIcon(TextField, Icons.dns_outlined);
    // 两个端口框共用 Icons.numbers：按树序前者是 rtl_tcp，后者是 ControlHub。
    final Finder portFields = find.widgetWithIcon(TextField, Icons.numbers);
    expect(rtlHostField, findsOneWidget);
    expect(chHostField, findsOneWidget);
    expect(portFields, findsNWidgets(2));

    // 主机：仅输入（不按「完成」），也应写回并 trim 后落盘。
    await tester.enterText(rtlHostField, '  10.0.2.2  ');
    await tester.pump();
    expect(settings.rtlHost, '10.0.2.2');
    expect(kv.getString('rtlHost'), '10.0.2.2');

    // 端口：合法值立即落盘。
    await tester.enterText(portFields.first, '1235');
    await tester.pump();
    expect(settings.rtlPort, 1235);
    expect(kv.getInt('rtlPort'), 1235);

    // 越界 / 半截输入：不落盘、不抛（setter 会抛 FormatException，门在此拦住）。
    await tester.enterText(portFields.first, '70000');
    await tester.pump();
    expect(settings.rtlPort, 1235);
    await tester.enterText(portFields.first, '');
    await tester.pump();
    expect(settings.rtlPort, 1235);
    expect(tester.takeException(), isNull);

    // ControlHub 同构：主机与端口同样即输即存。
    await tester.enterText(chHostField, '10.0.2.3');
    await tester.pump();
    expect(settings.controlHubHost, '10.0.2.3');
    await tester.enterText(portFields.last, '8081');
    await tester.pump();
    expect(settings.controlHubPort, 8081);
  });

  testWidgets('本站经纬度：只输入不按「完成」也落盘（失焦/返回键不丢）',
      (tester) async {
    tester.view.physicalSize = const Size(400, 3600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final _MemKv kv = _MemKv();
    final SettingsService settings =
        SettingsService(kv: kv, secure: _MemSecure());
    await settings.load();

    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(settings: settings, radio: RadioController()),
    )));
    await tester.pump();

    final Finder lat = find.ancestor(
      of: find.text('纬度（度，-90 ~ 90）'),
      matching: find.byType(TextField),
    );
    final Finder lon = find.ancestor(
      of: find.text('经度（度，-180 ~ 180）'),
      matching: find.byType(TextField),
    );
    expect(lat, findsOneWidget);
    expect(lon, findsOneWidget);

    // 模拟器实测现场：输入后按返回键收起键盘，未触发 onSubmitted。
    await tester.enterText(lat, '40');
    await tester.pump();
    expect(settings.stationLat, 40);
    await tester.enterText(lon, '-100');
    await tester.pump();
    expect(settings.stationLon, -100);

    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump();
    expect(settings.stationLat, 40);
    expect(settings.stationLon, -100);
  });
}
