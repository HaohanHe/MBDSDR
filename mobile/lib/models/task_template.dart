// 任务模板入口：中性、参数可编辑的 AI 任务草稿。
//
// 诚实边界：模板只生成一段「可编辑的用户输入草稿」，不自动执行、不硬编码任何
// 台名/呼号/频率偏好；真正的调谐仍由用户改完草稿后发送，走既有异步工具链。
// 所有示例参数都是中性占位，提示用户自行修改。

/// 一个任务模板的静态定义。
class TaskTemplate {
  /// 入口 chip 上的短标题。
  final String title;

  /// 占位说明（次要小字）。
  final String hint;

  /// 生成可编辑草稿正文（示例参数全部中性，等待用户改写）。
  final String Function() buildDraft;

  const TaskTemplate({
    required this.title,
    required this.hint,
    required this.buildDraft,
  });
}

/// 内置中性模板清单。顺序即入口顺序。
List<TaskTemplate> builtinTaskTemplates() => <TaskTemplate>[
      TaskTemplate(
        title: '扫频找信号',
        hint: '从起始频率步进扫描，发现强信号点',
        buildDraft: () =>
            '请帮我扫频找信号：从 144.000 MHz 开始，以 0.500 MHz 为步进，'
            '逐步用 set_frequency 调谐并用 get_status 观察；'
            '遇到明显强信号的频点，列出来告诉我。'
            '（起始频率、步进可按我的实际需要修改。）',
      ),
      TaskTemplate(
        title: '监听目标频率',
        hint: '调到指定频率并确认是否收到信号',
        buildDraft: () =>
            '请把接收机调到 145.000 MHz，解调模式设为 nfm，'
            '用 set_frequency / set_mode 调谐后调用 get_status 确认，'
            '然后告诉我当前是否收到信号。（频率与模式可修改。）',
      ),
    ];
