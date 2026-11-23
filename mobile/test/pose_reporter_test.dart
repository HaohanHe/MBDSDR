// 位姿上报：手机把真实朝向作为 sky-pose/1 发给桌面权威端（M3.1）。
//
// 纯单元 + 一条指天视图 widget 用例，全部离线（假传输不出网）。
// 覆盖：可用性门槛、缺传感器拒绝、载荷字段、精度夹取、服务端错误透传、
// 按钮禁用原因与成功提示。
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/asterism_overlay.dart';
import 'package:mbdsdr_mobile/astro/sky_assets.dart';
import 'package:mbdsdr_mobile/pages/pointing_page.dart';
import 'package:mbdsdr_mobile/services/device_gateway_client.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/pose_reporter.dart';
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

class _Call {
  _Call(this.url, this.method, this.headers, this.body);
  final Uri url;
  final String method;
  final Map<String, String> headers;
  final String? body;

  Map<String, dynamic> get json => jsonDecode(body!) as Map<String, dynamic>;
}

class _FakeGateway {
  _FakeGateway(this.respond);

  final GatewayHttpResponse Function(_Call call) respond;
  final List<_Call> calls = <_Call>[];

  Future<GatewayHttpResponse> call(
      Uri url, String method, Map<String, String> headers, String? body) async {
    final _Call c = _Call(url, method, headers, body);
    calls.add(c);
    return respond(c);
  }
}

SettingsService _pairedSettings({
  String role = 'lens',
  bool paired = true,
  bool withDeviceId = true,
}) {
  final SettingsService s = SettingsService(kv: _MemKv(), secure: _MemSecure());
  s.controlHubHost = '10.0.0.5';
  s.deviceGatewayPort = 8771;
  s.deviceGatewayRole = role;
  s.deviceGatewayName = '手机A';
  s.deviceGatewayToken = paired ? 'tok-1' : '';
  s.deviceGatewayDeviceId = withDeviceId ? 'dev-1' : '';
  return s;
}

PoseReporter _reporter(SettingsService s, _FakeGateway fake) => PoseReporter(
      settings: s,
      clock: () => DateTime.utc(2026, 1, 1),
      clientBuilder: (String host, int port) =>
          DeviceGatewayClient(host: host, port: port, transport: fake.call),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('PoseReporter 可用性', () {
    test('已配对 + lens + 设备 id + 主机 → 可用', () {
      final SettingsService s = _pairedSettings();
      expect(s.deviceGatewayPaired, isTrue);
      expect(_reporter(s, _FakeGateway((_) => const GatewayHttpResponse(200, '{}'))).available,
          isTrue);
    });

    test('未配对 / 只读角色 / 缺设备 id / 缺主机各自给出具体原因', () {
      final _FakeGateway fake =
          _FakeGateway((_) => const GatewayHttpResponse(200, '{}'));
      expect(_reporter(_pairedSettings(paired: false), fake).unavailableReason,
          contains('未配对'));
      expect(_reporter(_pairedSettings(role: 'handheld'), fake).unavailableReason,
          contains('只读'));
      expect(
          _reporter(_pairedSettings(withDeviceId: false), fake)
              .unavailableReason,
          contains('设备 id'));
      final SettingsService noHost = _pairedSettings();
      noHost.controlHubHost = '';
      expect(_reporter(noHost, fake).unavailableReason, contains('主机'));
      expect(fake.calls, isEmpty);
    });

    test('解除配对（清 token）会清掉设备 id', () {
      final SettingsService s = _pairedSettings();
      expect(s.deviceGatewayDeviceId, 'dev-1');
      s.deviceGatewayToken = '';
      expect(s.deviceGatewayDeviceId, '');
      expect(s.deviceGatewayPaired, isFalse);
    });
  });

  group('PoseReporter.report', () {
    test('载荷是 sky-pose/1：az 归一、alt/roll/精度/时刻/来源齐全', () async {
      final _FakeGateway fake = _FakeGateway(
          (_) => const GatewayHttpResponse(200, '{"ok":true}'));
      final PoseReporter r = _reporter(_pairedSettings(), fake);

      await r.report(headingDeg: 370.0, pitchDeg: 40.5, rollDeg: 2.0);

      final _Call c = fake.calls.single;
      expect(c.url.path, '/pose');
      expect(c.method, 'POST');
      expect(c.headers['Authorization'], 'Bearer tok-1');
      final Map<String, dynamic> sent = c.json;
      expect(sent['schema'], 'sky-pose/1');
      expect(sent['source'], 'dev-1');
      expect(sent['role'], 'lens');
      expect(sent['az_deg'], 10.0);
      expect(sent['alt_deg'], 40.5);
      expect(sent['roll_deg'], 2.0);
      expect(sent['accuracy_deg'], kDefaultPoseAccuracyDeg);
      // 2026-01-01T00:00:00Z
      expect(sent['t_utc'], 1767225600.0);
    });

    test('缺航向 / 缺俯仰 / 俯仰越界：本地拒绝，不发请求', () async {
      final _FakeGateway fake =
          _FakeGateway((_) => const GatewayHttpResponse(200, '{}'));
      final PoseReporter r = _reporter(_pairedSettings(), fake);

      await expectLater(
          r.report(headingDeg: null, pitchDeg: 40.0),
          throwsA(isA<DeviceGatewayException>()
              .having((e) => e.message, 'message', contains('罗盘'))));
      await expectLater(
          r.report(headingDeg: 100.0, pitchDeg: null),
          throwsA(isA<DeviceGatewayException>()
              .having((e) => e.message, 'message', contains('俯仰'))));
      await expectLater(
          r.report(headingDeg: 100.0, pitchDeg: 120.0),
          throwsA(isA<DeviceGatewayException>()
              .having((e) => e.message, 'message', contains('越界'))));
      expect(fake.calls, isEmpty);
    });

    test('精度夹到 [1, 30]：过小抬到 1，过大压到 30', () async {
      final _FakeGateway fake =
          _FakeGateway((_) => const GatewayHttpResponse(200, '{"ok":true}'));
      final PoseReporter r = _reporter(_pairedSettings(), fake);

      await r.report(headingDeg: 1, pitchDeg: 2, accuracyDeg: 0.2);
      await r.report(headingDeg: 1, pitchDeg: 2, accuracyDeg: 99);
      expect(fake.calls[0].json['accuracy_deg'], 1.0);
      expect(fake.calls[1].json['accuracy_deg'], 30.0);
    });

    test('服务端错误（403）透传 reason，不当成功', () async {
      final _FakeGateway fake = _FakeGateway(
          (_) => const GatewayHttpResponse(403, '{"reason":"角色只读"}'));
      final PoseReporter r = _reporter(_pairedSettings(), fake);

      await expectLater(
          r.report(headingDeg: 1, pitchDeg: 2),
          throwsA(isA<DeviceGatewayException>()
              .having((e) => e.status, 'status', 403)
              .having((e) => e.message, 'message', contains('角色只读'))));
    });
  });

  group('指天视图上报条', () {
    testWidgets('点按钮把当前朝向发出，并显示真实结果', (tester) async {
      final _FakeGateway fake =
          _FakeGateway((_) => const GatewayHttpResponse(200, '{"ok":true}'));
      final PoseReporter reporter = _reporter(_pairedSettings(), fake);
      final StreamController<DeviceOrientation> stream =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(stream.close);

      await tester.pumpWidget(MaterialApp(
        home: PointingPage(
          site: const SkySite(latDeg: 45, lonDeg: 120),
          orientationStream: stream.stream,
          assetSource: _FakeAssetSource(_tinyAssets()),
          clock: () => DateTime.utc(2026, 11, 18, 12),
          minFrame: Duration.zero,
          sensorTimeout: const Duration(seconds: 10),
          poseReporter: reporter,
        ),
      ));
      stream.add(const DeviceOrientation(
        heading: 100,
        pitch: 40,
        roll: 2,
        hasCompass: true,
        hasImu: true,
      ));
      await tester.pump();
      await tester.pump();

      final Finder btn = find.byKey(const Key('pointing-report-pose'));
      expect(btn, findsOneWidget);
      await tester.tap(btn);
      await tester.pumpAndSettle();

      final Map<String, dynamic> sent = fake.calls.single.json;
      expect(sent['az_deg'], 100.0);
      expect(sent['alt_deg'], 40.0);
      expect(sent['roll_deg'], 2.0);
      expect(find.text('已上报位姿：方位 100.0° 高度 40.0°'), findsOneWidget);
    });

    testWidgets('只读角色：按钮禁用并显示具体原因', (tester) async {
      final _FakeGateway fake =
          _FakeGateway((_) => const GatewayHttpResponse(200, '{}'));
      final PoseReporter reporter =
          _reporter(_pairedSettings(role: 'handheld'), fake);
      final StreamController<DeviceOrientation> stream =
          StreamController<DeviceOrientation>.broadcast();
      addTearDown(stream.close);

      await tester.pumpWidget(MaterialApp(
        home: PointingPage(
          site: const SkySite(latDeg: 45, lonDeg: 120),
          orientationStream: stream.stream,
          assetSource: _FakeAssetSource(_tinyAssets()),
          minFrame: Duration.zero,
          sensorTimeout: const Duration(seconds: 10),
          poseReporter: reporter,
        ),
      ));
      // 有姿态才会渲染天空与底部控件。
      stream.add(const DeviceOrientation(
        heading: 100,
        pitch: 40,
        roll: 0,
        hasCompass: true,
        hasImu: true,
      ));
      await tester.pump();
      await tester.pump();

      final Finder btn = find.byKey(const Key('pointing-report-pose'));
      expect(btn, findsOneWidget);
      final OutlinedButton button = tester.widget<OutlinedButton>(btn);
      expect(button.onPressed, isNull);
      expect(find.textContaining('只读'), findsOneWidget);
      expect(fake.calls, isEmpty);
    });
  });
}

// ---------------------------------------------------------------- 测试资产
SkyAssets _tinyAssets() {
  return const SkyAssets(
    stars: <SkyStar>[
      SkyStar(raDeg: 10.0, decDeg: 20.0, vmag: 1.0, bv: 0.2, hip: 1),
      SkyStar(raDeg: 12.0, decDeg: 22.0, vmag: 2.0, bv: 0.9, hip: 2),
    ],
    catalog: AsterismCatalog(
      asterisms: <Asterism>[],
      regionNames: <String, String>{},
      singleStarNames: <String>[],
      license: 'CC BY 4.0（测试）',
    ),
    license: 'CC BY 4.0（测试）',
  );
}

class _FakeAssetSource implements SkyAssetSource {
  _FakeAssetSource(this.assets);
  final SkyAssets assets;

  @override
  Future<SkyAssets> load() async => assets;
}
