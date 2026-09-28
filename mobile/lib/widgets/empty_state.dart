import 'package:flutter/material.dart';

import '../app/tokens.dart';

/// 空状态占位：图标 + 标题 + 可选副文案 + 可选动作按钮。
class EmptyState extends StatelessWidget {
  const EmptyState({
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
    super.key,
  });

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
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(icon, size: 48, color: AppTokens.textAt(AppTokens.textAlphaFaint)),
            const SizedBox(height: AppTokens.spacingL),
            Text(title, style: AppTokens.sectionTitle, textAlign: TextAlign.center),
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
    );
  }
}
