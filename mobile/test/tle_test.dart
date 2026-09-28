import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';

void main() {
  group('TLE 解析', () {
    const l1 =
        '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
    const l2 =
        '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';

    test('真实 Vanguard TLE 字段', () {
      final tle = Tle.parseThreeLine('VANGUARD 1', l1, l2);
      expect(tle.catalogNumber, 5);
      expect(tle.inclinationDeg, closeTo(34.2682, 1e-4));
      expect(tle.raanDeg, closeTo(348.7242, 1e-4));
      expect(tle.eccentricity, closeTo(0.1859667, 1e-6));
      expect(tle.meanMotionRevDay, closeTo(10.824191574, 1e-6));
      // 历元：2000 年第 179.78495062 日 -> 2000-06-27 18:50 UTC
      expect(tle.epoch.year, 2000);
      expect(tle.epoch.month, 6);
      expect(tle.epoch.day, 27);
      expect(tle.epoch.hour, 18);
      expect(tle.epoch.minute, 50);
    });

    test('批量解析名称行 + 注释', () {
      const text = '''
# comment line
VANGUARD 1
$l1
$l2
''';
      final list = Tle.parseBatch(text);
      expect(list.length, 1);
      expect(list.first.name, 'VANGUARD 1');
    });
  });

  group('校验和', () {
    const good1 =
        '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
    const good2 =
        '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';

    test('正确校验和通过', () {
      expect(Tle.validateChecksum(good1), isTrue);
      expect(Tle.validateChecksum(good2), isTrue);
    });

    test('篡改一位数字校验和失败', () {
      final bad = '${good1.substring(0, 68)}7'; // 原校验和为 3
      expect(Tle.validateChecksum(good1), isTrue);
      expect(Tle.validateChecksum(bad), isFalse);
    });

    test('computeChecksum 与末位一致', () {
      expect(Tle.computeChecksum(good1), int.parse(good1[68]));
    });
  });

  group('畸形输入', () {
    test('行列格式错误抛异常', () {
      expect(
        () => Tle.fromLines('garbage line one', 'garbage line two'),
        throwsA(isA<TleFormatException>()),
      );
    });

    test('两行编目号不一致抛异常', () {
      const l1 =
          '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
      const l2 =
          '2 00006  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';
      expect(
        () => Tle.fromLines(l1, l2),
        throwsA(isA<TleFormatException>()),
      );
    });
  });
}
