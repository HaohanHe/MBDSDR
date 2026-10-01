// GNSS 定位融合器：把逐帧 NMEA 记录合并成一个最新 fix 快照。
//
// 诚实红线（移植自 serial_gnss.py SerialGNSSReader.get_fix）：
//  - 收到合法 NMEA → source="real"，但 GGA fixQuality=0 / RMC status=V 时
//    坐标保持 null（"收到语句但未定位"），绝不返回 (0,0)；
//  - 超过 [fixStale]（默认 10s）无新语句 → 显式退化为 source="none"、坐标全 null，
//    杜绝旧坐标残留。
library;

import 'nmea_parser.dart';

/// 最新融合 fix 快照。
class GnssFix {
  const GnssFix({
    required this.source,
    this.latitude,
    this.longitude,
    this.altitudeM,
    this.satellites,
    this.hdop,
    this.speedKmh,
    this.courseDeg,
    this.utcTime,
    this.fixQuality = 0,
  });

  /// "none"（无数据/过期）/ "real"（收到合法语句，即便未定）。
  final String source;

  final double? latitude;
  final double? longitude;
  final double? altitudeM;
  final int? satellites;
  final double? hdop;
  final double? speedKmh;
  final double? courseDeg;
  final String? utcTime;

  /// 0=无,1=GPS,2=DGPS,4/5=RTK。
  final int fixQuality;

  bool get hasFix => source == 'real' && latitude != null && longitude != null;

  static const GnssFix empty = GnssFix(source: 'none');
}

/// 合并逐帧 NMEA → 最新 fix。可注入时钟便于测试过期逻辑。
class GnssFixMerger {
  GnssFixMerger({
    DateTime Function()? clock,
    this.fixStale = const Duration(seconds: 10),
  }) : _now = clock ?? DateTime.now;

  final DateTime Function() _now;
  final Duration fixStale;

  GnssFix _fix = GnssFix.empty;
  DateTime? _lastSeen;

  /// 最近一帧 GSA（按 talker 缓存），供天空图。
  final Map<String, GsaRecord> latestGsa = {};

  /// 最近一帧聚合完成的 GSV（按 talker 缓存），供天空图。
  final Map<String, GsvRecord> latestGsv = {};

  /// 喂入一条解析后的 NMEA 记录。
  void feed(NmeaRecord rec) {
    _lastSeen = _now();
    if (rec is GgaRecord) {
      final gga = rec;
      _fix = GnssFix(
        source: 'real',
        latitude: gga.latitude ?? _fix.latitude,
        longitude: gga.longitude ?? _fix.longitude,
        altitudeM: gga.altitudeM ?? _fix.altitudeM,
        satellites: gga.satellites,
        hdop: gga.hdop ?? _fix.hdop,
        speedKmh: _fix.speedKmh,
        courseDeg: _fix.courseDeg,
        utcTime: gga.utcTime.isEmpty ? _fix.utcTime : gga.utcTime,
        // no-fix 诚实态：fixQuality=0 时坐标字段不保留（未定位）。
        fixQuality: gga.fixQuality,
      );
      // fixQuality=0 → 不保留旧坐标（未定位）。
      if (gga.fixQuality == 0) {
        _fix = GnssFix(
          source: 'real',
          satellites: gga.satellites,
          hdop: gga.hdop,
          utcTime: gga.utcTime.isEmpty ? _fix.utcTime : gga.utcTime,
          fixQuality: 0,
        );
      }
    } else if (rec is RmcRecord) {
      final rmc = rec;
      final lat = rmc.valid ? rmc.latitude : null;
      final lon = rmc.valid ? rmc.longitude : null;
      _fix = GnssFix(
        source: 'real',
        latitude: lat ?? _fix.latitude,
        longitude: lon ?? _fix.longitude,
        altitudeM: _fix.altitudeM,
        satellites: _fix.satellites,
        hdop: _fix.hdop,
        speedKmh: rmc.speedKmh ?? _fix.speedKmh,
        courseDeg: rmc.courseDeg ?? _fix.courseDeg,
        utcTime: rmc.utcTime.isEmpty ? _fix.utcTime : rmc.utcTime,
        fixQuality: _fix.fixQuality,
      );
    } else if (rec is VtgRecord) {
      final vtg = rec;
      _fix = GnssFix(
        source: 'real',
        latitude: _fix.latitude,
        longitude: _fix.longitude,
        altitudeM: _fix.altitudeM,
        satellites: _fix.satellites,
        hdop: _fix.hdop,
        speedKmh: vtg.speedKmh ?? _fix.speedKmh,
        courseDeg: vtg.courseDeg ?? _fix.courseDeg,
        utcTime: _fix.utcTime,
        fixQuality: _fix.fixQuality,
      );
    } else if (rec is GsaRecord) {
      latestGsa[rec.talker] = rec;
    } else if (rec is GsvRecord) {
      latestGsv[rec.talker] = rec;
    }
  }

  /// 最新 fix 快照。超过 [fixStale] 无新语句 → 返回 [GnssFix.empty]。
  GnssFix getFix() {
    final last = _lastSeen;
    if (last == null || _now().difference(last) > fixStale) {
      return GnssFix.empty;
    }
    return _fix;
  }
}
