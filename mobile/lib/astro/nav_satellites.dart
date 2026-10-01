// 在视导航卫星（GNSS）：复用真实 SGP4 传播 visibleAt，按星座名过滤。
//
// 诚实边界：这是「预测」——由 GNSS TLE 经 SGP4 外推得到当前 az/el/range，
// 不是本机正在接收/解码的卫星；UI 必须标注「预测·非实时接收」。
// 无 GNSS TLE 时由调用方走空态，绝不编造卫星。
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// 依据 Celestrak GNSS 集合里卫星名的真实前缀判定是否为导航星。
/// 拒绝任何非导航星（即使误拉进集合），不硬编码具体 PRN/编号。
bool isNavSatelliteName(String name) {
  final n = name.toUpperCase();
  return n.startsWith('GPS') ||
      n.startsWith('GLONASS') ||
      n.startsWith('GLONASS-') ||
      n.startsWith('GALILEO') ||
      n.startsWith('GAL') ||
      n.startsWith('BDS') ||
      n.startsWith('BEIDOU') ||
      n.startsWith('COMPASS');
}

/// 当前可见（仰角>0）且属于导航星座的卫星预测列表。
/// 距离应落 MEO 量级（~20000+ km）；此处只做过滤，不校准。
List<SatVisibility> visibleNavSats(
  DateTime t,
  List<Tle> tles,
  Station station,
) {
  if (tles.isEmpty) return const <SatVisibility>[];
  return visibleAt(t, tles, station)
      .where((SatVisibility v) => v.isVisible && isNavSatelliteName(v.name))
      .toList();
}
