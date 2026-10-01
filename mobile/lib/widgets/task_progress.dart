// 任务进度视图：把 AI 真实多步工具调用渲染成有序步骤列表。
//
// 全部走 AppTokens；无步骤时返回 SizedBox.shrink（诚实空态，不画骨架/占位）。
import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/task_step.dart';

/// 有序步骤进度条：编号 + 工具名 + 状态图标 + 一行摘要。
/// 状态色：运行=warning（旋转）、成功=success、失败=danger。
class TaskProgressView extends StatelessWidget {
  final List<TaskStep> steps;
  const TaskProgressView({super.key, required this.steps});

  @override
  Widget build(BuildContext context) {
    if (steps.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.spacingS),
      padding: const EdgeInsets.all(AppTokens.spacingS),
      decoration: BoxDecoration(
        color: AppTokens.card1,
        borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.only(left: AppTokens.spacingS),
            child: Text(
              '执行步骤',
              style: AppTokens.auxiliary.copyWith(
                fontSize: AppTokens.annotationFontSize,
                color: AppTokens.textAt(AppTokens.textAlphaTertiary),
              ),
            ),
          ),
          const SizedBox(height: AppTokens.spacingS),
          for (int i = 0; i < steps.length; i++)
            _StepRow(index: i + 1, step: steps[i]),
        ],
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  final int index;
  final TaskStep step;
  const _StepRow({required this.index, required this.step});

  Color get _color => switch (step.status) {
        TaskStepStatus.running => AppTokens.warning,
        TaskStepStatus.success => AppTokens.success,
        TaskStepStatus.failed => AppTokens.danger,
      };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingS),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          // 状态图标（运行=旋转）。
          _StatusIcon(status: step.status, color: _color),
          const SizedBox(width: AppTokens.spacingS),
          // 编号 + 工具名。
          SizedBox(
            width: 22,
            child: Text(
              '$index',
              style: AppTokens.mono.copyWith(
                fontSize: AppTokens.annotationFontSize,
                color: AppTokens.textSecondary,
              ),
            ),
          ),
          const SizedBox(width: AppTokens.spacingS),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  step.tool,
                  style: AppTokens.auxiliary.copyWith(
                    fontSize: AppTokens.annotationFontSize + 1,
                    fontWeight: AppTokens.weightMedium,
                    color: AppTokens.textPrimary,
                  ),
                ),
                if (step.summary.isNotEmpty)
                  Text(
                    step.summary,
                    style: AppTokens.auxiliary.copyWith(
                      fontSize: AppTokens.annotationFontSize,
                      color: _color,
                    ),
                  )
                else if (step.status == TaskStepStatus.running &&
                    step.argumentsPreview.isNotEmpty)
                  Text(
                    step.argumentsPreview,
                    style: AppTokens.auxiliary.copyWith(
                      fontSize: AppTokens.annotationFontSize,
                      color: AppTokens.textAt(AppTokens.textAlphaTertiary),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusIcon extends StatelessWidget {
  final TaskStepStatus status;
  final Color color;
  const _StatusIcon({required this.status, required this.color});

  @override
  Widget build(BuildContext context) {
    return switch (status) {
      TaskStepStatus.running => SizedBox(
          width: AppTokens.iconSizeInline,
          height: AppTokens.iconSizeInline,
          child: CircularProgressIndicator(
            strokeWidth: 1.6,
            valueColor: AlwaysStoppedAnimation<Color>(color),
          ),
        ),
      TaskStepStatus.success =>
        Icon(Icons.check, size: AppTokens.iconSizeInline, color: color),
      TaskStepStatus.failed =>
        Icon(Icons.close, size: AppTokens.iconSizeInline, color: color),
    };
  }
}
