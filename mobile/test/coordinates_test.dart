import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
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
}
