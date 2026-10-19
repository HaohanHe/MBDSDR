// 首跑引导横幅（OnAir 首跑向导模式的轻量落地）。
//
// 设计取舍：
//   * 不做阻断式模态向导——第一次打开 App 的用户可能只想先看一眼频谱；
//   * 一张可消除的横幅卡挂在首页顶部，三步要点 + 两个动作（去设置 / 知道了）；
//   * 「知道了」即写持久化标记，之后永不再出现；设置页仍可从头帮助页随时查看。
// 诚实性：只描述当前真实存在的功能，不承诺未接线能力。
library;

import 'package:flutter/material.dart';

import '../app/tokens.dart';

class FirstRunGuide extends StatelessWidget {
  const FirstRunGuide({
    super.key,
    required this.onOpenSettings,
    required this.onDismiss,
  });

  /// 跳转设置页（由外壳注入：切到设置页下标）。
  final VoidCallback onOpenSettings;

  /// 「知道了」：写持久化标记并移除横幅。
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppTokens.spacingL,
        AppTokens.spacingM,
        AppTokens.spacingL,
        AppTokens.spacingS,
      ),
      child: Container(
        decoration: AppTokens.cardDecoration(color: AppTokens.card2),
        padding: const EdgeInsets.all(AppTokens.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Row(
              children: <Widget>[
                Icon(Icons.explore_outlined,
                    size: AppTokens.iconSizeInlineLg, color: AppTokens.accent),
                SizedBox(width: AppTokens.spacingM),
                Text('第一次使用？三步上路', style: AppTokens.sectionTitle),
              ],
            ),
            const SizedBox(height: AppTokens.spacingM),
            const _Bullet(text: '设置页填 rtl_tcp 接收机的主机地址与端口（同一局域网）'),
            const _Bullet(text: '回频谱页点连接，瀑布图实时绘制'),
            const _Bullet(text: '点瀑布选频率，选对解调模式即可收听'),
            const SizedBox(height: AppTokens.spacingM),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: <Widget>[
                TextButton(
                  onPressed: onDismiss,
                  child: const Text('知道了'),
                ),
                const SizedBox(width: AppTokens.spacingS),
                FilledButton(
                  onPressed: onOpenSettings,
                  child: const Text('去设置'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.spacingS),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            margin: const EdgeInsets.only(top: 6, right: AppTokens.spacingM),
            width: AppTokens.spacingS + 2,
            height: AppTokens.spacingS + 2,
            decoration: const BoxDecoration(
              color: AppTokens.accent,
              shape: BoxShape.circle,
            ),
          ),
          Expanded(child: Text(text, style: AppTokens.body)),
        ],
      ),
    );
  }
}
