// 任务进度步骤：把 AI 真实 function-calling 调用记录整理成有序步骤状态。
//
// 诚实边界：状态只由「真实工具执行结果」推导，绝不预填/伪造步骤。
//  - running ：工具已开始、尚未收到结果（流式 ToolCallStarted）。
//  - success ：已完成且结果表明成功（{ok:true}）。
//  - failed  ：已完成但结果表明失败/被 gate（{ok:false} / 手动模式 gated / Error）。
enum TaskStepStatus { running, success, failed }

/// 一步真实工具调用的展示模型。
class TaskStep {
  /// 工具名（如 set_frequency / get_status）。
  final String tool;

  /// 参数预览（来自模型流式分片，可能截断）。
  final String argumentsPreview;

  final TaskStepStatus status;

  /// 摘要：成功/失败时取真实结果的简短文本。
  final String summary;

  const TaskStep({
    required this.tool,
    required this.argumentsPreview,
    required this.status,
    required this.summary,
  });

  /// 工具刚被调用、等待结果。
  factory TaskStep.running({
    required String tool,
    required String argumentsPreview,
  }) {
    return TaskStep(
      tool: tool,
      argumentsPreview: argumentsPreview,
      status: TaskStepStatus.running,
      summary: '',
    );
  }

  /// 工具已返回结果；据真实结果文本分类成功/失败。
  factory TaskStep.finished({
    required String tool,
    required String argumentsPreview,
    required String result,
  }) {
    final bool failed = isFailureResult(result);
    return TaskStep(
      tool: tool,
      argumentsPreview: argumentsPreview,
      status: failed ? TaskStepStatus.failed : TaskStepStatus.success,
      summary: summarizeResult(result),
    );
  }

  /// 由 UI 层的工具调用记录（含 done 标记与 result）构造一步。
  factory TaskStep.fromCall({
    required String tool,
    required String argumentsPreview,
    required bool done,
    required String result,
  }) {
    if (!done) {
      return TaskStep.running(tool: tool, argumentsPreview: argumentsPreview);
    }
    return TaskStep.finished(
      tool: tool,
      argumentsPreview: argumentsPreview,
      result: result,
    );
  }
}

/// 判断一次真实工具返回是否为失败。覆盖三类：
///  1) 工具 JSON 里显式 {ok:false}（含手动模式 gated:true）；
///  2) 执行层抛出的 'Error: ...' 文本；
///  3) 未注册工具 / 参数非法的 Error 文案。
bool isFailureResult(String result) {
  final String s = result.trim();
  if (s.isEmpty) return false;
  if (s.startsWith('Error')) return true;
  // 容忍空格：{"ok": false} / {"ok":false,...}。
  final RegExp okFalse = RegExp(r'"ok"\s*:\s*false');
  return okFalse.hasMatch(s);
}

/// 从真实结果提取一行短摘要（成功读 ok 字段，失败读 error/message）。
String summarizeResult(String result) {
  final String s = result.trim();
  if (s.isEmpty) return '';
  // 尝试提取 error / message 字段；失败则截断原文。
  final RegExp errorRe = RegExp(r'"(?:error|message)"\s*:\s*"([^"]*)"');
  final RegExpMatch? m = errorRe.firstMatch(s);
  if (m != null && m.group(1)!.isNotEmpty) {
    return _truncate(m.group(1)!, 48);
  }
  return _truncate(s, 48);
}

String _truncate(String s, int n) =>
    s.length > n ? '${s.substring(0, n)}…' : s;
