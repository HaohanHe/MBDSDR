/// 卫星下行频率目录（按 NORAD 编目号索引）。
///
/// 诚实性说明：
///   * 这里只收录「公开、长期稳定、可用 RTL-SDR 直接接收」的下行中心频率。
///   * **2026-11-23 核实：NOAA POES 星座已全部退役**——NOAA-18（2025-06-06）、
///     NOAA-19（2025-08-13）、NOAA-15（2025-08-19）。原来的 137 MHz APT 三条
///     已删除：留着只会把接收机调到没有广播的频率上。
///   * 现收录 ISS（ZARYA, 25544）业余无线电下行 **145.800 MHz**（FM 语音/SSTV，
///     全球统一；仅当 ISS 正在发射时可听到，不是常态信标）。
///   * 目录外的卫星一律返回 null，由 UI 诚实禁用并提示「无下行频率数据」，
///     绝不猜测频率。
///   * 不内置 FM 广播频点；本目录只用于把接收机对准真实的卫星下行。
library;

import '../models/radio_state.dart';

/// 一颗卫星的真实下行参数（用于「捕获」一键调谐）。
class SatDownlink {
  const SatDownlink({
    required this.catalogNumber,
    required this.label,
    required this.downlinkHz,
    required this.mode,
  });

  /// NORAD 编目号（与 Tle.catalogNumber 对齐）。
  final int catalogNumber;

  /// 面向 UI 的事实性名称（如 ISS 语音/SSTV）。
  final String label;

  /// 下行中心频率（Hz）。
  final double downlinkHz;

  /// 推荐解调模式：ISS 业余语音/SSTV 为窄带调频（NFM）。
  final DemodMode mode;
}

/// 公开下行频率表。
///
/// 数值来源：AMSAT / JAXA 公开的 ISS 业余无线电频率资料（全球统一 145.800 MHz）。
/// 只收录当前在轨且有真实广播的条目；退役卫星不收录。
const Map<int, SatDownlink> kSatDownlinks = <int, SatDownlink>{
  // ISS（ZARYA）业余无线电：语音/SSTV 下行 145.800 MHz（FM，全球统一）。
  // 仅当 ISS 正在发射时才有信号；APRS 数字中继在 145.825 MHz（未收录）。
  25544: SatDownlink(
    catalogNumber: 25544,
    label: 'ISS 语音/SSTV',
    downlinkHz: 145.8000e6,
    mode: DemodMode.nfm,
  ),
};

/// 按 NORAD 编目号查下行参数；不在目录内返回 null（调用方诚实禁用）。
SatDownlink? satelliteDownlink(int catalogNumber) =>
    kSatDownlinks[catalogNumber];
