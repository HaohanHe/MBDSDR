import 'package:flutter/material.dart';

import '../app/tokens.dart';

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
  const EmptyState.notConnected({VoidCallback? onConnect, super.key})
      : icon = Icons.link_off_outlined,
        title = '未连接 rtl_tcp',
        message = '请在「设置」中确认接收机主机与端口，'
            '并在电脑或树莓派上启动 rtl_tcp。',
        actionLabel = onConnect == null ? null : '连接',
        onAction = onConnect;

  /// 未定位：还没有可用的本站坐标。
  ///
  /// [onRetry] 提供后渲染「重试定位」按钮；文案同时提示可手动填坐标。
  const EmptyState.noFix({VoidCallback? onRetry, super.key})
      : icon = Icons.location_disabled_outlined,
        title = '暂未定位',
        message = '请开启系统定位并授权，或在「设置 → 本站位置」中手动填写坐标。',
        actionLabel = onRetry == null ? null : '重试定位',
        onAction = onRetry;

  /// 无可用卫星 / TLE：轨道数据尚未就绪。
  ///
  /// [onRefresh] 提供后渲染「刷新」按钮；文案不展示任何占位卫星。
  const EmptyState.noSatellites({VoidCallback? onRefresh, super.key})
      : icon = Icons.satellite_alt_outlined,
        title = '暂无可用卫星数据',
        message = '尚未加载到卫星轨道参数（TLE）。请检查网络后刷新，或先设置本站位置。',
        actionLabel = onRefresh == null ? null : '刷新',
        onAction = onRefresh;

  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTokens.spacingL * 2),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: AppTokens.emptyStateMaxWidth),
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
                FilledButton(onPressed: onAction, child: Text(actionLabel!)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
