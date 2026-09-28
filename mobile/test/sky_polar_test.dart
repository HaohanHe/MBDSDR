import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/widgets/compass_dial.dart';

/// 极坐标变换落点单测：给定方位角/仰角，验证落点在期望的圆半径/角度上。
///
/// 约定：圆心=天顶(el 90°)，外圆=地平线(el 0°)；方位角自北顺时针，北在上方。
void main() {
  const center = Offset(100, 100);
  const R = 100.0; // 外圆半径

  group('polarPoint 半径（仰角）', () {
    test('天顶 el=90° 落在圆心，与方位无关', () {
      for (final az in [0.0, 90.0, 180.0, 270.0, 330.0]) {
        final p = polarPoint(center, R, az, 90);
        expect(p.dx, moreOrLessEquals(center.dx));
        expect(p.dy, moreOrLessEquals(center.dy));
      }
    });

    test('地平线 el=0° 落在外圆上（半径=R）', () {
      for (final az in [0.0, 90.0, 180.0, 270.0]) {
        final p = polarPoint(center, R, az, 0);
        expect((p - center).distance, moreOrLessEquals(R, epsilon: 1e-9));
      }
    });

    test('el=45° 落在半径 R*0.5 的内圆上', () {
      final p = polarPoint(center, R, 0, 45);
      expect((p - center).distance, moreOrLessEquals(R * 0.5, epsilon: 1e-9));
    });

    test('el=60° 落在半径 R/3 的内圆上', () {
      final p = polarPoint(center, R, 90, 60);
      // frac = (90-60)/90 = 1/3
      expect((p - center).distance, moreOrLessEquals(R / 3, epsilon: 1e-9));
    });
  });

  group('polarPoint 角度（方位）', () {
    test('正北 az=0° 在正上方（el=45）', () {
      final p = polarPoint(center, R, 0, 45);
      expect(p.dx, moreOrLessEquals(center.dx));
      expect(p.dy, moreOrLessEquals(center.dy - R * 0.5));
    });

    test('正东 az=90° 在正右方（el=45）', () {
      final p = polarPoint(center, R, 90, 45);
      expect(p.dx, moreOrLessEquals(center.dx + R * 0.5));
      expect(p.dy, moreOrLessEquals(center.dy));
    });

    test('正南 az=180° 在正下方（el=45）', () {
      final p = polarPoint(center, R, 180, 45);
      expect(p.dx, moreOrLessEquals(center.dx));
      expect(p.dy, moreOrLessEquals(center.dy + R * 0.5));
    });

    test('正西 az=270° 在正左方（el=45）', () {
      final p = polarPoint(center, R, 270, 45);
      expect(p.dx, moreOrLessEquals(center.dx - R * 0.5));
      expect(p.dy, moreOrLessEquals(center.dy));
    });

    test('正北偏东 30° 在右上象限', () {
      final p = polarPoint(center, R, 30, 0);
      // sin30=0.5, -cos30=-0.866
      expect(p.dx, moreOrLessEquals(center.dx + R * 0.5, epsilon: 1e-9));
      expect(p.dy, moreOrLessEquals(center.dy - R * 0.8660254038, epsilon: 1e-6));
    });
  });

  group('归一与夹取', () {
    test('az=360 与 az=0 等价', () {
      final a = polarPoint(center, R, 0, 30);
      final b = polarPoint(center, R, 360, 30);
      expect((a - b).distance, moreOrLessEquals(0, epsilon: 1e-9));
    });

    test('负方位角归一（-90 == 270）', () {
      final a = polarPoint(center, R, 270, 30);
      final b = polarPoint(center, R, -90, 30);
      expect((a - b).distance, moreOrLessEquals(0, epsilon: 1e-9));
    });

    test('仰角夹取：el=120 按天顶处理', () {
      final p = polarPoint(center, R, 45, 120);
      expect((p - center).distance, moreOrLessEquals(0, epsilon: 1e-9));
    });
  });

  group('极坐标变换互逆', () {
    test('polarPoint → inverse 还原 az/el', () {
      for (final az in [0.0, 45.0, 120.0, 200.0, 300.0]) {
        for (final el in [10.0, 45.0, 80.0]) {
          final p = polarPoint(center, R, az, el);
          final back = polarPointInverse(p, center, R);
          expect(back.el, moreOrLessEquals(el, epsilon: 1e-6));
          // 方位角按圆周归一比较。
          var dAz = (back.az - az).abs() % 360.0;
          if (dAz > 180) dAz = 360 - dAz;
          expect(dAz, moreOrLessEquals(0, epsilon: 1e-6));
        }
      }
    });
  });
}
