// SPDX-License-Identifier: MIT
//
// 时空状态卡片：时间源 / 接收目标 / 多普勒 / GNSS 四格。
//
// 文案全部由注入的真实服务状态推导（gnss 服务层 GnssFix + radio 连接状态/频率）；
// 缺任何一路都落到诚实空态——绝不编造坐标、绝不把 system 钟伪装成 GNSS 授时、
// 绝不在没有真实 range-rate 时显示一个多普勒数字。
//
// 与桌面 cpp/src/ui/spacetime_format.h 同一套思路：视图模型是纯 Dart（可单测），
// widget 只负责把 SpRole 映射到 tokens 色板。
library;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../gnss/gnss_fix.dart';

/// 四格语义角色（widget 无关；与桌面 SpRole 对齐）。
enum SpRole { neutral, info, ok, warn, danger }

/// 一格：标题 + 主文案 + 颜色角色。
class SpCell {
  const SpCell({required this.title, required this.text, required this.role});
  final String title;
  final String text;
  final SpRole role;
}

/// 时空状态四格的不可变视图模型（纯 Dart，可单测）。
class SpacetimeStatus {
  const SpacetimeStatus({
    required this.timeSource,
    required this.target,
    required this.doppler,
    required this.gnss,
  });

  final SpCell timeSource;
  final SpCell target;
  final SpCell doppler;
  final SpCell gnss;

  /// 从真实服务状态推导；任何一路缺失都落到诚实空态。
  ///
  /// - [fix]：gnss 服务层合并出的最新 fix 快照（空 GnssFix.empty = 无 NMEA）。
  /// - [radioConnected]：接收机是否已连接；[freqHz] 当前真实调谐。
  /// - [targetName]：当前捕获/选中的接收目标（null = 无目标）。
  /// - [dopplerHz]：真实多普勒补偿值（null = 本端无 range-rate，绝不编）。
  /// - [dopplerActive]：移动端是否正在 1 Hz 闭环真实改频（opt-in 开关，默认 off）。
  /// - [dopplerAppliedHz]：闭环当前施加的 VFO 偏移（Hz）；未在补偿为 null。
  factory SpacetimeStatus.fromServices({
    required GnssFix fix,
    bool radioConnected = false,
    int? freqHz,
    String? targetName,
    double? dopplerHz,
    bool dopplerActive = false,
    double? dopplerAppliedHz,
  }) {
    // 时间源：真实 NMEA 语句且带 UTC -> gnss；否则 system（本机时钟，诚实告诫）。
    final hasUtc = fix.source == 'real' &&
        fix.utcTime != null &&
        fix.utcTime!.isNotEmpty;
    final SpCell ts = hasUtc
        ? SpCell(title: '时间源', text: 'GNSS ${fix.utcTime}', role: SpRole.ok)
        : const SpCell(
            title: '时间源',
            text: 'system（本机时钟，非 GNSS 授时）',
            role: SpRole.warn);

    // GNSS 定位：无 fix -> 诚实空态（绝不画假坐标）。
    // 与桌面 spTileGnss 同一口径：坐标 · 星N · HDOP x.x（HDOP 来自真实 NMEA，
    // 无则不追加该段——绝不编造一个精度因子数字）。
    final String gnssDetail = fix.hasFix
        ? '${fix.latitude!.toStringAsFixed(5)},'
            '${fix.longitude!.toStringAsFixed(5)}'
            '${fix.satellites != null ? ' · 星${fix.satellites}' : ''}'
            '${fix.hdop != null ? ' · HDOP ${fix.hdop!.toStringAsFixed(1)}' : ''}'
        : '';
    final SpCell g = fix.hasFix
        ? SpCell(title: 'GNSS 定位', text: gnssDetail, role: SpRole.ok)
        : const SpCell(title: 'GNSS 定位', text: '无 fix', role: SpRole.neutral);

    // 接收目标：优先真实选中目标；否则连接中的当前频率；否则诚实空态。
    final hasTarget = targetName != null && targetName.isNotEmpty;
    late final SpCell t;
    if (hasTarget) {
      final f = (freqHz != null && freqHz > 0)
          ? ' · ${(freqHz / 1e6).toStringAsFixed(4)} MHz'
          : '';
      t = SpCell(title: '接收目标', text: '$targetName$f', role: SpRole.info);
    } else if (radioConnected && freqHz != null && freqHz > 0) {
      t = SpCell(
        title: '接收目标',
        text: '${(freqHz / 1e6).toStringAsFixed(4)} MHz',
        role: SpRole.info,
      );
    } else {
      t = const SpCell(title: '接收目标', text: '无接收目标', role: SpRole.neutral);
    }

    // 多普勒补偿：三态优先级——
    //   1) 正在 1 Hz 闭环改频（dopplerActive 且已施加偏移）→「补偿中·累计 xxx Hz」；
    //   2) 仅显值（有目标 + 有 range-rate，未开闭环）→「补偿值 x Hz」；
    //   3) 否则诚实空态「未补偿（无目标）」，绝不编数。
    final SpCell d;
    if (dopplerActive && dopplerAppliedHz != null && hasTarget) {
      d = SpCell(
        title: '多普勒补偿',
        text: '补偿中·累计 ${dopplerAppliedHz.toStringAsFixed(0)} Hz',
        role: SpRole.ok,
      );
    } else if (dopplerHz != null && hasTarget) {
      d = SpCell(
        title: '多普勒补偿',
        text: '补偿值 ${dopplerHz.toStringAsFixed(1)} Hz',
        role: SpRole.ok,
      );
    } else {
      d = const SpCell(
        title: '多普勒补偿',
        text: '未补偿（无目标）',
        role: SpRole.neutral,
      );
    }

    return SpacetimeStatus(timeSource: ts, target: t, doppler: d, gnss: g);
  }
}

/// 时空状态卡片 widget：把 SpRole 映射到 tokens 色板并渲染 2x2 网格。
class SpacetimeStatusCard extends StatelessWidget {
  const SpacetimeStatusCard({
    super.key,
    this.fix = GnssFix.empty,
    this.radioConnected = false,
    this.freqHz,
    this.targetName,
    this.dopplerHz,
    this.dopplerActive = false,
    this.dopplerAppliedHz,
  });

  /// gnss 服务层最新 fix（默认空 = 无 NMEA，走诚实空态）。
  final GnssFix fix;

  /// 接收机是否已连接。
  final bool radioConnected;

  /// 当前真实调谐（Hz）。
  final int? freqHz;

  /// 当前接收目标名（null = 无目标）。
  final String? targetName;

  /// 真实多普勒补偿值（null = 无 range-rate，绝不编造）。
  final double? dopplerHz;

  /// 是否正在 1 Hz 闭环真实改频（opt-in，默认 off）。
  final bool dopplerActive;

  /// 闭环当前施加的 VFO 偏移（Hz）；未在补偿为 null。
  final double? dopplerAppliedHz;

  Color _color(SpRole r) => switch (r) {
        SpRole.ok => AppTokens.success,
        SpRole.warn => AppTokens.warning,
        SpRole.danger => AppTokens.danger,
        SpRole.info => AppTokens.traceColor,
        SpRole.neutral => AppTokens.textSecondary,
      };

  Widget _cell(SpCell c) => Container(
        padding: const EdgeInsets.all(AppTokens.spacingM),
        decoration: AppTokens.cardDecoration(),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(c.title,
                style: AppTokens.auxiliary.copyWith(
                    fontSize: AppTokens.annotationFontSize)),
            const SizedBox(height: 2),
            Text(c.text,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTokens.body.copyWith(color: _color(c.role))),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final s = SpacetimeStatus.fromServices(
      fix: fix,
      radioConnected: radioConnected,
      freqHz: freqHz,
      targetName: targetName,
      dopplerHz: dopplerHz,
      dopplerActive: dopplerActive,
      dopplerAppliedHz: dopplerAppliedHz,
    );
    // 2x2 用自然高度的 Row 排布（不锁 childAspectRatio），紧凑空间里不溢出。
    Widget row(SpCell a, SpCell b) => Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: _cell(a)),
            const SizedBox(width: AppTokens.spacingS),
            Expanded(child: _cell(b)),
          ],
        );
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.spacingM),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('时空状态', style: AppTokens.sectionTitle),
          const SizedBox(height: AppTokens.spacingS),
          row(s.timeSource, s.target),
          const SizedBox(height: AppTokens.spacingS),
          row(s.doppler, s.gnss),
        ],
      ),
    );
  }
}
