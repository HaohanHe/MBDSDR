import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// 坐标链几何自检（无需网络/传播）。
void main() {
  group('WGS84 大地 -> ECEF', () {
    test('赤道/本初子午线：x≈地球半径，y≈0，z≈0', () {
      const st = Station(lat: 0, lon: 0);
      final r = ecefFromGeodetic(st);
      expect(r.x, closeTo(6378.137, 0.5));
      expect(r.y, closeTo(0, 1e-6));
      expect(r.z, closeTo(0, 1e-6));
    });

    test('北极：z≈地球半径，x,y≈0', () {
      const st = Station(lat: 90, lon: 0);
      final r = ecefFromGeodetic(st);
      expect(r.z, closeTo(6356.752, 0.5)); // 极半径
      expect(r.x.abs(), lessThan(1e-3));
      expect(r.y.abs(), lessThan(1e-3));
    });
  });

  group('ECEF -> 站心 ENU', () {
    // 测站放在赤道本初子午线上，简化 ENU 方向：东=+y，北=+z，上=+x。
    const st = Station(lat: 0, lon: 0);
    final statEcef = ecefFromGeodetic(st);

    test('正上方 1000 km：el≈90°', () {
      final satEcef = Vec3(statEcef.x + 1000.0, 0.0, 0.0);
      final a = ecefToAzEl(satEcef, statEcef, st);
      expect(a.el, closeTo(90.0, 1e-6));
      expect(a.range, closeTo(1000.0, 1e-6));
    });

    test('正东方 1000 km：az≈90°、el≈0°', () {
      final satEcef = Vec3(statEcef.x, 1000.0, 0.0);
      final a = ecefToAzEl(satEcef, statEcef, st);
      expect(a.az, closeTo(90.0, 1e-6));
      expect(a.el, closeTo(0.0, 1e-6));
    });

    test('正北方 1000 km：az≈0°、el≈0°', () {
      final satEcef = Vec3(statEcef.x, 0.0, 1000.0);
      final a = ecefToAzEl(satEcef, statEcef, st);
      expect(a.az, closeTo(0.0, 1e-6));
      expect(a.el, closeTo(0.0, 1e-6));
    });

    test('斜上方 45°：range≈√2·1000', () {
      final satEcef = Vec3(statEcef.x + 1000.0, 0.0, 1000.0);
      final a = ecefToAzEl(satEcef, statEcef, st);
      expect(a.el, closeTo(45.0, 1e-4));
      expect(a.range, closeTo(1000.0 * math.sqrt2, 1e-6));
    });
  });

  group('TEME -> ECEF 旋转', () {
    test('gst=0 时为恒等映射', () {
      const t = Vec3(1000.0, 2000.0, 3000.0);
      final r = temeToEcef(t, 0.0);
      expect(r.x, closeTo(1000.0, 1e-9));
      expect(r.y, closeTo(2000.0, 1e-9));
      expect(r.z, closeTo(3000.0, 1e-9));
    });

    test('gst=π/2 时 90° 旋转', () {
      const t = Vec3(1000.0, 0.0, 500.0);
      final r = temeToEcef(t, math.pi / 2);
      // Rz(-π/2): (x,y) -> (y, -x)
      expect(r.x, closeTo(0.0, 1e-9));
      expect(r.y, closeTo(-1000.0, 1e-9));
      expect(r.z, closeTo(500.0, 1e-9));
    });
  });

  group('多普勒频移（G6 显值）', () {
    test('纯函数：远离(vr>0)→负、靠近(vr<0)→正；量级 = -f0·v_r/c', () {
      final double d =
          dopplerShiftFromRangeRateHz(downlinkHz: 137.1e6, rangeRateKmS: 7.5);
      expect(d, closeTo(-137.1e6 * 7.5 / kSpeedOfLightKmS, 1e-6));
      expect(d, isNegative, reason: '卫星远离 → 接收载频偏低 → 多普勒为负');

      expect(
        dopplerShiftFromRangeRateHz(downlinkHz: 137.1e6, rangeRateKmS: -7.5),
        isPositive,
        reason: '卫星靠近 → 接收载频偏高 → 多普勒为正',
      );
    });

    test('SGP4 已知 TLE：range-rate 有限、多普勒量级合理且符号自洽', () {
      // ISS TLE（与 satellite_capture_test 同一组已知根数）。
      const l1 =
          '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
      const l2 =
          '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
      final iss = Tle.fromLines(l1, l2);
      const station = Station(lat: 40, lon: -100);
      final t = iss.epoch.add(const Duration(minutes: 30));

      final double vr = rangeRateAt(Sgp4(iss), t, station);
      expect(vr.isFinite, isTrue);
      expect(vr.abs(), lessThan(9.0),
          reason: 'LEO 径向速度量级应在 ~7.5 km/s 内，实得 $vr km/s');

      final double dop =
          dopplerShiftFromRangeRateHz(downlinkHz: 137.1e6, rangeRateKmS: vr);
      expect(dop.isFinite, isTrue);
      // 137 MHz × ~7.5 km/s / c ≈ 数 kHz 量级；放宽到 50 kHz 以内。
      expect(dop.abs(), lessThan(50e3), reason: '多普勒量级应在几十 kHz 内，实得 $dop Hz');

      // 符号约定：vr>0(远离) → dop<0；vr<0(靠近) → dop>0。
      if (vr > 0) {
        expect(dop, isNegative);
      } else if (vr < 0) {
        expect(dop, isPositive);
      }
    });
  });
}
