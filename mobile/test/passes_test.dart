import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// 过境预测结构自洽性（近地 ISS）与 GEO 风格静止轨道稳定性。
void main() {
  // 国际空间站近地 TLE（与 sgp4_test 同一颗）。
  const issL1 =
      '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
  const issL2 =
      '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';

  group('ISS 过境预测结构自洽', () {
    test('未来 48h 返回结构合法的 Pass', () {
      final iss = Tle.fromLines(issL1, issL2);
      // 北美中部站点。
      const station = Station(lat: 40.0, lon: -100.0);
      final start = iss.epoch.add(const Duration(hours: 2));
      final passes = predictPasses(
        [iss],
        station,
        hours: 48,
        stepSeconds: 60,
        startTime: start,
      );

      expect(passes, isNotEmpty, reason: 'ISS 每天过境十余次');
      for (final p in passes) {
        expect(p.name, iss.name);
        expect(p.riseTime.isBefore(p.setTime), isTrue);
        expect(p.duration.inSeconds, greaterThan(0));
        // 最高仰角在升起与落下之间。
        expect(p.maxTime.isAfter(p.riseTime) ||
                p.maxTime.isAtSameMomentAs(p.riseTime),
            isTrue);
        expect(
            p.maxTime.isBefore(p.setTime) ||
                p.maxTime.isAtSameMomentAs(p.setTime),
            isTrue);
        // 仰角范围合理。
        expect(p.maxEl, inInclusiveRange(0.0, 90.0));
        // 最高仰角不小于升起（升起处 el≈0）。
        expect(p.maxEl, greaterThanOrEqualTo(0.0));
        // 方位角落在 [0,360)。
        expect(p.riseAz, inInclusiveRange(0.0, 360.0));
        expect(p.setAz, inInclusiveRange(0.0, 360.0));
      }
    });

    test('visibleAt 返回可见性几何字段', () {
      final iss = Tle.fromLines(issL1, issL2);
      const station = Station(lat: 0.0, lon: 0.0);
      final t = iss.epoch.add(const Duration(minutes: 30));
      final vis = visibleAt(t, [iss], station);
      expect(vis, hasLength(1));
      final v = vis.single;
      expect(v.az, inInclusiveRange(0.0, 360.0));
      expect(v.el, inInclusiveRange(-90.0, 90.0));
      expect(v.range, greaterThan(500.0));
      expect(v.altitudeKm, inInclusiveRange(200.0, 2000.0));
    });
  });

  group('GEO 风格静止轨道在窗口内 az/el 基本稳定', () {
    // 近静止轨道：mean motion≈1.0027 rev/day、小偏心率、小倾角。
    const geoL1 =
        '1 99998U 98067A   08264.51782472  .00000000  00000-0  00000-0 0  9990';
    const geoL2 =
        '2 99998   5.0000   0.0000 0001000   0.0000   0.0000  1.00270000999990';

    test('数小时内方位/仰角漂移很小', () {
      final geo = Tle.fromLines(geoL1, geoL2);
      // 西经站点，使 GEO 在其天顶附近。
      const station = Station(lat: 0.0, lon: -100.0);
      final t0 = geo.epoch.add(const Duration(hours: 6));
      final v0 = visibleAt(t0, [geo], station).single;
      final v1 = visibleAt(t0.add(const Duration(hours: 6)), [geo], station)
          .single;

      // 静止轨道：6 小时间 az/el 漂移很小。
      // 注：本移植仅近地主路径、未移植深空日月/共振摄动，GEO 为近似，
      // 故采用宽公差（任务允许）。
      final dAz = (v0.az - v1.az).abs();
      final dEl = (v0.el - v1.el).abs();
      expect(dEl, lessThan(5.0), reason: 'el 漂移 $dEl');
      expect(dAz > 355.0 ? 360.0 - dAz : dAz, lessThan(10.0),
          reason: 'az 漂移');
    });
  });
}
