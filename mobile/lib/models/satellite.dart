/// 卫星指向引导相关的轻量不可变数据模型。
///
/// 跨模块契约：
///   * [Station] 由外壳模块 import，用于向 SkyPage 传「手填本站位置」。
///   * [SkyPage] 构造函数固定为 `SkyPage({super.key, Station? manualStation})`。
library;

import 'package:mbdsdr_mobile/astro/tle.dart';

/// 地面测站（大地坐标，WGS84）。
///
/// [lat] 纬度（度，北正南负），[lon] 经度（度，东正西负），
/// [alt] 海拔（km）。
class Station {
  const Station({required this.lat, required this.lon, this.alt = 0.0});

  final double lat;
  final double lon;
  final double alt;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Station &&
          other.lat == lat &&
          other.lon == lon &&
          other.alt == alt;

  @override
  int get hashCode => Object.hash(lat, lon, alt);

  @override
  String toString() => 'Station($lat°, $lon°, alt=${alt}km)';
}

/// 某一时刻某卫星相对测站的可见性几何结果（站心 ENU）。
///
/// [az] 方位角（度，自北顺时针 0..360），[el] 仰角（度，-90..90），
/// [range] 斜距（km），[altitudeKm] 卫星大地高度（km）。
class SatVisibility {
  const SatVisibility({
    required this.name,
    required this.az,
    required this.el,
    required this.range,
    required this.altitudeKm,
    required this.tle,
  });

  final String name;
  final double az;
  final double el;
  final double range;
  final double altitudeKm;
  final Tle tle;

  /// 是否在地平线以上（仰角为正）。
  bool get isVisible => el > 0.0;

  @override
  String toString() =>
      'SatVisibility($name: az=${az.toStringAsFixed(1)}°, '
      'el=${el.toStringAsFixed(1)}°, r=${range.toStringAsFixed(0)}km)';
}
