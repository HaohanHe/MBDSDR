import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/radio_state.dart';
import '../services/radio_controller.dart';

// ============================================================================
// 安静的连接状态行：小字号次要文字 + 中性微点。
// ----------------------------------------------------------------------------
// 对齐桌面端本轮语义与 Figma「状态是安静信息」：
//   * 连接中（connecting）→「连接中…」；
//   * 已连接（connected）→ 低调「rtl_tcp 已连接」，不抢视觉；
//   * 运行中掉线（reconnecting，自动重连进行中）→「设备断开，等待重插」，
//     中性文字，禁用红黄警示大贴纸；底层由 RadioController 按指数退避真实
//     重连（每轮新建 Socket 握手），重连成功后本状态自动更新为已连接；
//   * 失败（error）→ 直接展示真实原因（RadioApi.errorMessage），无文案时
//     回退通用「连接失败」。
// ============================================================================
class ConnectionStatusLine extends StatelessWidget {
  const ConnectionStatusLine({required this.radio, super.key});

  final RadioApi radio;

  /// 错误文案截断长度：AppBar 空间有限，过长（含 Socket 地址）截短展示。
  static const int _errorMaxChars = 48;

  @override
  Widget build(BuildContext context) {
    final (String text, Color dotColor) = switch (radio.status) {
      ConnectionStatus.connected => (
          'rtl_tcp 已连接',
          AppTokens.success.withValues(alpha: 0.8),
        ),
      ConnectionStatus.connecting => ('连接中…', AppTokens.textSecondary),
      ConnectionStatus.reconnecting => ('设备断开，等待重插', AppTokens.textSecondary),
      ConnectionStatus.error => (
          _trimmedError(radio.errorMessage),
          AppTokens.warning,
        ),
      ConnectionStatus.disconnected => ('rtl_tcp 未连接', AppTokens.textAt(AppTokens.textAlphaFaint)),
    };

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Container(
          width: AppTokens.spacingS + 2,
          height: AppTokens.spacingS + 2,
          decoration: BoxDecoration(color: dotColor, shape: BoxShape.circle),
        ),
        const SizedBox(width: AppTokens.spacingS + 2),
        Flexible(
          child: Text(
            text,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTokens.auxiliary.copyWith(
              fontSize: AppTokens.annotationFontSize,
              color: radio.status == ConnectionStatus.disconnected
                  ? AppTokens.textAt(AppTokens.textAlphaTertiary)
                  : AppTokens.textSecondary,
            ),
          ),
        ),
      ],
    );
  }

  String _trimmedError(String? msg) {
    if (msg == null || msg.isEmpty) return '连接失败';
    final String oneLine = msg.replaceAll('\n', ' ').trim();
    return oneLine.length > _errorMaxChars
        ? '${oneLine.substring(0, _errorMaxChars)}…'
        : oneLine;
  }
}
