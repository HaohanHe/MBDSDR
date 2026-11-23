// 设置页「多设备网关（配对）」UI 回归：全部走注入的 fake transport，离线。
//
// 覆盖：
//   * 未配对态如实显示「未配对」；
//   * 设备名/配对码本地校验（错码不发请求）；
//   * 配对成功 → token 落安全存储、状态显示「已配对 · 角色」；
//   * 角色切换后配对请求带新角色；
//   * 读取全景 → 摘要展示站址/太阳高度/缺项；
//   * 解除配对 → token 清空、回到未配对态。
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/pages/settings_page.dart';
import 'package:mbdsdr_mobile/services/device_gateway_client.dart';
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
  final Map<String, String> d = <String, String>{};

  @override
  Future<String?> get(String k) async => d[k];

  @override
  Future<void> set(String k, String v) async => d[k] = v;

  @override
  Future<void> delete(String k) async => d.remove(k);
}

/// 记录请求并按路径回冻结契约形状的假传输。
class _FakeGateway {
  final List<Uri> paths = <Uri>[];
  final List<Map<String, dynamic>> postBodies = <Map<String, dynamic>>[];

  Future<GatewayHttpResponse> call(Uri url, String method,
      Map<String, String> headers, String? body) async {
    paths.add(url);
    if (method == 'POST') {
      postBodies.add(jsonDecode(body!) as Map<String, dynamic>);
    }
    switch (url.path) {
      case '/pair':
        return const GatewayHttpResponse(
            200,
            '{"token":"tok-1","device":{"id":"dev-lens","name":"手机A","role":"lens"}}');
      case '/panorama':
        return const GatewayHttpResponse(200, jsonEncoder);
      case '/roster':
        return const GatewayHttpResponse(200, _rosterJson);
      case '/status':
        return const GatewayHttpResponse(200, _statusJson);
      default:
        return const GatewayHttpResponse(404, '{"reason":"not found"}');
    }
  }

  static const String _statusJson =
      '{"schema":"mbdsdr.desktop-status/1","authority":"desktop",'
      '"station":{"lat_deg":45.0,"lon_deg":120.0,"alt_m":10.0},'
      '"sdr":{"rtl_tcp":{"host":"127.0.0.1","port":1234},'
      '"lease_active":false,"dongle_probed":false},'
      '"missing":[]}';

  static const String jsonEncoder =
      '{"schema":"mbdsdr.sky-panorama/1","site":{"lat_deg":40.5,"lon_deg":-100.25},'
      '"sun":{"alt_deg":-18.4},"missing":["cloud_cover","kp_index"]}';

  static const String _rosterJson =
      '{"schema":"mbdsdr.device-roster/1","authority":"desktop",'
      '"devices":[{"id":"d1","role":"lens","name":"手机A"},'
      '{"id":"d2","role":"eyepiece","name":"手机B"}]}';
}

Widget _wrap(Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('zh'),
      home: home,
    );

Finder _fieldByLabel(String label) => find.ancestor(
      of: find.text(label),
      matching: find.byType(TextField),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('配对 → 全景 → 解除配对：全链路离线可走通', (tester) async {
    tester.view.physicalSize = const Size(420, 5200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final _MemKv kv = _MemKv();
    final _MemSecure secure = _MemSecure();
    final SettingsService settings =
        SettingsService(kv: kv, secure: secure);
    await settings.load();

    final _FakeGateway fake = _FakeGateway();
    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(
        settings: settings,
        radio: RadioController(),
        gatewayClientBuilder: (String host, int port) => DeviceGatewayClient(
          host: host,
          port: port,
          transport: fake.call,
        ),
      ),
    )));
    await tester.pump();

    // 未配对态。
    expect(find.text('未配对'), findsOneWidget);

    // 设备名 + 配对码，点「配对」。
    await tester.enterText(_fieldByLabel('设备名'), '手机A');
    await tester.pump();
    expect(settings.deviceGatewayName, '手机A');

    await tester.enterText(_fieldByLabel('配对码'), '123456');
    await tester.tap(find.widgetWithText(FilledButton, '配对'));
    await tester.pumpAndSettle();

    // 配对请求只打 /pair，载荷带设备名/角色/配对码。
    expect(fake.paths.map((Uri u) => u.path), <String>['/pair']);
    expect(fake.postBodies.single['code'], '123456');
    expect(fake.postBodies.single['device_name'], '手机A');
    expect(fake.postBodies.single['role'], 'handheld');

    // token 只进安全存储；UI 变成已配对 + 角色标签。
    expect(secure.d['deviceGatewayToken'], 'tok-1');
    expect(kv.d.containsKey('deviceGatewayToken'), isFalse);
    expect(settings.deviceGatewayDeviceId, 'dev-lens');
    expect(find.text('未配对'), findsNothing);
    expect(find.text('已配对 · 手持（只读）'), findsOneWidget);

    // 读取全景：摘要包含站址、太阳高度与缺项（不猜、不补）。
    await tester.tap(find.widgetWithText(OutlinedButton, '读取全景'));
    await tester.pumpAndSettle();
    expect(fake.paths.map((Uri u) => u.path),
        <String>['/pair', '/panorama']);
    expect(
        find.text('站址 40.50, -100.25 · 太阳高度 -18.4° · 缺项 cloud_cover, kp_index'),
        findsOneWidget);

    // 刷新名册：只显示角色/名字，不显示任何凭据。
    await tester.tap(find.widgetWithText(OutlinedButton, '刷新名册'));
    await tester.pumpAndSettle();
    expect(fake.paths.map((Uri u) => u.path),
        <String>['/pair', '/panorama', '/roster']);
    expect(
        find.text('名册（2 台）：镜头（视觉初指向） · 手机A，目镜（认星） · 手机B'),
        findsOneWidget);
    expect(find.textContaining('tok-1'), findsNothing);

    // 读取桌面状态：站址/SDR 配置/缺项如实展示（不缺就不编）。
    await tester.tap(find.widgetWithText(OutlinedButton, '读取桌面状态'));
    await tester.pumpAndSettle();
    expect(fake.paths.map((Uri u) => u.path),
        <String>['/pair', '/panorama', '/roster', '/status']);
    expect(
        find.text('站址 45.00, 120.00 · SDR rtl_tcp 127.0.0.1:1234 · 缺项 —'),
        findsOneWidget);

    // 解除配对：token 清空，回到未配对态。
    await tester.tap(find.widgetWithText(TextButton, '解除配对'));
    await tester.pumpAndSettle();
    expect(secure.d.containsKey('deviceGatewayToken'), isFalse);
    expect(settings.deviceGatewayToken, '');
    expect(find.text('未配对'), findsOneWidget);
    expect(find.text('已配对 · 手持（只读）'), findsNothing);
  });

  testWidgets('错码/缺名本地拦截，一个请求都不发', (tester) async {
    tester.view.physicalSize = const Size(420, 5200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings =
        SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    final _FakeGateway fake = _FakeGateway();

    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(
        settings: settings,
        radio: RadioController(),
        gatewayClientBuilder: (String host, int port) => DeviceGatewayClient(
          host: host,
          port: port,
          transport: fake.call,
        ),
      ),
    )));
    await tester.pump();

    // 缺设备名。
    await tester.enterText(_fieldByLabel('配对码'), '123456');
    await tester.tap(find.widgetWithText(FilledButton, '配对'));
    await tester.pumpAndSettle();
    expect(find.text('请先填写设备名'), findsOneWidget);
    expect(fake.paths, isEmpty);

    // 让第一条 SnackBar 走完（默认 4s），否则第二条排队不显示。
    await tester.pump(const Duration(seconds: 5));
    await tester.pumpAndSettle();

    // 设备名有了，配对码不足 6 位。
    await tester.enterText(_fieldByLabel('设备名'), '手机B');
    await tester.enterText(_fieldByLabel('配对码'), '12');
    await tester.tap(find.widgetWithText(FilledButton, '配对'));
    await tester.pumpAndSettle();
    expect(find.text('配对码应为 6 位数字'), findsOneWidget);
    expect(fake.paths, isEmpty);
    expect(settings.deviceGatewayPaired, isFalse);
  });

  testWidgets('桌面状态缺项如实展示；503 报错不编摘要', (tester) async {
    tester.view.physicalSize = const Size(420, 5200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings =
        SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    settings.deviceGatewayToken = 'tok-1';
    settings.deviceGatewayRole = 'handheld';

    final List<String> paths = <String>[];
    int status = 200;
    String body =
        '{"schema":"mbdsdr.desktop-status/1","station":null,"sdr":null,'
        '"missing":["station","sdr"]}';

    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(
        settings: settings,
        radio: RadioController(),
        gatewayClientBuilder: (String host, int port) => DeviceGatewayClient(
          host: host,
          port: port,
          transport: (Uri url, String method, Map<String, String> headers,
              String? payload) async {
            paths.add(url.path);
            return GatewayHttpResponse(status, body);
          },
        ),
      ),
    )));
    await tester.pump();

    await tester.tap(find.widgetWithText(OutlinedButton, '读取桌面状态'));
    await tester.pumpAndSettle();
    expect(paths, <String>['/status']);
    expect(find.text('站址 — · SDR — · 缺项 station, sdr'), findsOneWidget);

    // 权威端没接状态提供者：如实报原因，不把旧摘要当新结果。
    status = 503;
    body = '{"error":"status_unavailable","reason":"权威端尚未接入状态提供者"}';
    await tester.tap(find.widgetWithText(OutlinedButton, '读取桌面状态'));
    await tester.pumpAndSettle();
    expect(find.text('读取桌面状态失败：权威端尚未接入状态提供者'), findsOneWidget);
  });

  testWidgets('角色切到目镜后，配对请求带 eyepiece', (tester) async {
    tester.view.physicalSize = const Size(420, 5200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings =
        SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    final _FakeGateway fake = _FakeGateway();

    await tester.pumpWidget(_wrap(Scaffold(
      body: SettingsPage(
        settings: settings,
        radio: RadioController(),
        gatewayClientBuilder: (String host, int port) => DeviceGatewayClient(
          host: host,
          port: port,
          transport: fake.call,
        ),
      ),
    )));
    await tester.pump();

    await tester.enterText(_fieldByLabel('设备名'), '目镜机');
    // 打开角色下拉，选「目镜（认星）」。
    await tester.tap(find.byIcon(Icons.arrow_drop_down).first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('目镜（认星）').last);
    await tester.pumpAndSettle();
    expect(settings.deviceGatewayRole, 'eyepiece');

    await tester.enterText(_fieldByLabel('配对码'), '654321');
    await tester.tap(find.widgetWithText(FilledButton, '配对'));
    await tester.pumpAndSettle();

    expect(fake.postBodies.single['role'], 'eyepiece');
    expect(settings.deviceGatewayPaired, isTrue);
    expect(find.text('已配对 · 目镜（认星）'), findsOneWidget);
  });
}
