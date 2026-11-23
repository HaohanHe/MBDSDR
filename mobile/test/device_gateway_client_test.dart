// DeviceGatewayClient：按桌面 device_gateway.py 的冻结 HTTP 契约收发，
// 服务端拒绝/不可达一律诚实抛 [DeviceGatewayException]。
//
// 全部用注入的 fake transport 离线跑：不触碰真实网络。
// 覆盖：
//   * /pair 成功保存 token、请求体字段齐全；
//   * 错码 403 / 锁定 429 原样透出服务端 reason；
//   * 回执缺 token 时不静默当成功；
//   * /roster /panorama /poses 带 Bearer 且解析 JSON；
//   * 只读角色（handheld/headset）本地即拒发位姿，一个字节都不发；
//   * lens/eyepiece 的 /pose 载荷是 sky-pose/1；
//   * 非 JSON 成功响应、socket 异常都翻成明确错误。
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/services/device_gateway_client.dart';

/// 记录每次请求的假传输。
class _FakeTransport {
  _FakeTransport(this.responder);

  final GatewayHttpResponse Function(
      Uri url, String method, Map<String, String> headers, String? body) responder;

  final List<_Call> calls = <_Call>[];

  Future<GatewayHttpResponse> call(
      Uri url, String method, Map<String, String> headers, String? body) async {
    calls.add(_Call(url, method, headers, body));
    return responder(url, method, headers, body);
  }

  _Call get last => calls.last;
}

class _Call {
  _Call(this.url, this.method, this.headers, this.body);

  final Uri url;
  final String method;
  final Map<String, String> headers;
  final String? body;

  Map<String, dynamic> get json => jsonDecode(body!) as Map<String, dynamic>;
}

DeviceGatewayClient _client(_FakeTransport t) => DeviceGatewayClient(
      host: '192.168.1.20',
      port: 8771,
      transport: t.call,
    );

void main() {
  test('pair 成功：token 回执 + 请求体字段齐全', () async {
    final _FakeTransport t = _FakeTransport((url, method, headers, body) {
      expect(url.path, '/pair');
      expect(method, 'POST');
      expect(headers['Content-Type'], 'application/json');
      expect(headers.containsKey('Authorization'), isFalse,
          reason: '配对前不应带 Bearer');
      final Map<String, dynamic> sent = jsonDecode(body!) as Map<String, dynamic>;
      expect(sent['code'], '123456');
      expect(sent['device_name'], '目镜机');
      expect(sent['role'], 'eyepiece');
      return const GatewayHttpResponse(
          200, '{"token":"tok-abc","device":{"name":"目镜机","role":"eyepiece"}}');
    });
    final DeviceGatewayClient c = _client(t);

    final Map<String, dynamic> out = await c.pair(
      code: '123456',
      deviceName: '目镜机',
      role: 'eyepiece',
    );

    expect(out['token'], 'tok-abc');
    expect((out['device'] as Map)['role'], 'eyepiece');
  });

  test('pair 错码 403：透出服务端 reason', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(
            403, '{"error":"bad_code","reason":"配对码错误或已轮换"}'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.pair(code: '000000', deviceName: 'x', role: 'handheld'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.status, 'status', 403)
          .having((e) => e.message, 'message', contains('配对码'))),
    );
  });

  test('pair 锁定 429：status 与 reason 都保留', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(
            429, '{"reason":"尝试过多，60 秒后重试"}'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.pair(code: '111111', deviceName: 'x', role: 'handheld'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.status, 'status', 429)
          .having((e) => e.message, 'message', contains('尝试过多'))),
    );
  });

  test('pair 回执缺 token：不当成功，明确报错', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(200, '{"device":{}}'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.pair(code: '222222', deviceName: 'x', role: 'handheld'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.message, 'message', contains('token'))),
    );
  });

  test('pair 非法角色：本地拒绝，不发请求', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(200, '{}'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.pair(code: '333333', deviceName: 'x', role: 'camera'),
      throwsA(isA<DeviceGatewayException>()),
    );
    expect(t.calls, isEmpty);
  });

  test('roster/panorama/poses：带 Bearer 且解析 JSON', () async {
    final _FakeTransport t = _FakeTransport((url, method, headers, body) {
      expect(method, 'GET');
      expect(headers['Authorization'], 'Bearer tok-abc');
      switch (url.path) {
        case '/roster':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.device-roster/1","devices":[]}');
        case '/panorama':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.sky-panorama/1","missing":["cloud"]}');
        case '/poses':
          return const GatewayHttpResponse(200, '{"poses":[]}');
        case '/passes':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.sat-passes/1","count":0,"passes":[]}');
        case '/status':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.desktop-status/1","station":null,'
                  '"sdr":null,"missing":["station","sdr"]}');
        case '/spectrum':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.spectrum-snapshot/1","readonly":true,'
                  '"realtime":false,"bins":4,"powers_db":[-80.0,null,-70.0],'
                  '"peak_freq_hz":100000000.0,"peak_power_db":-70.0}');
        case '/fuse':
          return const GatewayHttpResponse(
              200, '{"schema":"mbdsdr.pose-fusion/1","observations":2,'
                  '"valid":true,"az_deg":77.57,"alt_deg":0.91,'
                  '"accuracy_deg":0.62,"sources":["dev-a","dev-b"]}');
      }
      fail('unexpected path ${url.path}');
    });
    final DeviceGatewayClient c = _client(t);

    expect((await c.roster('tok-abc'))['schema'], 'mbdsdr.device-roster/1');
    expect((await c.panorama('tok-abc'))['schema'], 'mbdsdr.sky-panorama/1');
    expect((await c.poses('tok-abc'))['poses'], isEmpty);
    expect((await c.passes('tok-abc'))['schema'], 'mbdsdr.sat-passes/1');
    expect((await c.status('tok-abc'))['schema'], 'mbdsdr.desktop-status/1');
    expect((await c.spectrum('tok-abc'))['schema'],
        'mbdsdr.spectrum-snapshot/1');
    expect((await c.fuse('tok-abc'))['schema'], 'mbdsdr.pose-fusion/1');
    expect(t.calls.map((e) => e.url.path), <String>[
      '/roster', '/panorama', '/poses', '/passes', '/status', '/spectrum',
      '/fuse']);
  });

  test('spectrum 无帧 503：透出服务端 reason，不伪造快照', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(
            503, '{"error":"spectrum_unavailable","reason":"没有可读的频谱帧"}'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.spectrum('tok-abc'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.status, 'status', 503)
          .having((e) => e.message, 'message', contains('没有可读的频谱帧'))),
    );
  });

  test('只读角色上报位姿：本地拒绝，不发请求', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(200, '{}'));
    final DeviceGatewayClient c = _client(t);

    for (final String role in <String>['handheld', 'headset']) {
      await expectLater(
        c.postPose('tok-abc',
            deviceId: 'dev-1',
            role: role,
            tUtcSeconds: 1700000000,
            azDeg: 120,
            altDeg: 30,
            accuracyDeg: 1),
        throwsA(isA<DeviceGatewayException>()
            .having((e) => e.message, 'message', contains('只读'))),
      );
    }
    expect(t.calls, isEmpty);
  });

  test('lens 上报：载荷是 sky-pose/1 且 source=设备', () async {
    final _FakeTransport t = _FakeTransport((url, method, headers, body) {
      expect(url.path, '/pose');
      expect(method, 'POST');
      expect(headers['Authorization'], 'Bearer tok-abc');
      return const GatewayHttpResponse(200, '{"ok":true}');
    });
    final DeviceGatewayClient c = _client(t);

    await c.postPose(
      'tok-abc',
      deviceId: 'lens-7',
      role: 'lens',
      tUtcSeconds: 1700000000.5,
      azDeg: 214.2,
      altDeg: 41.8,
      accuracyDeg: 0.6,
      fovDeg: 8.5,
    );

    final Map<String, dynamic> sent = t.last.json;
    expect(sent['schema'], 'sky-pose/1');
    expect(sent['source'], 'lens-7');
    expect(sent['role'], 'lens');
    expect(sent['t_utc'], 1700000000.5);
    expect(sent['az_deg'], 214.2);
    expect(sent['alt_deg'], 41.8);
    expect(sent['accuracy_deg'], 0.6);
    expect(sent['roll_deg'], 0.0);
    expect(sent['fov_deg'], 8.5);
  });

  test('200 但非 JSON：抛协议错，不静默吞', () async {
    final _FakeTransport t = _FakeTransport(
        (_, _, _, _) => const GatewayHttpResponse(200, 'OK'));
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.roster('tok-abc'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.message, 'message', contains('不是 JSON'))),
    );
  });

  test('不可达：文案含网关地址，不当成功', () async {
    final _FakeTransport t = _FakeTransport((_, _, _, _) {
      throw const SocketException('Connection refused');
    });
    final DeviceGatewayClient c = _client(t);

    await expectLater(
      c.panorama('tok-abc'),
      throwsA(isA<DeviceGatewayException>()
          .having((e) => e.message, 'message', contains('192.168.1.20:8771'))),
    );
  });

  // 真实回环 HTTP：默认传输必须发定长请求体。桌面网关只按 Content-Length
  // 读 body；若这里退化成 chunked，网关会把 role/code 读成空串，
  // 而注入 fake 传输的用例永远发现不了（2026-11-24 模拟器实测缺陷）。
  test('默认传输：回环 HTTP 定长请求体，role/code 不丢', () async {
    final HttpServer server =
        await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(() => server.close(force: true));
    String? rawBody;
    String? contentType;
    int? contentLength;
    server.listen((HttpRequest request) async {
      rawBody = await utf8.decoder.bind(request).join();
      contentType = request.headers.contentType?.mimeType;
      contentLength = request.headers.contentLength;
      request.response
        ..statusCode = 200
        ..headers.contentType = ContentType.json
        ..write('{"token":"tok-real","device":{"id":"dev-9","role":"handheld"}}');
      await request.response.close();
    });

    final DeviceGatewayClient c =
        DeviceGatewayClient(host: '127.0.0.1', port: server.port);
    final Map<String, dynamic> out = await c.pair(
      code: '654321',
      deviceName: '手机C',
      role: 'handheld',
    );

    expect(out['token'], 'tok-real');
    expect(rawBody, isNotNull);
    expect(contentLength, utf8.encode(rawBody!).length);
    expect(contentType, 'application/json');
    final Map<String, dynamic> sent =
        jsonDecode(rawBody!) as Map<String, dynamic>;
    expect(sent['code'], '654321');
    expect(sent['device_name'], '手机C');
    expect(sent['role'], 'handheld');
  });
}
