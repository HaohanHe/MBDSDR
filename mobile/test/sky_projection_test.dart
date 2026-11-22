/// 透视投影与段裁剪对拍。期望值来自 `sky_projection_golden.json`
/// （由 `scripts/build_sky_projection_golden.py` 用桌面 `sky_to_screen` 算出）。
///
/// 验证记录（2026-11-18）
/// ----------
/// 公式正确性由 `tests/test_mobile_sky_algorithms.py` 在 Python 侧
/// 复现同一套算式并与桌面实现对拍。本文件已在 Flutter 3.50.0 / Dart 3.14.0
/// 下实际执行：`flutter test test/sky_projection_test.dart` -> 20 passed
/// （含 84 点投影对拍，最大像素偏差 0.0000）。
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/sky_projection.dart';

SkyView _viewFrom(Map<String, dynamic> v) => SkyView(
      centerAzDeg: (v['center_az_deg'] as num).toDouble(),
      centerAltDeg: (v['center_alt_deg'] as num).toDouble(),
      fovDeg: (v['fov_deg'] as num).toDouble(),
      width: (v['width'] as num).toDouble(),
      height: (v['height'] as num).toDouble(),
      rotationDeg: (v['rotation_deg'] as num).toDouble(),
    );

void main() {
  late Map<String, dynamic> golden;

  setUpAll(() {
    golden = jsonDecode(
            File('test/fixtures/sky_projection_golden.json').readAsStringSync())
        as Map<String, dynamic>;
  });

  group('像素缩放', () {
    test('fov=60, 短边 1080 -> ppr = 540/tan(30°)', () {
      const v = SkyView(
          centerAzDeg: 0, centerAltDeg: 45, fovDeg: 60, width: 1080, height: 1920);
      expect(v.minDim, 1080);
      expect(v.pixelPerRad, closeTo(540.0 / math.tan(math.pi / 6), 1e-9));
    });

    test('wh 互换时 ppr 不变（按短边度量）', () {
      const a = SkyView(
          centerAzDeg: 0, centerAltDeg: 45, fovDeg: 45, width: 1080, height: 1920);
      const b = SkyView(
          centerAzDeg: 0, centerAltDeg: 45, fovDeg: 45, width: 1920, height: 1080);
      expect(a.pixelPerRad, closeTo(b.pixelPerRad, 1e-9));
    });
  });

  group('基本几何', () {
    test('视中心投影到画布正中', () {
      const v = SkyView(
          centerAzDeg: 180, centerAltDeg: 45, fovDeg: 60, width: 1080, height: 1920);
      final p = projectAzAlt(180.0, 45.0, v)!;
      expect(p.x, closeTo(540, 1e-6));
      expect(p.y, closeTo(960, 1e-6));
    });

    test('视中心正后方（背对相机）返回 null', () {
      const v = SkyView(
          centerAzDeg: 180, centerAltDeg: 45, fovDeg: 60, width: 800, height: 800);
      expect(projectAzAlt(0.0, -45.0, v), isNull);
    });

    test('抬头：alt 增大 -> y 减小（屏幕 y 向下）', () {
      const v = SkyView(
          centerAzDeg: 180, centerAltDeg: 45, fovDeg: 60, width: 800, height: 800);
      final up = projectAzAlt(180.0, 65.0, v)!;
      final dn = projectAzAlt(180.0, 25.0, v)!;
      expect(up.y, lessThan(dn.y));
    });

    test('东转：az 增大 -> x 增大（东在右）', () {
      const v = SkyView(
          centerAzDeg: 180, centerAltDeg: 45, fovDeg: 60, width: 800, height: 800);
      final right = projectAzAlt(200.0, 45.0, v)!;
      final left = projectAzAlt(160.0, 45.0, v)!;
      expect(right.x, greaterThan(left.x));
    });
  });

  group('与桌面真值对拍（黄金向量）', () {
    test('全部 15 组 / 105 点：可见性一致、像素落在容差内', () {
      final tolPx = (golden['meta']['tolerance_px'] as num).toDouble();
      final cases = (golden['cases'] as List).cast<Map<String, dynamic>>();
      var worstPx = 0.0;
      var checked = 0;

      for (final c in cases) {
        final view = _viewFrom(c['view'] as Map<String, dynamic>);
        for (final p in (c['points'] as List).cast<Map<String, dynamic>>()) {
          final az = (p['az_deg'] as num).toDouble();
          final alt = (p['alt_deg'] as num).toDouble();
          final wantVisible = p['visible'] as bool;
          final got = projectAzAlt(az, alt, view);

          if (!wantVisible) {
            expect(got, isNull,
                reason: '${c['name']} az=$az alt=$alt 桌面判不可见，Dart 却给了坐标');
            continue;
          }
          expect(got, isNotNull,
              reason: '${c['name']} az=$az alt=$alt 桌面可见，Dart 却返回 null');
          final wantX = (p['px'] as num).toDouble();
          final wantY = (p['py'] as num).toDouble();
          final dx = (got!.x - wantX).abs();
          final dy = (got.y - wantY).abs();
          worstPx = math.max(worstPx, math.max(dx, dy));
          checked++;
          expect(dx, lessThan(tolPx),
              reason: '${c['name']} az=$az alt=$alt x 超差：'
                  '得到 ${got.x} 期望 $wantX');
          expect(dy, lessThan(tolPx),
              reason: '${c['name']} az=$az alt=$alt y 超差：'
                  '得到 ${got.y} 期望 $wantY');
        }
      }
      expect(checked, greaterThan(80));
      // ignore: avoid_print
      print('投影对拍：$checked 点通过，最大像素偏差 ${worstPx.toStringAsFixed(4)}');
    });
  });

  group('反投影往返', () {
    test('投影 -> 反投影 回到原 az/alt', () {
      const v = SkyView(
          centerAzDeg: 210, centerAltDeg: 35, fovDeg: 70, width: 1080, height: 1920);
      const samples = [
        [210.0, 35.0],
        [230.0, 45.0],
        [190.0, 20.0],
        [215.0, 55.0],
      ];
      for (final s in samples) {
        final p = projectAzAlt(s[0], s[1], v)!;
        final back = unprojectToAzAlt(p.x, p.y, v)!;
        final daz = ((back.azDeg - s[0] + 540.0) % 360.0) - 180.0;
        expect(daz.abs(), lessThan(1e-6),
            reason: 'az 往返失败 @${s[0]}');
        expect((back.altDeg - s[1]).abs(), lessThan(1e-6),
            reason: 'alt 往返失败 @${s[1]}');
      }
    });

    test('带旋转时往返仍成立', () {
      const v = SkyView(
          centerAzDeg: 90,
          centerAltDeg: 40,
          fovDeg: 50,
          width: 800,
          height: 800,
          rotationDeg: 33.0);
      final p = projectAzAlt(100.0, 50.0, v)!;
      final back = unprojectToAzAlt(p.x, p.y, v)!;
      final daz = ((back.azDeg - 100.0 + 540.0) % 360.0) - 180.0;
      expect(daz.abs(), lessThan(1e-6));
      expect((back.altDeg - 50.0).abs(), lessThan(1e-6));
    });
  });

  group('段裁剪（Liang–Barsky 四态）', () {
    test('两端都在内：原样返回', () {
      final r = clipSegmentToRect(
          const ScreenPoint(100, 100), const ScreenPoint(200, 200), 400, 400)!;
      expect(r.a.x, closeTo(100, 1e-9));
      expect(r.b.y, closeTo(200, 1e-9));
    });

    test('一端出界：裁到边界，另一端不动', () {
      final r = clipSegmentToRect(
          const ScreenPoint(200, 200), const ScreenPoint(900, 200), 400, 400)!;
      expect(r.a.x, closeTo(200, 1e-9));
      expect(r.b.x, closeTo(400, 1e-9));
      expect(r.b.y, closeTo(200, 1e-9));
    });

    test('两端都出界但穿过画面：必须保留（这条是"装不下也要画到边界"的根）',
        () {
      final r = clipSegmentToRect(
          const ScreenPoint(-500, 200), const ScreenPoint(900, 200), 400, 400);
      expect(r, isNotNull, reason: '穿过画面的段被整条丢弃 = 用户看不到延伸方向');
      expect(r!.a.x, closeTo(0, 1e-9));
      expect(r.b.x, closeTo(400, 1e-9));
    });

    test('完全在外：返回 null', () {
      expect(
          clipSegmentToRect(
              const ScreenPoint(-100, -100), const ScreenPoint(-50, -50), 400, 400),
          isNull);
    });

    test('非有限坐标：返回 null（不是"在很远处"）', () {
      expect(
          clipSegmentToRect(const ScreenPoint(double.infinity, 0),
              const ScreenPoint(10, 10), 400, 400),
          isNull);
    });

    test('水平线（dy=0）不除零', () {
      final r = clipSegmentToRect(
          const ScreenPoint(-10, 50), const ScreenPoint(500, 50), 400, 400);
      expect(r, isNotNull);
      expect(r!.a.x, closeTo(0, 1e-9));
      expect(r.b.x, closeTo(400, 1e-9));
    });
  });

  group('画布判定', () {
    test('边界含余量', () {
      const p = ScreenPoint(405, 200);
      expect(isOnCanvas(p, 400, 400), isFalse);
      expect(isOnCanvas(p, 400, 400, margin: 10), isTrue);
    });
  });

  group('投影有效性域与跨域裁剪（Task 10.2）', () {
    const v = SkyView(
        centerAzDeg: 0, centerAltDeg: 0, fovDeg: 60, width: 1080, height: 1920);

    test('相机前方可画，背面/地平线不可画', () {
      expect(isDirectionDrawable(0, 0, v), isTrue);
      expect(isDirectionDrawable(80, 0, v), isTrue);
      expect(isDirectionDrawable(90, 0, v), isFalse); // 离轴 90° = 透视地平线
      expect(isDirectionDrawable(180, 0, v), isFalse);
    });

    test('一端在相机背后：裁到边界返回 2 点，不再整段丢弃', () {
      final pts = clipSegmentToSkyDomain(0, 0, 180, 0, v);
      expect(pts.length, 2);
      expect(pts[0].x, closeTo(v.centerX, 1e-6));
      expect(pts[1].x.isFinite, isTrue);
      // 边界在离轴 90°，落屏远在画布外（由画布裁剪收边）。对跖点的大圆
      // 不唯一，边界可能落在横向或纵向，任一轴出画即可。
      final dx = (pts[1].x - v.centerX).abs();
      final dy = (pts[1].y - v.centerY).abs();
      expect(math.max(dx, dy), greaterThan(v.minDim));
    });

    test('两端都不可画 -> 空；两端都可画 -> 原样两点', () {
      expect(clipSegmentToSkyDomain(100, 0, 180, 0, v), isEmpty);
      final both = clipSegmentToSkyDomain(0, 0, 10, 0, v);
      expect(both.length, 2);
      expect(both[0].x, closeTo(v.centerX, 1e-6));
    });

    test('drawable 回调可叠额外域（地平余量），边界按回调走', () {
      // 赤道上 az 单调递增；把域限成 az <= 10°，边界应落在 10°。
      final pts = clipSegmentToSkyDomain(
        0,
        0,
        30,
        0,
        v,
        drawable: (double az, double alt) => az <= 10.0 + 1e-9,
        iterations: 40,
      );
      expect(pts.length, 2);
      final boundary = unprojectToAzAlt(pts[1].x, pts[1].y, v);
      expect(boundary, isNotNull);
      expect(boundary!.altDeg, closeTo(0.0, 1e-6));
      expect(boundary.azDeg, closeTo(10.0, 1e-6));
    });
  });
}
