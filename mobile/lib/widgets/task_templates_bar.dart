// 任务模板入口条：一排中性模板 chips。点击仅把「可编辑草稿」交给回调
// （由外层写入输入框），不自动发送。全部走 AppTokens。
import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/task_template.dart';

class TaskTemplatesBar extends StatelessWidget {
  /// 点击某模板：把草稿正文交给外层（通常写入输入框，等待用户编辑后发送）。
  final void Function(String draft) onSelect;

  /// 模板清单（注入便于测试；为空/未传则用内置中性模板）。
  final List<TaskTemplate>? templates;

  const TaskTemplatesBar({
    super.key,
    required this.onSelect,
    this.templates,
  });

  @override
  Widget build(BuildContext context) {
    final List<TaskTemplate> list = templates ?? builtinTaskTemplates();
    if (list.isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(
        AppTokens.spacingM,
        AppTokens.spacingS,
        AppTokens.spacingM,
        AppTokens.spacingS,
      ),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: AppTokens.cardEdge)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            '任务模板（点击填入草稿，可再修改）',
            style: AppTokens.auxiliary.copyWith(
              fontSize: AppTokens.annotationFontSize,
              color: AppTokens.textAt(AppTokens.textAlphaTertiary),
            ),
          ),
          const SizedBox(height: AppTokens.spacingS),
          Wrap(
            spacing: AppTokens.spacingS,
            runSpacing: AppTokens.spacingS,
            children: <Widget>[
              for (final TaskTemplate t in list)
                ActionChip(
                  label: Text(t.title),
                  tooltip: t.hint,
                  onPressed: () => onSelect(t.buildDraft()),
                  // chip 紧凑高度：模板入口，不占满 touchMin。
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  visualDensity: VisualDensity.compact,
                  labelStyle: AppTokens.auxiliary.copyWith(
                    fontSize: AppTokens.annotationFontSize + 1,
                    color: AppTokens.accent,
                  ),
                  side: const BorderSide(color: AppTokens.cardEdge),
                  backgroundColor: AppTokens.card1,
                ),
            ],
          ),
        ],
      ),
    );
  }
}
