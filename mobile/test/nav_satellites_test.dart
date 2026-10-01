// 在视导航卫星测试：星座名过滤（接受 GNSS、拒绝非导航星）、空态、
// 复用真实 SGP4 visibleAt 传播。不伪造卫星。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/nav_satellites.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

void main() {
  const station = Station(lat: 39.9, lon: 116.4, alt: 0.1);

  group('isNavSatelliteName（真实星座前缀）', () {
    test('接受 GPS/GLONASS/Galileo/BeiDou', () {
      expect(isNavSatelliteName('GPS BIIR-2  (PRN 12)'), isTrue);
      expect(isNavSatelliteName('GLONASS M  #757'), isTrue);
      expect(isNavSatelliteName('GALILEO-209'), isTrue);
      expect(isNavSatelliteName('BDS-3 M3'), isTrue);
    });
    test('拒绝非导航星（气象/业余/空间站）', () {
      expect(isNavSatelliteName('NOAA 19'), isFalse);
      expect(isNavSatelliteName('ISS (ZARYA)'), isFalse);
      expect(isNavSatelliteName('VANGUARD 1'), isFalse);
      expect(isNavSatelliteName('AO-91'), isFalse);
    });
  });

  group('visibleNavSats（复用真实 SGP4）', () {
    const l1 =
        '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
    const l2 =
        '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';
    final vanguard = Tle.parseThreeLine('VANGUARD 1', l1, l2);

    test('空 TLE → 空列表（诚实空态）', () {
      expect(visibleNavSats(DateTime.utc(2026), const [], station), isEmpty);
    });

    test('非导航星（Vanguard）即使可见也被过滤掉', () {
      final res = visibleNavSats(vanguard.epoch, [vanguard], station);
      expect(res, isEmpty, reason: 'Vanguard 不是导航星，不应出现在导航列表');
    });
  });
}
