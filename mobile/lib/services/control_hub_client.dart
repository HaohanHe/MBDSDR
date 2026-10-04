// ============================================================================
// ControlHubClient —— 桌面 ControlHub HTTP JSON 只读客户端（移动端 = 查看器）。
// ----------------------------------------------------------------------------
// * 只读：仅 GET /status /pocsag_messages /m17_calls /vor_radial；
// * [HttpGetTransport] 是可注入请求缝（与 AiClient.ChatTransport 同范式），
//   测试注入 fake 即可完全离线、不触碰真实网络；
// * 服务不可达（无监听/拒绝连接/超时）-> 抛 [ControlHubException]，文案诚实，
//   不伪造任何读数；面板据此渲染「服务不可达 + 重试」空态；
// * 端口不硬编码、host/port 由设置注入；解析按冻结契约容错。
// ============================================================================

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/control_hub.dart';

/// 请求缝：给定 [url]，返回响应体字符串。默认实现走 dart:io HttpClient；
/// 测试注入 fake 即可离线。
typedef HttpGetTransport = Future<String> Function(Uri url);

/// 业务异常：message 已经过过滤，是面向用户的诚实错误描述。
class ControlHubException implements Exception {
  final String message;
  ControlHubException(this.message);

  @override
  String toString() => 'ControlHubException: $message';
}

class ControlHubClient {
  /// 桌面主机（已由调用方 trim，非空）。
  final String host;

  /// 桌面 ControlHub HTTP 端口。
  final int port;

  /// 单次请求超时。
  final Duration timeout;

  /// 连接级失败时的最大尝试次数（1 = 不重试；>=2 = 额外重试）。
  final int maxAttempts;

  final HttpGetTransport _transport;

  ControlHubClient({
    required this.host,
    required this.port,
    HttpGetTransport? transport,
    this.timeout = const Duration(seconds: 3),
    this.maxAttempts = 2,
  }) : _transport = transport ?? _defaultGet;

  Uri _uri(String path) => Uri.parse('http://$host:$port$path');

  /// 真实服务基址（仅用于日志/错误文案展示，不含任何凭证）。
  String get baseUrl => 'http://$host:$port';

  // ------------------------------------------------------------------ GET
  /// 发起一次 GET，连接级失败按 [maxAttempts] 重试；解析/协议错误不重试。
  Future<String> _get(String path) async {
    final Uri url = _uri(path);
    int attempt = 0;
    Object? lastError;
    while (attempt < maxAttempts) {
      attempt++;
      try {
        return await _transport(url);
      } on ControlHubException {
        rethrow; // 协议/HTTP 错误（如 5xx）不属瞬时抖动，直接上抛。
      } catch (e) {
        lastError = e;
        if (attempt >= maxAttempts) break;
        // 连接级瞬时失败：短暂退避后重试。
        await Future<void>.delayed(const Duration(milliseconds: 250));
      }
    }
    throw ControlHubException(_friendlyUnreachable(url, lastError));
  }

  static String _friendlyUnreachable(Uri url, Object? e) {
    if (e is SocketException) {
      return '无法连接桌面 ControlHub（$url）：${e.osError?.message ?? e.message}';
    }
    if (e is TimeoutException) {
      return '请求超时：$url 未在时限内响应';
    }
    return '无法连接桌面 ControlHub（$url）：$e';
  }

  // ------------------------------------------------------------- 高层 API
  /// GET /status —— 引擎整机状态快照。
  Future<EngineStatus> fetchStatus() async {
    final String body = await _get('/status');
    return EngineStatus.fromJson(_decodeObject(body));
  }

  /// GET /pocsag_messages —— 全部累积寻呼消息（newest 顺序由桌面决定）。
  Future<List<PocsagMessage>> fetchPocsagMessages() async {
    final Object decoded = _decodeObject(await _get('/pocsag_messages'));
    final Object? messages = decoded is Map ? decoded['messages'] : null;
    if (messages is! List) return const <PocsagMessage>[];
    return messages
        .map(PocsagMessage.fromJson)
        .whereType<PocsagMessage>()
        .toList(growable: false);
  }

  /// GET /m17_calls —— 全部累积 M17 呼叫/帧。
  Future<List<M17Call>> fetchM17Calls() async {
    final Object decoded = _decodeObject(await _get('/m17_calls'));
    final Object? calls = decoded is Map ? decoded['calls'] : null;
    if (calls is! List) return const <M17Call>[];
    return calls.map(M17Call.fromJson).whereType<M17Call>().toList(growable: false);
  }

  /// GET /vor_radial —— 最新一次 VOR 读数（未锁定返回诚实 unlocked）。
  Future<VorRadial> fetchVorRadial() async {
    return VorRadial.fromJson(_decodeObject(await _get('/vor_radial')));
  }

  // ------------------------------------------------------------------ 解析
  static Object _decodeObject(String body) {
    try {
      final Object? decoded = jsonDecode(body);
      if (decoded is Map) return decoded;
    } on FormatException {
      // 落到下面抛协议错。
    }
    throw ControlHubException('ControlHub 返回了非 JSON 响应');
  }

  // ------------------------------------------------------------- 默认 transport
  static Future<String> _defaultGet(Uri url) async {
    final HttpClient client = HttpClient();
    try {
      final HttpClientRequest request = await client.getUrl(url);
      request.headers.set('Accept', 'application/json');
      final HttpClientResponse response =
          await request.close().timeout(const Duration(seconds: 3));
      final String body =
          await response.transform(utf8.decoder).join();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ControlHubException(
            'ControlHub 返回 HTTP ${response.statusCode}');
      }
      return body;
    } on SocketException {
      rethrow; // 由 _get 统一翻译为不可达文案。
    } on TimeoutException {
      rethrow;
    } finally {
      client.close(force: true);
    }
  }
}
