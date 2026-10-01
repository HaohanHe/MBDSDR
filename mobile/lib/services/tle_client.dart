import 'dart:async';
import 'dart:io';

import 'package:mbdsdr_mobile/astro/tle.dart';

/// Celestrak 卫星分组（与 gp.php?GROUP= 对应）。
enum TleGroup {
  stations,
  amateur,
  weather,
  noaa,
  gnss;

  /// Celestrak 分组名。
  String get query => switch (this) {
        TleGroup.stations => 'stations',
        TleGroup.amateur => 'amateur',
        TleGroup.weather => 'weather',
        TleGroup.noaa => 'noaa',
        // GNSS 导航星座合集（GPS/GLONASS/Galileo/BeiDou），供在视导航星预测。
        TleGroup.gnss => 'gnss',
      };
}

/// TLE 拉取失败时抛出的诚实异常（无静默回退、无假数据）。
class TleFetchException implements Exception {
  TleFetchException(this.message, [this.cause]);

  final String message;
  final Object? cause;

  @override
  String toString() =>
      cause == null ? 'TleFetchException: $message' : 'TleFetchException: $message ($cause)';
}

/// 从 Celestrak 拉取 TLE 并做内存缓存。
///
/// 仅使用 dart:io [HttpClient]，15s 超时；不内置任何密钥/坐标。
class TleClient {
  TleClient({HttpClient? httpClient, this.userAgent = 'mbdsdr-sky/1.0'})
      : _http = httpClient ?? HttpClient();

  static const String _baseUrl =
      'https://celestrak.org/NORAD/elements/gp.php';
  static const Duration timeout = Duration(seconds: 15);

  final HttpClient _http;
  final String userAgent;

  /// 上次成功拉取的分组 -> TLE 列表。
  final Map<TleGroup, List<Tle>> _cache = <TleGroup, List<Tle>>{};

  /// 各分组上次成功拉取时间（UTC）；未拉取为 null。
  final Map<TleGroup, DateTime> _lastUpdated = <TleGroup, DateTime>{};

  /// 已缓存的某分组 TLE（可能为空列表）；从未拉取返回 null。
  List<Tle>? cached(TleGroup group) => _cache[group];

  /// 某分组上次成功拉取时间。
  DateTime? lastUpdated(TleGroup group) => _lastUpdated[group];

  /// 拉取某分组 TLE。成功后写入缓存并返回。失败抛 [TleFetchException]。
  Future<List<Tle>> fetch(TleGroup group) async {
    final uri = Uri.parse('$_baseUrl?GROUP=${group.query}&FORMAT=tle');
    try {
      final req = await _http.getUrl(uri).timeout(timeout);
      req.headers.set(HttpHeaders.userAgentHeader, userAgent);
      final resp = await req.close().timeout(timeout);
      final chunks = <String>[];
      await for (final data in resp.timeout(timeout)) {
        chunks.add(String.fromCharCodes(data));
      }
      final text = chunks.join();
      if (resp.statusCode != HttpStatus.ok) {
        throw TleFetchException(
            'Celestrak 返回 HTTP ${resp.statusCode}（$uri）');
      }
      if (text.trim().isEmpty) {
        throw TleFetchException('Celestrak 返回空文本（$uri）');
      }
      final result = Tle.parseBatch(text);
      if (result.isEmpty) {
        throw TleFetchException('未能从响应中解析出 TLE（$uri）');
      }
      _cache[group] = result;
      _lastUpdated[group] = DateTime.now().toUtc();
      return result;
    } on TimeoutException {
      throw TleFetchException('拉取 $group 超时（${timeout.inSeconds}s）');
    } on TleFetchException {
      rethrow;
    } catch (e) {
      throw TleFetchException('拉取 $group 失败', e);
    }
  }

  /// 释放底层 client。
  void close() => _http.close(force: true);
}
