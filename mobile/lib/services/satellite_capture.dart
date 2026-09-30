// 卫星过境「捕获」：把接收机对准真实下行频率。
//
// 诚实边界（务必逐条保留在代码与 UI 文案里）：
//   * 只有在 [kSatDownlinks] 目录里的卫星才有真实下行频率；目录外的过境
//     一律返回 [CaptureUnavailable]（「无下行频率数据」），绝不猜频率、
//     绝不预置台名。
//   * 多普勒只做「捕获那一刻的一次性预测偏置」：用单点 SGP4 传播得到
//     径向速度 v_r，按 f_d ≈ -f0·v_r/c 估算并把中心频率偏置一次。
//     移动端没有随时间连续传播并不断改频的轨道 loop，因此**不做实时
//     多普勒跟踪**——过境中载频仍会持续漂移，需手动在频谱上微调。
//     这一点在回报与 UI 里都如实说明，不伪造实时补偿效果。
library;

import 'package:flutter/foundation.dart';

import '../app/tokens.dart';
import '../astro/coordinates.dart';
import '../astro/passes.dart';
import '../astro/sgp4.dart';
import '../astro/tle.dart';
import '../models/radio_state.dart';
import '../models/satellite.dart';
import '../models/satellite_downlink.dart';
import '../services/radio_controller.dart';

/// 捕获结果（不可变）。
@immutable
sealed class CaptureOutcome {
  const CaptureOutcome();
}

/// 已真实下发调谐：中心频率 = 下行频率 + 一次性预测多普勒偏置。
@immutable
class CaptureApplied extends CaptureOutcome {
  const CaptureApplied({
    required this.label,
    required this.downlinkHz,
    required this.centerHz,
    required this.dopplerHz,
    required this.mode,
  });

  /// 下行名称（事实性，如 NOAA 19 APT）。
  final String label;

  /// 标称下行中心频率（Hz）。
  final double downlinkHz;

  /// 实际下发的接收机中心频率（Hz，已含多普勒偏置并 clamp 到 RTL 范围）。
  final int centerHz;

  /// 一次性预测多普勒偏置（Hz，已并入 centerHz）。
  final double dopplerHz;

  /// 已应用的解调模式。
  final DemodMode mode;

  /// 面向 SnackBar 的简短说明。
  String describe() {
    final khz = (centerHz / 1e3).toStringAsFixed(3);
    final dKhz = (dopplerHz / 1e3).toStringAsFixed(2);
    return '已捕获 $label：$khz kHz · ${mode.label}'
        '（多普勒偏置 $dKhz kHz，仅一次性预测，未实时跟踪）';
  }
}

/// 无法捕获：缺少真实下行频率数据等诚实原因。
@immutable
class CaptureUnavailable extends CaptureOutcome {
  const CaptureUnavailable({required this.reason});

  final String reason;
}

/// 计算并执行一次过境捕获。[radio] 为真实/测试注入的射频接口。
///
/// [tle] 应是与 [pass] 同编目号的 TLE（用于多普勒预测）；传 null 时跳过
/// 多普勒偏置、仍按标称频率捕获。[station] 为本站坐标；缺失时同样跳过
/// 多普勒预测。
Future<CaptureOutcome> capturePass({
  required RadioApi radio,
  required Pass pass,
  required Tle? tle,
  required Station? station,
  required DateTime now,
}) async {
  final dl = satelliteDownlink(pass.catalogNumber);
  if (dl == null) {
    return const CaptureUnavailable(reason: '无下行频率数据');
  }

  // 一次性预测多普勒：单点径向速度。失败（衰减/无站）则偏置 0，诚实退化为
  // 按标称频率捕获，不抛错打断 UI。
  var dopplerHz = 0.0;
  if (tle != null && station != null) {
    try {
      final vr = rangeRateAt(Sgp4(tle), now.toUtc(), station);
      // 远离(vr>0)时接收频率偏低，故中心频率向下偏置：-f0·v_r/c。
      dopplerHz = -dl.downlinkHz * vr / kSpeedOfLightKmS;
    } on Sgp4Exception {
      dopplerHz = 0.0;
    }
  }

  var center = dl.downlinkHz + dopplerHz;
  center = center.clamp(AppTokens.freqMinHz, AppTokens.freqMaxHz);
  final centerHz = center.round();

  await radio.setFrequencyHz(centerHz);
  radio.setMode(dl.mode);

  return CaptureApplied(
    label: dl.label,
    downlinkHz: dl.downlinkHz,
    centerHz: centerHz,
    dopplerHz: dopplerHz,
    mode: dl.mode,
  );
}
