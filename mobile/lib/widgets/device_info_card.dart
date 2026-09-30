import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/radio_state.dart';
import '../services/radio_controller.dart';

// ============================================================================
// 设备信息卡：与桌面端「设备管理」对称的移动端只读回读。
// ----------------------------------------------------------------------------
// 全部数据来自真实 RadioApi，不内置任何假设备/假读数：
//   * 连接状态 —— RadioApi.status（rtl_tcp 真实连接状态机：已连接/连接中/
//     等待重连/连接失败/未连接）；
//   * 后端     —— 当前唯一传输实现就是 rtl_tcp（见 RadioController 注入的
//     RtlTcpClient），已连接时才标注；
//   * 采样率   —— RadioApi.sampleRateHz：连接握手时真实下发给 rtl_tcp、
//     并被 DSP/解调链路实际使用的采样率；
//   * 当前频率 —— RadioApi.freqHz：真实下发给 rtl_tcp 的调谐频率。
// 诚实空态：未连接/连接中/重连中/失败时，后端/采样率/频率一律显示「—」，
// 不拿上一次配置值冒充「设备当前读数」。
// 真实诊断：失败（error）/ 等待重连（reconnecting，含 RadioController 指数退避
// 1→2→4→8→15s 的真实秒数）/ 自动重连连接中，均透出 RadioApi.errorMessage 原文，
// 不吞错误、不伪造原因。
// ============================================================================
class DeviceInfoCard extends StatelessWidget {
  const DeviceInfoCard({required this.radio, super.key});

  final RadioApi radio;

  /// 当前唯一传输后端（RadioController 真实注入 RtlTcpClient）。
  static const String backendLabel = 'rtl_tcp';

  /// 诊断详情单行截断长度：卡片内可两行，取比 AppBar 状态行更宽的预算。
  static const int _detailMaxChars = 96;

  @override
  Widget build(BuildContext context) {
    // 仅在「已连接」时才展示后端/采样率/频率——这些是设备在线时的真实读数；
    // 其余状态（未连接/连接中/重连中/失败）一律空态「—」。
    final bool live = radio.status == ConnectionStatus.connected;

    final (String statusText, Color dotColor) = switch (radio.status) {
      ConnectionStatus.connected => (
          '已连接',
          AppTokens.success.withValues(alpha: 0.8),
        ),
      ConnectionStatus.connecting => ('连接中…', AppTokens.textSecondary),
      ConnectionStatus.reconnecting => ('等待重连', AppTokens.textSecondary),
      ConnectionStatus.error => ('连接失败', AppTokens.warning),
      ConnectionStatus.disconnected => (
          '未连接',
          AppTokens.textAt(AppTokens.textAlphaFaint),
        ),
    };

    final Color valueColor = live
        ? AppTokens.textPrimary
        : AppTokens.textAt(AppTokens.textAlphaTertiary);

    // 真实诊断详情：失败原因 / 退避重连秒数。仅在确有真实消息时出现。
    final String? detail = switch (radio.status) {
      ConnectionStatus.error ||
      ConnectionStatus.reconnecting ||
      ConnectionStatus.connecting =>
        _trimmed(radio.errorMessage),
      _ => null,
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        _InfoRow(
          label: '状态',
          value: statusText,
          dot: dotColor,
          valueColor: live
              ? AppTokens.textPrimary
              : AppTokens.textAt(AppTokens.textAlphaTertiary),
        ),
        if (detail != null) ...<Widget>[
          const SizedBox(height: AppTokens.spacingS),
          // 诊断行与状态值对齐（跳过 56 标签宽 + 8 间距），次要弱色小字，克制。
          Padding(
            padding: const EdgeInsetsDirectional.only(start: 64),
            child: Text(
              detail,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: AppTokens.auxiliary.copyWith(
                color: AppTokens.textAt(AppTokens.textAlphaTertiary),
              ),
            ),
          ),
        ],
        const SizedBox(height: AppTokens.spacingS),
        _InfoRow(
          label: '后端',
          value: live ? backendLabel : '—',
          valueColor: valueColor,
        ),
        const SizedBox(height: AppTokens.spacingS),
        _InfoRow(
          label: '采样率',
          value: live
              ? '${(radio.sampleRateHz / 1e6).toStringAsFixed(2)} Msps'
              : '—',
          valueColor: valueColor,
          mono: true,
        ),
        const SizedBox(height: AppTokens.spacingS),
        _InfoRow(
          label: '频率',
          value: live
              ? '${(radio.freqHz / 1e6).toStringAsFixed(4)} MHz'
              : '—',
          valueColor: valueColor,
          mono: true,
        ),
      ],
    );
  }

  /// 诊断文案折行截断；空则返回 null（不渲染详情行）。
  String? _trimmed(String? msg) {
    if (msg == null) return null;
    final String oneLine = msg.replaceAll('\n', ' ').trim();
    if (oneLine.isEmpty) return null;
    return oneLine.length > _detailMaxChars
        ? '${oneLine.substring(0, _detailMaxChars)}…'
        : oneLine;
  }
}

/// 单行「标签 …… 值」：标签次要色定宽，值用等宽仪器字体。
class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.label,
    required this.value,
    required this.valueColor,
    this.dot,
    this.mono = false,
  });

  final String label;
  final String value;
  final Color valueColor;
  final Color? dot;
  final bool mono;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        SizedBox(
          width: 56,
          child: Text(label, style: AppTokens.auxiliary),
        ),
        const SizedBox(width: AppTokens.spacingM),
        if (dot != null) ...<Widget>[
          Container(
            width: AppTokens.spacingS + 2,
            height: AppTokens.spacingS + 2,
            decoration: BoxDecoration(
              color: dot,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: AppTokens.spacingS + 2),
        ],
        Text(
          value,
          style: (mono ? AppTokens.mono : AppTokens.body).copyWith(
            color: valueColor,
          ),
        ),
      ],
    );
  }
}
