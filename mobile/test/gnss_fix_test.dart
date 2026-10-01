// GnssFixMerger 诚实态测试：no-fix 不回 (0,0)；过期退化 none；real 定位保留。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/gnss/gnss_fix.dart';
import 'package:mbdsdr_mobile/gnss/nmea_parser.dart';

String mk(String body) =>
    '\$$body*${nmeaChecksum(body).toRadixString(16).padLeft(2, '0').toUpperCase()}';

void main() {
  group('GnssFixMerger 诚实 no-fix', () {
    test('无数据 source=none，坐标全 null', () {
      final m = GnssFixMerger(clock: () => DateTime.utc(2026));
      expect(m.getFix().source, 'none');
      expect(m.getFix().latitude, isNull);
    });

    test('GGA fixQuality=1 → real 且有坐标', () {
      final now = DateTime.utc(2026, 10, 1);
      final m = GnssFixMerger(clock: () => now);
      final r = NmeaParser().parse(mk(
          'GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,'))!;
      m.feed(r);
      final f = m.getFix();
      expect(f.source, 'real');
      expect(f.hasFix, isTrue);
      expect(f.latitude, closeTo(43 + 52 / 60.0, 1e-4));
      expect(f.fixQuality, 1);
    });

    test('GGA fixQuality=0 → source=real 但坐标 null（绝不返回 0,0）', () {
      final now = DateTime.utc(2026, 10, 1);
      final m = GnssFixMerger(clock: () => now);
      final r = NmeaParser()
          .parse(mk('GNGGA,072545.00,,,,,0,00,99.0,,,,'))!;
      m.feed(r);
      final f = m.getFix();
      expect(f.source, 'real');
      expect(f.hasFix, isFalse);
      expect(f.latitude, isNull);
      expect(f.longitude, isNull);
      expect(f.fixQuality, 0);
    });

    test('超过 10s 无新语句 → 退化 none（旧坐标不残留）', () {
      var now = DateTime.utc(2026, 10, 1);
      final m = GnssFixMerger(clock: () => now);
      final r = NmeaParser().parse(mk(
          'GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,,,,'))!;
      m.feed(r);
      expect(m.getFix().source, 'real');
      // 拨时钟 11s。
      now = now.add(const Duration(seconds: 11));
      expect(m.getFix().source, 'none');
      expect(m.getFix().latitude, isNull);
    });

    test('RMC status=V 不写坐标；GSA/GSV 按 talker 缓存', () {
      final now = DateTime.utc(2026, 10, 1);
      final m = GnssFixMerger(clock: () => now);
      final rmc = NmeaParser().parse(mk('GNRMC,072545.00,V,4352.00,N,12519.00,E,0,0,010126,,A'))!;
      m.feed(rmc);
      expect(m.getFix().latitude, isNull);

      final gsa = NmeaParser().parse(mk('GPGSA,A,3,01,02,,,,,,,,,,,1.0,0.8,0.9'))!;
      m.feed(gsa);
      expect(m.latestGsa['GP'], isNotNull);
      expect(m.latestGsa['GP']!.satellitesUsed, [1, 2]);
    });
  });
}
