// TLE 新鲜度：基于真实 TLE epoch 计算距今天数并判定是否过期。
//
// 诚实边界：新鲜度只是参考标注——TLE 越老轨道外推越不准；这里只如实报告
// 「最老一份 TLE 的 epoch 距今天数」，不据此修改 SGP4 几何结果。
import '../app/tokens.dart';
import 'tle.dart';

/// 最老一份 TLE 的 epoch 距今天数（天，浮点）。无 TLE 返回 null。
/// 用最老 epoch 是保守口径：只要有一份偏旧就提示刷新。
double? oldestTleAgeDaysUtc(List<Tle> tles, DateTime nowUtc) {
  if (tles.isEmpty) return null;
  DateTime oldest = tles.first.epoch;
  for (final Tle t in tles) {
    if (t.epoch.isBefore(oldest)) oldest = t.epoch;
  }
  return nowUtc.difference(oldest).inMicroseconds / 86400 / 1e6;
}

/// 该年龄（天）是否超过新鲜阈值（AppTokens.tleFreshMaxDays）。
bool isTleStaleAgeDays(double ageDays) => ageDays > AppTokens.tleFreshMaxDays;

/// 新鲜度一句话标注（无 TLE 返回 null，由调用方决定空态）。
/// 形如「TLE 3 天前 · 新鲜」/「TLE 19 天前 · 过期，建议刷新」。
String? tleFreshnessLabel(List<Tle> tles, DateTime nowUtc) {
  final double? age = oldestTleAgeDaysUtc(tles, nowUtc);
  if (age == null) return null;
  final int days = age.round();
  final bool stale = isTleStaleAgeDays(age);
  return 'TLE $days 天前 · ${stale ? '过期，建议刷新' : '新鲜'}';
}
