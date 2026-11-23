// ============================================================================
// DeviceGatewayClient —— 桌面「多设备网关」的只读客户端（M2.2 数据层）。
// ----------------------------------------------------------------------------
// * 端点与桌面 mbdsdr_ai/device_gateway.py 一一对应：
//     POST /pair  GET /roster  GET /panorama  GET /passes  POST /pose  GET /poses
// * 只读：唯一写动作是 /pair（换 token）与 /pose（自己的位姿）。
//   没有调谐/录音/改站址端点——那些仍归桌面权威端。
// * 传输缝可注入（与 ControlHubClient / AiClient 同范式），测试完全离线。
// * 失败一律抛 [DeviceGatewayException]，文案优先取服务端 reason；
//   不把「不可达」渲染成成功，也不造假数据。
// ============================================================================

import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// 角色白名单，与桌面 `device_gateway.ROLES` 一致。
const List<String> kDeviceRoles = <String>['lens', 'eyepiece', 'handheld', 'headset'];

/// 允许上报位姿的角色。
const List<String> kPoseRoles = <String>['lens', 'eyepiece'];

/// HTTP 结果（状态码 + 响应体）。
class GatewayHttpResponse {
  final int status;
  final String body;
  const GatewayHttpResponse(this.status, this.body);
}

/// 请求缝：[url] 已含 host/port/path；[headers] 含 Content-Type 与 Bearer。
typedef GatewayTransport = Future<GatewayHttpResponse> Function(
    Uri url, String method, Map<String, String> headers, String? body);

class DeviceGatewayException implements Exception {
  final String message;
  final int? status;
  DeviceGatewayException(this.message, {this.status});

  @override
  String toString() => 'DeviceGatewayException($status): $message';
}

class DeviceGatewayClient {
  final String host;
  final int port;
  final Duration timeout;
  final GatewayTransport _transport;

  DeviceGatewayClient({
    required this.host,
    required this.port,
    GatewayTransport? transport,
    this.timeout = const Duration(seconds: 6),
  }) : _transport = transport ?? _defaultTransport;

  Uri _uri(String path) => Uri.parse('http://$host:$port$path');

  String get baseUrl => 'http://$host:$port';

  // ------------------------------------------------------------------ 配对
  /// 用 6 位配对码换 token。成功返回 {'token': ..., 'device': {...}}。
  Future<Map<String, dynamic>> pair({
    required String code,
    required String deviceName,
    required String role,
  }) async {
    if (!kDeviceRoles.contains(role)) {
      throw DeviceGatewayException('角色必须是 ${kDeviceRoles.join('/')} 之一');
    }
    final Map<String, dynamic> data = await _post('/pair', <String, dynamic>{
      'code': code,
      'device_name': deviceName,
      'role': role,
    });
    final Object? token = data['token'];
    if (token is! String || token.isEmpty) {
      throw DeviceGatewayException('配对回执缺少 token，未保存任何凭据');
    }
    return data;
  }

  // ------------------------------------------------------------------ 只读
  Future<Map<String, dynamic>> roster(String token) =>
      _get('/roster', token);

  Future<Map<String, dynamic>> panorama(String token) =>
      _get('/panorama', token);

  Future<Map<String, dynamic>> poses(String token) =>
      _get('/poses', token);

  /// 权威端过境预报（`mbdsdr.sat-passes/1`）。
  Future<Map<String, dynamic>> passes(String token) =>
      _get('/passes', token);

  /// 上报一条 `sky-pose/1`。桌面会校验 source/role/时钟/边界，失败如实抛。
  ///
  /// [identification] 仅目镜角色可带（星名/HIP/角距残差），桌面按独立字段存。
  Future<Map<String, dynamic>> postPose(String token, {
    required String deviceId,
    required String role,
    required double tUtcSeconds,
    required double azDeg,
    required double altDeg,
    required double accuracyDeg,
    double rollDeg = 0.0,
    double? fovDeg,
    Map<String, dynamic>? identification,
  }) async {
    if (!kPoseRoles.contains(role)) {
      throw DeviceGatewayException('角色 $role 只读，不能上报位姿');
    }
    return _post('/pose', <String, dynamic>{
      'schema': 'sky-pose/1',
      'source': deviceId,
      'role': role,
      't_utc': tUtcSeconds,
      'az_deg': azDeg,
      'alt_deg': altDeg,
      'accuracy_deg': accuracyDeg,
      'roll_deg': rollDeg,
      'fov_deg': ?fovDeg,
      'identification': ?identification,
    }, token: token);
  }

  // ------------------------------------------------------------------ 传输
  Future<Map<String, dynamic>> _get(String path, String token) async {
    final GatewayHttpResponse r = await _send(path, 'GET', null, token);
    return _decode(r);
  }

  Future<Map<String, dynamic>> _post(
    String path,
    Map<String, dynamic> payload, {
    String? token,
  }) async {
    final GatewayHttpResponse r = await _send(
        path, 'POST', jsonEncode(payload), token);
    return _decode(r);
  }

  Future<GatewayHttpResponse> _send(
      String path, String method, String? body, String? token) async {
    final Map<String, String> headers = <String, String>{
      'Content-Type': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
    try {
      return await _transport(_uri(path), method, headers, body)
          .timeout(timeout);
    } on DeviceGatewayException {
      rethrow;
    } catch (e) {
      throw DeviceGatewayException('连不上桌面网关 $baseUrl：$e');
    }
  }

  Map<String, dynamic> _decode(GatewayHttpResponse r) {
    Map<String, dynamic>? data;
    try {
      final Object? parsed = jsonDecode(r.body);
      if (parsed is Map<String, dynamic>) data = parsed;
    } catch (_) {
      data = null;
    }
    if (r.status >= 200 && r.status < 300) {
      if (data == null) {
        throw DeviceGatewayException('网关返回不是 JSON 对象', status: r.status);
      }
      return data;
    }
    final String reason = (data?['reason'] ?? data?['error'] ?? r.body).toString();
    throw DeviceGatewayException(reason.trim().isEmpty ? 'HTTP ${r.status}' : reason,
        status: r.status);
  }

  /// 默认走 dart:io HttpClient；测试注入 fake 即可离线。
  static Future<GatewayHttpResponse> _defaultTransport(
      Uri url, String method, Map<String, String> headers, String? body) async {
    final HttpClient client = HttpClient();
    try {
      final HttpClientRequest req = await client.openUrl(method, url);
      headers.forEach(req.headers.set);
      if (body != null) req.write(body);
      final HttpClientResponse resp = await req.close();
      final String text = await resp.transform(utf8.decoder).join();
      return GatewayHttpResponse(resp.statusCode, text);
    } finally {
      client.close(force: true);
    }
  }
}
