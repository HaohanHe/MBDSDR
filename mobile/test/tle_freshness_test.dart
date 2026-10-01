// TLE 新鲜度纯函数测试：基于真实解析的 TLE epoch 算距今天数、阈值判定、空态。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/astro/tle_freshness.dart';

void main() {
  const l1 =
      '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753';
  const l2 =
      '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667';
  final tle = Tle.parseThreeLine('VANGUARD 1', l1, l2);

  group('oldestTleAgeDaysUtc', () {
    test('空列表 → null（诚实空态）', () {
      expect(oldestTleAgeDaysUtc(const [], DateTime.utc(2026)), isNull);
    });

    test('now 取 epoch+N 天 → 年龄≈N（浮点天）', () {
      final now = tle.epoch.add(const Duration(days: 5, hours: 12));
      expect(oldestTleAgeDaysUtc([tle], now)!, closeTo(5.5, 1e-6));
    });

    test('多份 TLE 取最老 epoch（保守口径）', () {
      final older = tle;
      final newer = Tle.parseThreeLine('VANGUARD 1', l1, l2);
      final now = older.epoch.add(const Duration(days: 20));
      // 两份相同 epoch → 年龄=20。
      expect(oldestTleAgeDaysUtc([newer, older], now)!, closeTo(20, 1e-6));
    });
  });

  group('isTleStaleAgeDays（阈值 = tleFreshMaxDays）', () {
    test('未超阈值新鲜；超过阈值过期', () {
      expect(isTleStaleAgeDays(AppTokens.tleFreshMaxDays - 1), isFalse);
      expect(isTleStaleAgeDays(AppTokens.tleFreshMaxDays + 1), isTrue);
      // 边界：恰好等于阈值不算过期。
      expect(isTleStaleAgeDays(AppTokens.tleFreshMaxDays), isFalse);
    });
  });

  test('tleFreshnessLabel：空→null；新鲜/过期文案', () {
    expect(tleFreshnessLabel(const [], DateTime.utc(2026)), isNull);
    final freshNow = tle.epoch.add(const Duration(days: 3));
    expect(tleFreshnessLabel([tle], freshNow), contains('新鲜'));
    final staleNow = tle.epoch.add(const Duration(days: 30));
    final label = tleFreshnessLabel([tle], staleNow);
    expect(label, contains('过期'));
  });
}
