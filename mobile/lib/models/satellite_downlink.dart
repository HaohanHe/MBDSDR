/// 卫星下行频率目录（按 NORAD 编目号索引）。
///
/// 诚实性说明：
///   * 这里只收录「公开、长期稳定、可用 RTL-SDR 直接接收」的下行中心频率——
///     即 NOAA 极轨气象卫星的 137 MHz APT 自动图像传输。这些频率是卫星载荷
///     的事实性广播参数（与台呼/位置无关），不是为了凑数而编造的假台名。
///   * 目录外的卫星（包括 amateur/stations 分组里的大多数）一律返回 null，
///     由 UI 诚实禁用并提示「无下行频率数据」，绝不猜测频率。
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

  /// 面向 UI 的事实性名称（如 NOAA 19）。
  final String label;

  /// 下行中心频率（Hz）。
  final double downlinkHz;

  /// 推荐解调模式：APT 为宽带调频（WFM）。
  final DemodMode mode;
}

/// 公开 APT 下行频率表（NOAA 极轨气象卫星，137 MHz 频段）。
///
/// 数值来源：NOAA POES APT 广播频率的公开工程资料（长期稳定）。仅收录
/// RTL-SDR 可直接接收的主流在轨卫星；失效/退役星不收录。
const Map<int, SatDownlink> kSatDownlinks = <int, SatDownlink>{
  // NOAA-19 (POES)
  33591: SatDownlink(
    catalogNumber: 33591,
    label: 'NOAA 19 APT',
    downlinkHz: 137.1000e6,
    mode: DemodMode.wfm,
  ),
  // NOAA-18 (POES)
  28654: SatDownlink(
    catalogNumber: 28654,
    label: 'NOAA 18 APT',
    downlinkHz: 137.9125e6,
    mode: DemodMode.wfm,
  ),
  // NOAA-15 (POES)
  25338: SatDownlink(
    catalogNumber: 25338,
    label: 'NOAA 15 APT',
    downlinkHz: 137.6200e6,
    mode: DemodMode.wfm,
  ),
};

/// 按 NORAD 编目号查下行参数；不在目录内返回 null（调用方诚实禁用）。
SatDownlink? satelliteDownlink(int catalogNumber) =>
    kSatDownlinks[catalogNumber];
