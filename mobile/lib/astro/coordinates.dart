/// 坐标变换链：WGS84 大地 ↔ ECEF，TEME --GMST--> ECEF，ECEF --> 站心 ENU。
///
/// 约定：
///   * TEME->ECEF 用 Rz(-GMST)（Vallado teme_ecef 约定）；忽略章动/极移。
///   * 方位角 [az] 自北顺时针 0..360°；仰角 [el] -90..90°；斜距 [range] km。
library;

import 'dart:math' as math;

import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// WGS84 椭球常数。
class Wgs84 {
  static const double a = 6378.137; // km
  static const double f = 1.0 / 298.257223563;
  static const double e2 = f * (2.0 - f);
}

/// 大地坐标(Station) -> ECEF(km)。
Vec3 ecefFromGeodetic(Station s) {
  final lat = s.lat * math.pi / 180.0;
  final lon = s.lon * math.pi / 180.0;
  final sinLat = math.sin(lat);
  final cosLat = math.cos(lat);
  final n = Wgs84.a / math.sqrt(1.0 - Wgs84.e2 * sinLat * sinLat);
  final alt = s.alt;
  return Vec3(
    (n + alt) * cosLat * math.cos(lon),
    (n + alt) * cosLat * math.sin(lon),
    (n * (1.0 - Wgs84.e2) + alt) * sinLat,
  );
}

/// TEME(km) -> ECEF(km)，[gst] 为格林尼治恒星时(rad)。
Vec3 temeToEcef(Vec3 teme, double gst) {
  final c = math.cos(gst);
  final s = math.sin(gst);
  return Vec3(
    teme.x * c + teme.y * s,
    -teme.x * s + teme.y * c,
    teme.z,
  );
}

/// 站心几何结果。
class AzEl {
  const AzEl({
    required this.az,
    required this.el,
    required this.range,
    required this.altitudeKm,
  });

  /// 方位角（度，自北顺时针）。
  final double az;

  /// 仰角（度）。
  final double el;

  /// 斜距（km）。
  final double range;

  /// 卫星径向高度（km，近似 |r|-地球平均半径）。
  final double altitudeKm;
}

/// 由卫星 ECEF 与测站 ECEF 算站心 az/el/range。
AzEl ecefToAzEl(Vec3 satEcef, Vec3 statEcef, Station s) {
  final rho = satEcef - statEcef;
  final lat = s.lat * math.pi / 180.0;
  final lon = s.lon * math.pi / 180.0;
  final sinLat = math.sin(lat);
  final cosLat = math.cos(lat);
  final sinLon = math.sin(lon);
  final cosLon = math.cos(lon);

  // ENU 基向量。
  final e = (-sinLon * rho.x + cosLon * rho.y);
  final n = (-sinLat * cosLon * rho.x -
      sinLat * sinLon * rho.y +
      cosLat * rho.z);
  final u = (cosLat * cosLon * rho.x +
      cosLat * sinLon * rho.y +
      sinLat * rho.z);

  final range = math.sqrt(e * e + n * n + u * u);
  var az = math.atan2(e, n) * 180.0 / math.pi;
  if (az < 0.0) az += 360.0;
  final el = math.asin((u / range).clamp(-1.0, 1.0)) * 180.0 / math.pi;
  final altitudeKm = satEcef.norm - Wgs84.a;
  return AzEl(az: az, el: el, range: range, altitudeKm: altitudeKm);
}

/// 一站式：给定 [propagator]、时刻 [t]、测站 [station]，返回站心几何。
AzEl azElAt(Sgp4 propagator, DateTime t, Station station) {
  final pv = propagator.propagate(t);
  final gst = Sgp4.gmst(Sgp4.julianDate(t.toUtc()));
  final satEcef = temeToEcef(pv.r, gst);
  final statEcef = ecefFromGeodetic(station);
  return ecefToAzEl(satEcef, statEcef, station);
}
