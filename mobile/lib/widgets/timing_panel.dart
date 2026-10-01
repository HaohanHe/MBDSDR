// 授时面板：真实系统时钟 + GNSS 授时三态。
//
// 诚实边界（移动端无 NMEA / 无驯服时钟）：
//   * 系统时间 = 设备真实时钟（DateTime.now().toUtc()），恒显示，秒级跳动；
//   * GNSS 授时三态按真实数据判定，绝不造假 fix：
//       - hasFix   ：有真实定位/授时 fix 时间戳 → 显示 fix 时间与 Δt(ms)；
//       - noFix    ：定位服务可用但尚无 fix；
//       - noModule ：无 GNSS/定位模块可用（移动端默认态）→ 诚实显示「无 GNSS 授时」。
// 生产默认源 = 系统时钟、无 GNSS fix；测试注入 fake 源覆盖三态。
import 'dart:async';

import 'package:flutter/material.dart';

import '../app/tokens.dart';

/// GNSS 授时质量三态。
enum TimingQuality {
  /// 有真实 fix：显示 fix 时间与 Δt。
  hasFix,

  /// 服务可用但尚无 fix。
  noFix,

  /// 无 GNSS/定位模块可用（移动端默认）。
  noModule,
}

/// 一次授时快照。systemTimeUtc 恒为真实时钟；gnssFixTimeUtc 仅 hasFix 时非空。
class TimingSnapshot {
  final TimingQuality quality;

  /// GNSS 授时 fix 时间（UTC）；无 fix/无模块时为 null，绝不伪造。
  final DateTime? gnssFixTimeUtc;

  /// 设备系统时间（UTC），恒真实。
  final DateTime systemTimeUtc;

  const TimingSnapshot({
    required this.quality,
    required this.systemTimeUtc,
    this.gnssFixTimeUtc,
  });

  /// Δt = 系统钟 − GNSS fix 钟（ms）；无 fix 时为 null。
  Duration? get delta => gnssFixTimeUtc == null
      ? null
      : systemTimeUtc.difference(gnssFixTimeUtc!).abs();
}

/// 授时数据源缝：生产用系统时钟实现；测试注入 fake。
abstract class TimingSource {
  TimingSnapshot snapshot();
}

/// 生产默认源：真实系统时钟，无 GNSS fix（移动端无 NMEA）。
class SystemClockTimingSource implements TimingSource {
  const SystemClockTimingSource();

  @override
  TimingSnapshot snapshot() => TimingSnapshot(
        quality: TimingQuality.noModule,
        systemTimeUtc: DateTime.now().toUtc(),
      );
}

/// 授时面板卡片：系统钟秒级跳动 + GNSS 授时三态。
class TimingPanel extends StatefulWidget {
  /// 授时数据源；默认系统时钟（无 GNSS fix）。
  final TimingSource source;

  const TimingPanel({super.key, this.source = const SystemClockTimingSource()});

  @override
  State<TimingPanel> createState() => _TimingPanelState();
}

class _TimingPanelState extends State<TimingPanel> {
  Timer? _ticker;
  late TimingSnapshot _snap;

  @override
  void initState() {
    super.initState();
    _snap = widget.source.snapshot();
    // 系统钟秒级跳动（授时面板的最小可读粒度）。
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() => _snap = widget.source.snapshot());
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  String _hhmmss(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:'
      '${t.minute.toString().padLeft(2, '0')}:'
      '${t.second.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final q = _snap.quality;
    final Color badgeColor = switch (q) {
      TimingQuality.hasFix => AppTokens.success,
      TimingQuality.noFix => AppTokens.warning,
      TimingQuality.noModule => AppTokens.textSecondary,
    };
    final String gnssLine = switch (q) {
      TimingQuality.hasFix =>
        'fix ${_hhmmss(_snap.gnssFixTimeUtc!)}  Δt ${_snap.delta!.inMilliseconds} ms',
      TimingQuality.noFix => 'GNSS 无 fix',
      TimingQuality.noModule => '无 GNSS 授时',
    };

    return Container(
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: BoxDecoration(
        color: AppTokens.card1,
        borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              const Text('授时', style: AppTokens.auxiliary),
              const Spacer(),
              Container(
                width: AppTokens.spacingS,
                height: AppTokens.spacingS,
                decoration: BoxDecoration(color: badgeColor, shape: BoxShape.circle),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.spacingS),
          Text(
            '系统 ${_hhmmss(_snap.systemTimeUtc)} UTC',
            style: AppTokens.mono.copyWith(
              color: AppTokens.textPrimary,
              fontWeight: AppTokens.weightMedium,
            ),
          ),
          const SizedBox(height: AppTokens.spacingS),
          Text(gnssLine, style: AppTokens.mono),
        ],
      ),
    );
  }
}
