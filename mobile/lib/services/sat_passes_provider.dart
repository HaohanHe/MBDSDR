// 卫星过境预测服务（只读）：把 Celestrak TLE + 本地 SGP4 过境计算
// 封装成 AI 工具可调用的、可注入的数据源。
//
// 诚实边界（逐条保留）：
//   * 生产用真实 [TleClient]（Celestrak），绝不内置/编造过境；
//   * 无 TLE（拉取失败）→ 抛 [TleFetchException]；
//   * 同名卫星无 TLE / 窗口内无过境 → 由调用方回诚实空态，不猜频率、不编过境；
//   * 测试用 [seedTles] 注入固定 TLE、[now] 固定时钟，全程禁真网。
library;

import 'package:flutter/foundation.dart';

import '../astro/coordinates.dart';
import '../astro/passes.dart';
import '../astro/sgp4.dart';
import '../astro/tle.dart';
import '../models/satellite.dart';
import 'tle_client.dart';

/// 过境预测在「无 TLE / 无同名卫星」等情形下的诚实异常。
class SatPassesException implements Exception {
  SatPassesException(this.message);

  final String message;

  @override
  String toString() => 'SatPassesException: $message';
}

/// 一次过境的精简结果（面向 AI 工具 JSON，字段与 C++ 侧 predict_passes 对齐）。
@immutable
class PredictedPass {
  const PredictedPass({
    required this.name,
    required this.startUtc,
    required this.endUtc,
    required this.maxTimeUtc,
    required this.maxElevationDeg,
    required this.azimuthDeg,
    required this.riseAzDeg,
    required this.setAzDeg,
  });

  final String name;

  /// 升出地平线时刻（UTC）。
  final DateTime startUtc;

  /// 落入地平线时刻（UTC）。
  final DateTime endUtc;

  /// 最高仰角时刻（UTC）。
  final DateTime maxTimeUtc;

  /// 最高仰角（度）。
  final double maxElevationDeg;

  /// 最高仰角时刻方位角（度，自北顺时针）。
  final double azimuthDeg;

  /// 升起方位角（度）。
  final double riseAzDeg;

  /// 落下方位角（度）。
  final double setAzDeg;

  /// 持续时长（秒）。
  int get durationSec => endUtc.difference(startUtc).inSeconds;
}

/// 卫星过境预测服务（只读）。
///
/// 与 UI 天空页共用同一份 [TleClient] + [predictPasses]，不另造执行路径。
class SatPassesService {
  SatPassesService({
    TleClient? tleClient,
    this.group = TleGroup.noaa,
    this.now,
  }) : _tle = tleClient ?? TleClient();

  final TleClient _tle;

  /// 默认拉取的 Celestrak 分组（NOAA 极轨气象星 = 本 app 的 SDR 卫星对象）。
  final TleGroup group;

  /// 时钟缝（返回 UTC）；默认 [DateTime.now]。测试注入固定时刻。
  final DateTime Function()? now;

  /// 测试缝：预置 TLE 列表（跳过网络）。生产保持 null。
  @visibleForTesting
  List<Tle>? seedTles;

  /// 解析可用 TLE 列表：优先用种子数据（测试），其次 TleClient 缓存，
  /// 缓存未命中才真拉 Celestrak；拉取失败抛 [TleFetchException]（诚实）。
  Future<List<Tle>> _resolveTles() async {
    final seeded = seedTles;
    if (seeded != null) return seeded;
    final cached = _tle.cached(group);
    if (cached != null && cached.isNotEmpty) return cached;
    return _tle.fetch(group);
  }

  /// 预测 [satelliteName] 未来 [hours] 小时内相对 [station] 的过境。
  ///
  /// 同名匹配：大小写不敏感、去空格后包含匹配（Celestrak 名如 "NOAA 19"）。
  /// 无同名 TLE 抛 [SatPassesException]；窗口内无过境返回空列表（由调用方诚实空态）。
  Future<List<PredictedPass>> predict({
    required String satelliteName,
    required Station station,
    double hours = 48,
  }) async {
    final tles = await _resolveTles();
    final needle = satelliteName.trim().toLowerCase();
    final matched =
        tles.where((Tle t) => t.name.toLowerCase().contains(needle)).toList();
    if (matched.isEmpty) {
      throw SatPassesException(
          '未找到卫星 "$satelliteName" 的 TLE（分组 ${group.query}）');
    }

    final start = (now?.call() ?? DateTime.now()).toUtc();
    final raw = predictPasses(
      matched,
      station,
      hours: hours.round(),
      stepSeconds: 60,
      startTime: start,
    );

    // predictPasses 只给 rise/set 方位；这里补「最高仰角时刻」方位角，
    // 用真实 SGP4 单点传播，不编造。
    final byCat = <int, Tle>{
      for (final Tle t in matched) t.catalogNumber: t,
    };
    final out = <PredictedPass>[];
    for (final Pass p in raw) {
      var azAtMax = p.riseAz;
      final Tle? tle = byCat[p.catalogNumber];
      if (tle != null) {
        try {
          azAtMax = azElAt(Sgp4(tle), p.maxTime, station).az;
        } on Sgp4Exception {
          azAtMax = p.riseAz; // 衰减卫星退为升起方位，不打断
        }
      }
      out.add(PredictedPass(
        name: p.name,
        startUtc: p.riseTime,
        endUtc: p.setTime,
        maxTimeUtc: p.maxTime,
        maxElevationDeg: p.maxEl,
        azimuthDeg: azAtMax,
        riseAzDeg: p.riseAz,
        setAzDeg: p.setAz,
      ));
    }
    return out;
  }
}
