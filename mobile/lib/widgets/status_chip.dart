import 'package:flutter/material.dart';

import '../app/tokens.dart';

/// 状态小药丸：彩色圆点 + 文字，用于 AppBar 上的 rtl 连接状态。
class StatusChip extends StatelessWidget {
  const StatusChip({
    required this.color,
    required this.text,
    this.dot = true,
    super.key,
  });

  final Color color;
  final String text;
  final bool dot;

  @override
  Widget build(BuildContext context) {
    return Container(
      // 药丸紧凑内边距：横向 spacingM，纵向约 spacingS*0.75（半格微收）。
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingM,
        vertical: AppTokens.spacingS * 0.75,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (dot) ...[
            Container(
              width: AppTokens.spacingM,
              height: AppTokens.spacingM,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
            const SizedBox(width: AppTokens.spacingS * 1.5),
          ],
          Text(
            text,
            style: AppTokens.auxiliary.copyWith(
              fontSize: AppTokens.annotationFontSize + 1,
              fontWeight: FontWeight.w500,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}
