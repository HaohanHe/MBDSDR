/// 恒星坐标链对拍：本文件的输入/期望值全部来自 `sky_transform_golden.json`，
/// 而该文件由 `scripts/build_sky_projection_golden.py` 用**桌面唯一真值**
/// （`mbdsdr_ai.celestial_geometry.j2000_to_altaz`）算出。
///
/// 也就是说：这里断言的不是"我觉得对"，是"与桌面一致"。
///
/// 未验证声明
/// ----------
/// **本机无 Flutter 工具链**（`docs/parameter-unification.md:453`），
/// 本文件从未被 `flutter test` 执行过。算法本身的正确性另由
/// `tests/test_mobile_sky_algorithms.py` 在 Python 侧复现同一套公式并与桌面
/// 对拍来保证；本文件是"有工具链的机器上的一次性验真"。
///
/// 在有 Flutter 的机器上跑：
///     flutter test test/sky_transform_test.dart
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/sky_transform.dart';

void main() {
  late Map<String, dynamic> golden;

  setUpAll(() {
    final file = File('test/fixtures/sky_transform_golden.json');
    golden = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
  });

  group('GMST（Capitaine 2003，与桌面同式）', () {
    test('J2000 时刻 GMST ≈ 280.4606°', () {
      // J2000.0 的 GMST 是天文常数级事实，不依赖任何实现。
      expect(gmstDegCapitaine(j2000Jd), closeTo(280.4606, 0.01));
    });

    test('一天推进约 360.9856°（恒星日比太阳日长）', () {
      final g0 = gmstDegCapitaine(2461355.25);
      final g1 = gmstDegCapitaine(2461356.25);
      final delta = ((g1 - g0) % 360.0 + 360.0) % 360.0;
      expect(delta, closeTo(0.9856, 0.01));
    });

    test('归一化：任意输入都落在 [0, 360)', () {
      for (final jd in [2451545.0, 2460000.5, 2400000.0, 2500000.0]) {
        final g = gmstDegCapitaine(jd);
        expect(g, greaterThanOrEqualTo(0));
        expect(g, lessThan(360));
      }
    });
  });

  group('岁差往返', () {
    test('J2000 -> date -> J2000 回到原值', () {
      const cases = [
        [101.28722, -16.716116],
        [279.234734, 38.783689],
        [0.0, 0.0],
        [200.0, -60.0],
      ];
      for (final c in cases) {
        for (final jd in [2451545.0, 2461355.25, 2480000.0]) {
          final ofDate = precessJ2000ToDate(c[0], c[1], jd);
          final back =
              precessDateToJ2000(ofDate.raDeg, ofDate.decDeg, jd);
          // ra 在 0/360 边界附近要按环距比较，不能直接相减。
          final dra = ((back.raDeg - c[0] + 540.0) % 360.0) - 180.0;
          expect(dra.abs(), lessThan(1e-6),
              reason: 'ra 往返失败 @jd=$jd');
          expect((back.decDeg - c[1]).abs(), lessThan(1e-6),
              reason: 'dec 往返失败 @jd=$jd');
        }
      }
    });

    test('J2000 时刻岁差矩阵应近似恒等（角度全为 0 附近）', () {
      final a = precessionAnglesArcsec(j2000Jd);
      // IAU 2006 在 T=0 时 ζ_A 与 z_A 取 ±2.650545″（偏置项），θ_A = 0。
      expect(a.zetaA, closeTo(2.650545, 1e-6));
      expect(a.zA, closeTo(-2.650545, 1e-6));
      expect(a.thetaA, closeTo(0.0, 1e-6));
    });
  });

  group('altAz 往返', () {
    test('地平 -> 当日赤道 -> 地平 回到原值', () {
      const lat = 45.0;
      const lon = 120.0;
      const jd = 2461355.25;
      const cases = [
        [180.0, 45.0],
        [0.0, 10.0],
        [270.0, 80.0],
        [45.0, -30.0],
      ];
      for (final c in cases) {
        final eq = dateFromAltAz(
            azDeg: c[0], altDeg: c[1], latDeg: lat, lonDeg: lon, jdUt1: jd);
        final back = altAzFromDate(
            raDeg: eq.raDeg,
            decDeg: eq.decDeg,
            latDeg: lat,
            lonDeg: lon,
            jdUt1: jd);
        final daz = ((back.azDeg - c[0] + 540.0) % 360.0) - 180.0;
        expect(daz.abs(), lessThan(1e-6),
            reason: 'az 往返失败 @az=${c[0]}');
        expect((back.altDeg - c[1]).abs(), lessThan(1e-6),
            reason: 'alt 往返失败 @alt=${c[1]}');
      }
    });
  });

  group('与桌面真值对拍（黄金向量）', () {
    test('全部 72 条：az/alt 落在容差内', () {
      final tolerance = (golden['meta']['tolerance_deg'] as num).toDouble();
      final cases = (golden['cases'] as List).cast<Map<String, dynamic>>();
      expect(cases.length, greaterThan(0));

      var worstAz = 0.0;
      var worstAlt = 0.0;
      for (final c in cases) {
        final got = altAzFromJ2000(
          raDeg: (c['ra_deg'] as num).toDouble(),
          decDeg: (c['dec_deg'] as num).toDouble(),
          latDeg: (c['lat_deg'] as num).toDouble(),
          lonDeg: (c['lon_deg'] as num).toDouble(),
          jdUt1: (c['jd_ut1'] as num).toDouble(),
        );
        final wantAz = (c['az_deg'] as num).toDouble();
        final wantAlt = (c['alt_deg'] as num).toDouble();
        final daz = ((got.azDeg - wantAz + 540.0) % 360.0) - 180.0;
        final dalt = got.altDeg - wantAlt;
        worstAz = math.max(worstAz, daz.abs());
        worstAlt = math.max(worstAlt, dalt.abs());
        expect(daz.abs(), lessThan(tolerance),
            reason: 'az 超差 @${c['star']} site=${c['site']} jd=${c['jd_ut1']}: '
                '得到 ${got.azDeg} 期望 $wantAz（差 ${daz * 3600}″）');
        expect(dalt.abs(), lessThan(tolerance),
            reason: 'alt 超差 @${c['star']} site=${c['site']} jd=${c['jd_ut1']}: '
                '得到 ${got.altDeg} 期望 $wantAlt（差 ${dalt * 3600}″）');
      }
      // 把实测最坏偏差打出来：数字比"通过"更有信息量。
      // ignore: avoid_print
      print('最坏偏差：az ${(worstAz * 3600).toStringAsFixed(2)}″ / '
          'alt ${(worstAlt * 3600).toStringAsFixed(2)}″');
    });

    test('北极星高度应约等于纬度（45N -> alt≈45）', () {
      final cases = (golden['cases'] as List)
          .cast<Map<String, dynamic>>()
          .where((c) => c['star'] == 'polaris' && c['site'] == 'sample_45n_120e');
      for (final c in cases) {
        final got = altAzFromJ2000(
          raDeg: (c['ra_deg'] as num).toDouble(),
          decDeg: (c['dec_deg'] as num).toDouble(),
          latDeg: 45.0,
          lonDeg: 120.0,
          jdUt1: (c['jd_ut1'] as num).toDouble(),
        );
        // 北极星距真天极约 0.7°，所以 alt 在 44.3–45.7 之间。
        expect(got.altDeg, closeTo(45.0, 0.8));
      }
    });
  });

  group('儒略日', () {
    test('Unix 纪元 = JD 2440587.5', () {
      expect(julianDateFromUtc(DateTime.utc(1970)),
          closeTo(2440587.5, 1e-9));
    });

    test('J2000 = 2000-01-01T12:00:00Z', () {
      expect(julianDateFromUtc(DateTime.utc(2000, 1, 1, 12)),
          closeTo(2451545.0, 1e-9));
    });

    test('闰日不丢：2000-02-29 与 2000-03-01 相差 1 天', () {
      final a = julianDateFromUtc(DateTime.utc(2000, 2, 29));
      final b = julianDateFromUtc(DateTime.utc(2000, 3, 1));
      expect(b - a, closeTo(1.0, 1e-9));
    });
  });

  group('角距', () {
    test('同一点角距为 0', () {
      const p = AzAlt(azDeg: 123.0, altDeg: 45.0);
      expect(angularSeparationDeg(p, p), closeTo(0.0, 1e-9));
    });

    test('天顶到地平任一点 = 90° - 该点高度', () {
      const zen = AzAlt(azDeg: 0.0, altDeg: 90.0);
      const p = AzAlt(azDeg: 200.0, altDeg: 30.0);
      expect(angularSeparationDeg(zen, p), closeTo(60.0, 1e-6));
    });
  });
}
