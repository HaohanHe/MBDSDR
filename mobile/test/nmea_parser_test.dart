// NMEA 解析器离线测试：校验和动态生成（不手写），覆盖 GGA/RMC/GSA/GSV/VTG/ZDA/GLL/GST/TXT、
// 坏校验和、no-fix、多星座 talker、GSV 多帧聚合与超时。移植自 serial_gnss.py 自测。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/gnss/nmea_parser.dart';

/// 用动态校验和拼一条合法语句：`$<body>*HH`。
String mk(String body) => '\$$body*${nmeaChecksum(body).toRadixString(16).padLeft(2, '0').toUpperCase()}';

void main() {
  group('nmeaChecksum', () {
    test('逐字符 XOR', () {
      // 已知 NMEA 标准样例："GPGLL,...." 的校验和可独立核对——这里只测 XOR 性质。
      expect(nmeaChecksum('GNGGA'), isA<int>());
      // 空体 = 0。
      expect(nmeaChecksum(''), 0);
      // 自反：mk 出来的语句一定能通过 parse 校验。
      final r = NmeaParser().parse(mk('GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,'));
      expect(r, isNotNull);
    });
  });

  group('GGA', () {
    test('经纬度换算/卫星数/高度/fixQuality', () {
      final r = NmeaParser().parse(mk(
          'GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,'));
      expect(r, isA<GgaRecord>());
      final g = r as GgaRecord;
      expect(g.latitude, closeTo(43 + 52 / 60.0, 1e-4));
      expect(g.longitude, closeTo(125 + 19 / 60.0, 1e-4));
      expect(g.satellites, 9);
      expect(g.altitudeM, closeTo(150.0, 1e-6));
      expect(g.fixQuality, 1);
      expect(g.utcTime, '072545.00');
    });

    test('fixQuality=0（no-fix）仍解析出语句，坐标字段存在但 fixQuality=0', () {
      final r = NmeaParser().parse(mk('GNGGA,072545.00,,,,,0,00,99.0,,,,'));
      expect(r, isA<GgaRecord>());
      expect((r as GgaRecord).fixQuality, 0);
      expect(r.latitude, isNull);
    });
  });

  group('坏输入一律返回 null', () {
    final p = NmeaParser();
    test('坏校验和 / 无* / 非\$开头 / hex非法', () {
      final good = mk('GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,,,');
      // 改最后两位校验和。
      expect(p.parse('${good.substring(0, good.length - 2)}00'), isNull);
      expect(p.parse('GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,,,'), isNull); // 无*
      expect(p.parse('hello world'), isNull); // 非$
      expect(p.parse('\$GNGGA,....,XX'), isNull); // *后非hex
    });
  });

  group('RMC', () {
    test('南纬/西经取负、speedKmh=knots*1.852、status=V→valid=false', () {
      final r = NmeaParser().parse(
          mk('GNRMC,072545.00,A,3351.1111,S,11820.2222,W,10.5,45.0,010126,,A'));
      expect(r, isA<RmcRecord>());
      final m = r as RmcRecord;
      expect(m.latitude, isNegative);
      expect(m.longitude, isNegative);
      expect(m.speedKmh, closeTo(10.5 * 1.852, 1e-3));
      expect(m.valid, isTrue);

      final v = NmeaParser().parse(mk('GNRMC,072545.00,V,3351.1111,S,11820.2222,W,0,0,010126,,A'));
      expect((v as RmcRecord).valid, isFalse);
    });
  });

  group('GSA / VTG / ZDA', () {
    test('GSA fixType/satellitesUsed/pdop-hdop-vdop', () {
      final r = NmeaParser().parse(mk('GNGSA,A,3,01,02,03,,,,,,,,,,1.0,0.8,0.9'));
      expect(r, isA<GsaRecord>());
      final g = r as GsaRecord;
      expect(g.fixType, 3);
      expect(g.satellitesUsed, [1, 2, 3]);
      expect(g.pdop, closeTo(1.0, 1e-6));
      expect(g.hdop, closeTo(0.8, 1e-6));
      expect(g.vdop, closeTo(0.9, 1e-6));
    });
    test('VTG course/speed', () {
      final r = NmeaParser().parse(mk('GNVTG,45.0,T,,M,10.5,N,19.4,K'));
      final v = r as VtgRecord;
      expect(v.courseDeg, closeTo(45.0, 1e-6));
      expect(v.speedKnots, closeTo(10.5, 1e-6));
    });
    test('ZDA 拼出 UTC DateTime', () {
      final r = NmeaParser().parse(mk('GNZDA,072545.00,24,09,2026,,'));
      final z = r as ZdaRecord;
      expect(z.dateTimeUtc, isNotNull);
      expect(z.dateTimeUtc!.year, 2026);
      expect(z.dateTimeUtc!.month, 9);
      expect(z.dateTimeUtc!.day, 24);
      expect(z.dateTimeUtc!.hour, 7);
      expect(z.dateTimeUtc!.minute, 25);
    });
  });

  group('GLL / GST / TXT', () {
    test('GLL 经纬度/状态', () {
      final r = NmeaParser().parse(mk('GNGLL,4352.0000,N,12519.0000,E,072545.00,A'));
      final g = r as GllRecord;
      expect(g.latitude, closeTo(43 + 52 / 60.0, 1e-4));
      expect(g.valid, isTrue);
      final v = NmeaParser().parse(mk('GNGLL,4352.0000,N,12519.0000,E,072545.00,V'));
      expect((v as GllRecord).valid, isFalse);
    });
    test('GST 各 sigma；空字段为 null', () {
      final r = NmeaParser().parse(mk('GNGST,072545.00,1.0,2.0,1.0,30.0,0.8,0.6,5.0'));
      final g = r as GstRecord;
      expect(g.rmsStd, 1.0);
      expect(g.altSigma, 5.0);
      final e = NmeaParser().parse(mk('GNGST,072545.00,,,,,,,')) as GstRecord;
      expect(e.majorSigma, isNull);
    });
    test('TXT 文本拼接', () {
      final r = NmeaParser().parse(mk('BDTXT,01,01,01,HW U-BLOX 8 READY'));
      expect(r, isA<TxtRecord>());
      expect((r as TxtRecord).text, contains('U-BLOX'));
    });
  });

  group('GSV 多帧聚合', () {
    test('中间帧 null、末帧聚合、单帧直接返回', () {
      final p = NmeaParser();
      final f1 = p.parse(mk('GNGSV,3,1,11,01,88,045,42,02,45,120,38'));
      final f2 = p.parse(mk('GNGSV,3,2,11,03,40,200,30'));
      final f3 = p.parse(mk('GNGSV,3,3,11,04,30,300,25'));
      expect(f1, isNull);
      expect(f2, isNull);
      expect(f3, isA<GsvRecord>());
      final agg = f3 as GsvRecord;
      expect(agg.aggregated, isTrue);
      expect(agg.sats.length, 4);
      expect(agg.sats.any((s) => s.id == 4), isTrue);

      final single = p.parse(mk('GNGSV,1,1,4,01,80,040,40')) as GsvRecord;
      expect(single.aggregated, isFalse);
      expect(single.sats.length, 1);
    });

    test('按 talker 分组：北斗 GBGSV PRN>32 正常解析', () {
      final p = NmeaParser();
      final b1 = p.parse(mk('GBGSV,2,1,6,211,80,040,40,212,50,100,35'));
      expect(b1, isNull);
      final b2 = p.parse(mk('GBGSV,2,2,6,213,30,200,20')) as GsvRecord;
      expect(b2.aggregated, isTrue);
      expect(b2.sats.length, 3);
      expect(b2.sats.any((s) => s.id == 211), isTrue);
    });

    test('超时丢弃半截缓冲（注入时钟）', () {
      final p = NmeaParser();
      final t0 = DateTime.utc(2026, 10, 1);
      p.parse(mk('GPGSV,3,1,9,11,80,040,40'), now: t0);
      final mid = p.parse(mk('GPGSV,3,2,9,12,40,100,30'), now: t0);
      expect(mid, isNull);
      // 2.5s 后才来末帧 → 半截缓冲已被丢弃，末帧单独成帧只剩 1 颗。
      final late = p.parse(mk('GPGSV,3,3,9,13,20,200,25'),
          now: t0.add(const Duration(milliseconds: 2500))) as GsvRecord;
      expect(late.aggregated, isTrue);
      expect(late.sats.length, 1);
      expect(late.sats.single.id, 13);
    });
  });

  group('多星座 talker', () {
    test('GP/GL/GA/GB/BD/GN 全收；未知 talker 拒', () {
      final p = NmeaParser();
      for (final t in ['GP', 'GL', 'GA', 'GB', 'BD', 'GN']) {
        expect(p.parse(mk('${t}GGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,,')),
            isNotNull, reason: 'talker $t 应被接受');
      }
      // 未知 talker XX。
      expect(p.parse(mk('XXGGA,072545.00,,,,,1,9,0.9,,,')), isNull);
    });
  });
}
