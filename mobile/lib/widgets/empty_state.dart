import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../l10n/gen/app_localizations.dart';

// ============================================================================
// 诚实空态：图标 + 标题 + 一行说明 + 可选操作按钮。
// ----------------------------------------------------------------------------
// 文案只描述「当前确实缺什么」与「下一步能做什么」，不伪造数据、不显示
// 假连接 / 假卫星。语义化构造对应产品里反复出现的三类空态，便于复用。
// ============================================================================

/// 通用空态占位：图标 + 标题 + 一行说明 + 可选操作按钮。
class EmptyState extends StatelessWidget {
  const EmptyState({
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
    super.key,
  });

  /// 未连接 rtl_tcp：IQ 流尚未建立。
  ///
  /// [onConnect] 提供后渲染「连接」按钮；文案提示先在设置里配好主机端口。
  factory EmptyState.notConnected(
    BuildContext context, {
    VoidCallback? onConnect,
    Key? key,
  }) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return EmptyState(
      key: key,
      icon: Icons.link_off_outlined,
      title: l10n.emptyNotConnectedTitle,
      message: l10n.emptyNotConnectedMessage,
      actionLabel: onConnect == null ? null : l10n.emptyConnect,
      onAction: onConnect,
    );
  }

  /// 未定位：还没有可用的本站坐标。
  ///
  /// [onRetry] 提供后渲染「重试定位」按钮；文案同时提示可手动填坐标。
  factory EmptyState.noFix(
    BuildContext context, {
    VoidCallback? onRetry,
    Key? key,
  }) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return EmptyState(
      key: key,
      icon: Icons.location_disabled_outlined,
      title: l10n.emptyNoFixTitle,
      message: l10n.emptyNoFixMessage,
      actionLabel: onRetry == null ? null : l10n.emptyRetryLocate,
      onAction: onRetry,
    );
  }

  /// 无可用卫星 / TLE：轨道数据尚未就绪。
  ///
  /// [onRefresh] 提供后渲染「刷新」按钮；文案不展示任何占位卫星。
  factory EmptyState.noSatellites(
    BuildContext context, {
    VoidCallback? onRefresh,
    Key? key,
  }) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return EmptyState(
      key: key,
      icon: Icons.satellite_alt_outlined,
      title: l10n.emptyNoSatellitesTitle,
      message: l10n.emptyNoSatellitesMessage,
      actionLabel: onRefresh == null ? null : l10n.emptyRefresh,
      onAction: onRefresh,
    );
  }

  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    // 单滚降级：视口被压缩（如首跑引导横幅占位、小屏横屏）时内容可滚动，
    // 不出现 RenderFlex 溢出。正常视口下内容一屏放得下，滚动条不出现。
    return Center(
      child: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(AppTokens.spacingL * 2),
          child: ConstrainedBox(
            constraints:
                const BoxConstraints(maxWidth: AppTokens.emptyStateMaxWidth),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Icon(
                  icon,
                  size: AppTokens.iconSizeEmpty,
                  color: AppTokens.textAt(AppTokens.textAlphaFaint),
                ),
                const SizedBox(height: AppTokens.spacingL),
                Text(
                  title,
                  style: AppTokens.sectionTitle,
                  textAlign: TextAlign.center,
                ),
                if (message != null) ...[
                  const SizedBox(height: AppTokens.spacingM),
                  Text(
                    message!,
                    style: AppTokens.auxiliary,
                    textAlign: TextAlign.center,
                  ),
                ],
                if (actionLabel != null && onAction != null) ...[
                  const SizedBox(height: AppTokens.spacingL),
                  FilledButton(
                      onPressed: onAction, child: Text(actionLabel!)),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
