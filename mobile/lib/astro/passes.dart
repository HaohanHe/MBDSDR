/// 过境预测：某时刻可见几何 + 未来窗口内的升/降过境。
library;

import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// 一次完整过境（升起->最高->落下）。
class Pass {
  const Pass({
    required this.name,
    required this.riseTime,
    required this.riseAz,
    required this.setTime,
    required this.setAz,
    required this.maxEl,
    required this.maxTime,
  });

  final String name;
  final DateTime riseTime;
  final double riseAz;
  final DateTime setTime;
  final double setAz;
  final double maxEl;
  final DateTime maxTime;

  /// 持续时长。
  Duration get duration => setTime.difference(riseTime);

  @override
  String toString() =>
      'Pass($name: rise ${riseTime.toIso8601String()} az=${riseAz.toStringAsFixed(0)}°, '
      'max=${maxEl.toStringAsFixed(1)}°, dur=${duration.inSeconds}s)';
}

/// 计算 [t] 时刻所有 [tles] 相对 [station] 的几何（含地平线以下，调用方筛选）。
List<SatVisibility> visibleAt(DateTime t, List<Tle> tles, Station station) {
  final out = <SatVisibility>[];
  for (final tle in tles) {
    try {
      final azEl = azElAt(Sgp4(tle), t, station);
      out.add(SatVisibility(
        name: tle.name,
        az: azEl.az,
        el: azEl.el,
        range: azEl.range,
        altitudeKm: azEl.altitudeKm,
        tle: tle,
      ));
    } on Sgp4Exception {
      // 衰减/失效卫星：跳过。
    }
  }
  return out;
}

/// 预测未来 [hours] 小时内 [tles] 相对 [station] 的完整过境。
///
/// 以 [stepSeconds] 步进采样，检测仰角由负转正（升）与正转负（降），
/// 用线性插值细化升降时刻与方位角；过境区间内取最高仰角。
List<Pass> predictPasses(
  List<Tle> tles,
  Station station, {
  int hours = 48,
  int stepSeconds = 30,
  DateTime? startTime,
}) {
  final start = (startTime ?? DateTime.now().toUtc()).toUtc();
  final step = Duration(seconds: stepSeconds);
  final nSteps = (hours * 3600) ~/ stepSeconds;
  final result = <Pass>[];

  for (final tle in tles) {
    final Sgp4 sat;
    try {
      sat = Sgp4(tle);
    } on Sgp4Exception {
      continue;
    }

    // 采样仰角/方位。
    final el = <double>[];
    final az = <double>[];
    final times = <DateTime>[];
    var ok = true;
    for (var k = 0; k <= nSteps; k++) {
      final t = start.add(step * k);
      try {
        final a = azElAt(sat, t, station);
        el.add(a.el);
        az.add(a.az);
        times.add(t);
      } on Sgp4Exception {
        ok = false;
        break;
      }
    }
    if (!ok || el.length < 2) continue;

    // 找完整可见区间。
    var i = 0;
    while (i < el.length - 1) {
      // 升：el[i]<=0 且 el[i+1]>0。
      if (el[i] <= 0.0 && el[i + 1] > 0.0) {
        final riseFrac = -el[i] / (el[i + 1] - el[i]);
        final riseTime = times[i].add(
          Duration(microseconds: (riseFrac * step.inMicroseconds).round()),
        );
        final riseAz = _interpAz(az[i], az[i + 1], riseFrac);

        // 在区间内找最高仰角，并定位降落。
        var j = i + 1;
        var bestIdx = i + 1;
        var bestEl = el[i + 1];
        var setTime = times.last;
        var setAz = az.last;
        var foundSet = false;
        while (j < el.length - 1) {
          if (el[j] > bestEl) {
            bestEl = el[j];
            bestIdx = j;
          }
          // 降：el[j]>0 且 el[j+1]<=0。
          if (el[j] > 0.0 && el[j + 1] <= 0.0) {
            final setFrac = el[j] / (el[j] - el[j + 1]);
            setTime = times[j].add(
              Duration(microseconds: (setFrac * step.inMicroseconds).round()),
            );
            setAz = _interpAz(az[j], az[j + 1], setFrac);
            foundSet = true;
            break;
          }
          j++;
        }
        if (el[j] > bestEl) {
          bestEl = el[j];
          bestIdx = j;
        }
        if (foundSet) {
          result.add(Pass(
            name: tle.name,
            riseTime: riseTime,
            riseAz: riseAz,
            setTime: setTime,
            setAz: setAz,
            maxEl: bestEl,
            maxTime: times[bestIdx],
          ));
        }
        i = j + 1;
      } else {
        i++;
      }
    }
  }
  return result;
}

double _interpAz(double a0, double a1, double frac) {
  // 处理方位跨越 360 边界。
  var d = a1 - a0;
  if (d > 180.0) d -= 360.0;
  if (d < -180.0) d += 360.0;
  var az = a0 + frac * d;
  az = az % 360.0;
  if (az < 0.0) az += 360.0;
  return az;
}
