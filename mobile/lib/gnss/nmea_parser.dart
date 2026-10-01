// NMEA-0183 解析器：移植自有 mbdsdr_ai/serial_gnss.py（MIT）到纯 Dart。
//
// 支持语句：GGA / RMC / GSA / GSV / VTG / ZDA / GLL / GST / TXT；
// 多星座 talker 前缀 GP(GPS)/GL(GLONASS)/GA(Galileo)/GB|BD(北斗)/GN(组合)。
// GSV 多帧按 (talker, totalMessages) 聚合，2s 未收齐末帧丢弃半截缓冲。
//
// 纯 Dart、零平台依赖、可注入时钟（[parse] 的 now 参数），云内全离线单测。
// 红线：非法/坏校验和/无定位一律返回 null 或 no-fix 诚实态，绝不抛、绝不造坐标。
library;

/// NMEA 校验和：'$' 与 '*' 之间所有字符逐字节 XOR。
int nmeaChecksum(String body) {
  var cs = 0;
  for (final ch in body.codeUnits) {
    cs ^= ch;
  }
  return cs & 0xFF;
}

/// 合法 talker 前缀白名单（公开约定）。
const Set<String> kNmeaTalkers = {'GP', 'GL', 'GA', 'GB', 'BD', 'GN'};

/// 一条解析后的 NMEA 记录（密封类，按 sentence 类型分支）。
sealed class NmeaRecord {
  const NmeaRecord({required this.talker, required this.sentence});
  final String talker;
  final String sentence;
}

/// GGA：定位数据（经纬度/高度/卫星数/HDOP）。
class GgaRecord extends NmeaRecord {
  const GgaRecord({
    required super.talker,
    required this.utcTime,
    required this.latitude,
    required this.longitude,
    required this.fixQuality,
    required this.satellites,
    required this.hdop,
    required this.altitudeM,
  }) : super(sentence: 'GGA');

  final String utcTime;
  final double? latitude;
  final double? longitude;

  /// 0=无定位,1=GPS,2=DGPS,4=RTK固定,5=RTK浮动。
  final int fixQuality;
  final int satellites;
  final double? hdop;
  final double? altitudeM;
}

/// RMC：推荐最小定位（时间/状态/经纬度/速度/航向/日期）。
class RmcRecord extends NmeaRecord {
  const RmcRecord({
    required super.talker,
    required this.utcTime,
    required this.status,
    required this.valid,
    required this.latitude,
    required this.longitude,
    required this.speedKnots,
    required this.speedKmh,
    required this.courseDeg,
    required this.date,
  }) : super(sentence: 'RMC');

  final String utcTime;

  /// 'A'=有效 'V'=无效。
  final String status;
  final bool valid;
  final double? latitude;
  final double? longitude;
  final double? speedKnots;
  final double? speedKmh;
  final double? courseDeg;
  final String date;
}

/// GSA：精度因子 + 参与定位的卫星号 / 定位模式。
class GsaRecord extends NmeaRecord {
  const GsaRecord({
    required super.talker,
    required this.mode,
    required this.fixType,
    required this.satellitesUsed,
    required this.pdop,
    required this.hdop,
    required this.vdop,
  }) : super(sentence: 'GSA');

  /// M=手动 A=自动。
  final String mode;

  /// 1=无 2=2D 3=3D。
  final int fixType;
  final List<int> satellitesUsed;
  final double? pdop;
  final double? hdop;
  final double? vdop;
}

/// 一颗可见卫星（GSV）。id 可能是 int（PRN）或厂商文本。
class GsvSat {
  const GsvSat({
    required this.id,
    required this.elevation,
    required this.azimuth,
    required this.snrDb,
  });
  final Object id; // int(PRN) 或 String
  final double? elevation;
  final double? azimuth;
  final double? snrDb;
}

/// GSV：可见卫星。多帧聚合后 aggregated=true、sats 为完整列表；中间帧 parse 返回 null。
class GsvRecord extends NmeaRecord {
  const GsvRecord({
    required super.talker,
    required this.totalMessages,
    required this.messageNumber,
    required this.satellitesInView,
    required this.sats,
    required this.aggregated,
  }) : super(sentence: 'GSV');

  final int totalMessages;
  final int messageNumber;
  final int satellitesInView;
  final List<GsvSat> sats;
  final bool aggregated;
}

/// VTG：航迹角 + 地面速度。
class VtgRecord extends NmeaRecord {
  const VtgRecord({
    required super.talker,
    required this.courseDeg,
    required this.speedKnots,
    required this.speedKmh,
  }) : super(sentence: 'VTG');
  final double? courseDeg;
  final double? speedKnots;
  final double? speedKmh;
}

/// ZDA：日期/时间（高精度授时）。
class ZdaRecord extends NmeaRecord {
  const ZdaRecord({
    required super.talker,
    required this.dateTimeUtc,
    required this.rawTime,
  }) : super(sentence: 'ZDA');
  final DateTime? dateTimeUtc;
  final String rawTime;
}

/// GLL：地理坐标（经纬度 + UTC 时间 + 状态）。
class GllRecord extends NmeaRecord {
  const GllRecord({
    required super.talker,
    required this.latitude,
    required this.longitude,
    required this.utcTime,
    required this.valid,
    required this.status,
  }) : super(sentence: 'GLL');
  final double? latitude;
  final double? longitude;
  final String utcTime;
  final bool valid;
  final String status;
}

/// GST：伪距噪声统计（各轴标准差）。
class GstRecord extends NmeaRecord {
  const GstRecord({
    required super.talker,
    required this.utcTime,
    required this.rmsStd,
    required this.majorSigma,
    required this.minorSigma,
    required this.orient,
    required this.latSigma,
    required this.lonSigma,
    required this.altSigma,
  }) : super(sentence: 'GST');
  final String utcTime;
  final double? rmsStd;
  final double? majorSigma;
  final double? minorSigma;
  final double? orient;
  final double? latSigma;
  final double? lonSigma;
  final double? altSigma;
}

/// TXT：厂商文本语句。
class TxtRecord extends NmeaRecord {
  const TxtRecord({required super.talker, required this.text})
      : super(sentence: 'TXT');
  final String text;
}

class _GsvBuffer {
  _GsvBuffer({required this.ts});
  final List<GsvRecord> frames = [];
  DateTime ts;
}

/// NMEA-0183 解析器（有状态：GSV 多帧聚合缓冲）。
class NmeaParser {
  final Map<String, _GsvBuffer> _gsvBuf = {};

  static const Duration _gsvAggTimeout = Duration(seconds: 2);

  /// 把 ddmm.mmmm(纬度)/dddmm.mmmm(经度) 转十进制度；S/W 取负；空→null。
  static double? _ddmmToDeg(String? value, String? hemi, bool isLat) {
    if (value == null || value.isEmpty) return null;
    final dot = value.indexOf('.');
    final head = dot < 0 ? value : value.substring(0, dot);
    final int degDigits = isLat ? 2 : 3;
    if (head.length < degDigits) return null;
    final deg = int.tryParse(head.substring(0, degDigits));
    final minute = double.tryParse(value.substring(degDigits));
    if (deg == null || minute == null) return null;
    var d = deg + minute / 60.0;
    if (hemi == 'S' || hemi == 'W') d = -d;
    return d;
  }

  static double? _f(List<String> f, int i) {
    if (i >= f.length) return null;
    final s = f[i];
    if (s.isEmpty) return null;
    return double.tryParse(s);
  }

  static int? _intDigits(String? s) {
    if (s == null || s.isEmpty) return null;
    return int.tryParse(s);
  }

  /// 解析一行；成功返回具体 [NmeaRecord]，失败/坏校验和/中间 GSV 帧返回 null。
  ///
  /// [now] 可注入（测试 GSV 聚合超时用）；默认当前时间。
  NmeaRecord? parse(String line, {DateTime? now}) {
    final trimmed = line.trim();
    if (!trimmed.startsWith('\$')) return null;
    final star = trimmed.indexOf('*');
    if (star < 0) return null;
    final body = trimmed.substring(1, star);
    final checksumHex = trimmed.substring(star + 1).trim();
    final expected = int.tryParse(checksumHex, radix: 16);
    if (expected == null) return null;
    if (nmeaChecksum(body) != expected) return null;

    final fields = body.split(',');
    final head = fields[0];
    if (head.length < 5) return null;
    final talker = head.substring(0, 2);
    final stype = head.substring(2, 5);
    if (!kNmeaTalkers.contains(talker)) return null;

    switch (stype) {
      case 'GGA':
        return _gga(talker, fields);
      case 'RMC':
        return _rmc(talker, fields);
      case 'GSA':
        return _gsa(talker, fields);
      case 'GSV':
        return _gsv(talker, fields, now ?? DateTime.now());
      case 'VTG':
        return _vtg(talker, fields);
      case 'ZDA':
        return _zda(talker, fields);
      case 'GLL':
        return _gll(talker, fields);
      case 'GST':
        return _gst(talker, fields);
      case 'TXT':
        return TxtRecord(
            talker: talker,
            text: fields.length > 1 ? fields.sublist(1).join(',') : '');
    }
    return null;
  }

  GgaRecord _gga(String talker, List<String> f) {
    return GgaRecord(
      talker: talker,
      utcTime: f.length > 1 ? f[1] : '',
      latitude: f.length > 3 ? _ddmmToDeg(f[2], f[3], true) : null,
      longitude: f.length > 5 ? _ddmmToDeg(f[4], f[5], false) : null,
      fixQuality: f.length > 6 ? (_intDigits(f[6]) ?? 0) : 0,
      satellites: f.length > 7 ? (_intDigits(f[7]) ?? 0) : 0,
      hdop: _f(f, 8),
      altitudeM: _f(f, 9),
    );
  }

  RmcRecord _rmc(String talker, List<String> f) {
    final status = f.length > 2 ? f[2] : 'V';
    final knots = _f(f, 7);
    return RmcRecord(
      talker: talker,
      utcTime: f.length > 1 ? f[1] : '',
      status: status,
      valid: status == 'A',
      latitude: f.length > 4 ? _ddmmToDeg(f[3], f[4], true) : null,
      longitude: f.length > 6 ? _ddmmToDeg(f[5], f[6], false) : null,
      speedKnots: knots,
      speedKmh: (knots ?? 0.0) * 1.852,
      courseDeg: _f(f, 8),
      date: f.length > 9 ? f[9] : '',
    );
  }

  GsaRecord _gsa(String talker, List<String> f) {
    final used = <int>[];
    for (var i = 3; i < 15 && i < f.length; i++) {
      final v = _intDigits(f[i]);
      if (v != null) used.add(v);
    }
    return GsaRecord(
      talker: talker,
      mode: f.length > 1 ? f[1] : '',
      fixType: f.length > 2 ? (_intDigits(f[2]) ?? 1) : 1,
      satellitesUsed: used,
      pdop: _f(f, 15),
      hdop: _f(f, 16),
      vdop: _f(f, 17),
    );
  }

  GsvRecord? _gsv(String talker, List<String> f, DateTime now) {
    final sats = <GsvSat>[];
    var i = 4;
    while (i + 3 < f.length) {
      final sid = f[i];
      if (sid.isNotEmpty) {
        sats.add(GsvSat(
          id: int.tryParse(sid) ?? sid,
          elevation: _f(f, i + 1),
          azimuth: _f(f, i + 2),
          snrDb: _f(f, i + 3),
        ));
      }
      i += 4;
    }
    final total = f.length > 1 ? (_intDigits(f[1]) ?? 0) : 0;
    final num = f.length > 2 ? (_intDigits(f[2]) ?? 0) : 0;
    final frame = GsvRecord(
      talker: talker,
      totalMessages: total,
      messageNumber: num,
      satellitesInView: f.length > 3 ? (_intDigits(f[3]) ?? 0) : 0,
      sats: sats,
      aggregated: false,
    );

    _sweepStale(now);

    if (total <= 1) return frame;

    final key = '$talker:$total';
    if (num <= 1) {
      _gsvBuf[key] = _GsvBuffer(ts: now)..frames.add(frame);
    } else {
      final entry = _gsvBuf.putIfAbsent(key, () => _GsvBuffer(ts: now));
      entry.frames.add(frame);
    }
    if (num >= total) {
      final buffered = _gsvBuf.remove(key)?.frames ?? [frame];
      final all = <GsvSat>[];
      for (final fr in buffered) {
        all.addAll(fr.sats);
      }
      return GsvRecord(
        talker: talker,
        totalMessages: total,
        messageNumber: num,
        satellitesInView: frame.satellitesInView,
        sats: all,
        aggregated: true,
      );
    }
    return null;
  }

  void _sweepStale(DateTime now) {
    final stale = <String>[];
    _gsvBuf.forEach((k, v) {
      if (now.difference(v.ts) > _gsvAggTimeout) stale.add(k);
    });
    for (final k in stale) {
      _gsvBuf.remove(k);
    }
  }

  VtgRecord _vtg(String talker, List<String> f) {
    return VtgRecord(
      talker: talker,
      courseDeg: _f(f, 1),
      speedKnots: _f(f, 5),
      speedKmh: _f(f, 7),
    );
  }

  ZdaRecord _zda(String talker, List<String> f) {
    DateTime? dt;
    try {
      if (f.length > 4 &&
          f[1].isNotEmpty &&
          f[2].isNotEmpty &&
          f[3].isNotEmpty &&
          f[4].isNotEmpty) {
        final hh = int.parse(f[1].substring(0, 2));
        final mm = int.parse(f[1].substring(2, 4));
        final ss = double.parse(f[1].substring(4)).round();
        dt = DateTime.utc(
            int.parse(f[4]), int.parse(f[3]), int.parse(f[2]), hh, mm, ss);
      }
    } catch (_) {
      dt = null;
    }
    return ZdaRecord(
        talker: talker,
        dateTimeUtc: dt,
        rawTime: f.length > 1 ? f[1] : '');
  }

  GllRecord _gll(String talker, List<String> f) {
    final status = f.length > 6 ? f[6] : 'V';
    return GllRecord(
      talker: talker,
      latitude: f.length > 2 ? _ddmmToDeg(f[1], f[2], true) : null,
      longitude: f.length > 4 ? _ddmmToDeg(f[3], f[4], false) : null,
      utcTime: f.length > 5 ? f[5] : '',
      valid: status == 'A',
      status: status,
    );
  }

  GstRecord _gst(String talker, List<String> f) {
    return GstRecord(
      talker: talker,
      utcTime: f.length > 1 ? f[1] : '',
      rmsStd: _f(f, 2),
      majorSigma: _f(f, 3),
      minorSigma: _f(f, 4),
      orient: _f(f, 5),
      latSigma: _f(f, 6),
      lonSigma: _f(f, 7),
      altSigma: _f(f, 8),
    );
  }
}
