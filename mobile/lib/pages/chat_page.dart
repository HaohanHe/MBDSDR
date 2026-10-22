// ============================================================================
// ChatPage —— 流式聊天 UI（多会话：列表 / 新建 / 切换 / 删除 + 诚实空态）
// ----------------------------------------------------------------------------
// * 未配置 key：诚实空态 + 去设置按钮，输入框禁用（绝不 mock 输出）；
// * 已配置：用户气泡（accent）、assistant 气泡（card1 叠加）、内联工具 chip、
//   SelectableText 流式渲染、HH:mm 时间戳、错误气泡可重试；
// * 多会话：顶部会话栏（≡ 抽屉 + 当前标题 + 新建）；抽屉列会话、切、删；
//   持久化走 ChatSessionStore（与桌面 JSON 同形）；store 未注入时退化为
//   纯内存单会话（不假接持久化）；AI 轮的真实工具步骤随 assistant 行落盘
//   （steps），重启/切会话后重建 TaskProgressView chip，历史不丢过程；
// * 流式行：partial 实时显示（▋光标）、完成时固化且只固化一次（去重）、
//   错误时保留已流式内容不擦除；
// * 全部弹性布局，横屏可用；只用 AppTokens 常量。
// ============================================================================

import 'dart:async';

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../l10n/gen/app_localizations.dart';
import '../models/chat_message.dart';
import '../models/task_step.dart';
import '../services/ai_client.dart';
import '../services/chat_session_store.dart';
import '../widgets/empty_state.dart';
import '../widgets/task_progress.dart';
import '../widgets/task_templates_bar.dart';
import 'tools_catalog_page.dart';

/// UI 层的工具调用 chip。
class _UiToolCall {
  final String name;
  final String argumentsPreview;
  String result = '';
  bool done = false;

  _UiToolCall({
    required this.name,
    required this.argumentsPreview,
  });
}

/// UI 层的一条气泡消息。
class _UiMessage {
  final ChatRole role;
  String text;
  final DateTime time;
  bool streaming = false;
  String? error;
  final List<_UiToolCall> toolCalls = <_UiToolCall>[];

  /// 从持久化历史恢复的任务步骤快照（[TaskStep]，仅重载行有；实时行用
  /// [toolCalls] 现算）。二者择一渲染 TaskProgressView，语义一致。
  final List<TaskStep> restoredSteps = <TaskStep>[];

  /// 折叠占位标记（展示层）：true 时这不是一条气泡，而是一行居中的
  /// 「已折叠 N 轮早期对话」诚实说明——它只说明上送层发生了折叠，不删任何气泡/原文。
  final bool isFoldMarker;

  /// 折叠标记被折走的 user 轮次数（[isFoldMarker] = true 时有意义）。
  final int foldedTurns;

  _UiMessage({
    required this.role,
    required this.text,
    required this.time,
    this.isFoldMarker = false,
    this.foldedTurns = 0,
  });

  /// 构造一条居中折叠占位说明（不渲染气泡）。
  factory _UiMessage.foldMarker(int foldedTurns) => _UiMessage(
        role: ChatRole.system,
        text: '',
        time: DateTime.now(),
        isFoldMarker: true,
        foldedTurns: foldedTurns,
      );
}

class ChatPage extends StatefulWidget {
  const ChatPage({
    super.key,
    required this.clientFactory,
    required this.isConfigured,
    this.manualMode = false,
    this.onOpenSettings,
    this.store,
  });

  /// 每次发送时构造一个新的 AiClient（外壳注入 key / 工具）。
  final AiClient Function() clientFactory;

  /// 是否已配置 API key；false 时展示诚实空态并禁用输入。
  final bool isConfigured;

  /// 当前是否「手动模式」。true 时 AI 可对话但动作不执行，
  /// 输入区上方安静标注，让用户诚实知道当前模式。
  final bool manualMode;

  /// 点击「去设置」回调。
  final VoidCallback? onOpenSettings;

  /// 多会话持久化存储。未注入（如外壳导航单测）时退化为纯内存单会话，
  /// 不渲染会话栏 / 抽屉，不假接磁盘。
  final ChatSessionStore? store;

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final List<_UiMessage> _messages = <_UiMessage>[];
  final List<ChatMessage> _history = <ChatMessage>[];

  StreamSubscription<ChatStreamEvent>? _sub;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _loadCurrentSession();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  ChatSessionStore? get _store => widget.store;

  /// 把存储里当前会话的已固化行载入 UI 气泡 + LLM 上下文。
  ///
  /// 恢复两层：纯文本 user/assistant 行（kind=chat）进气泡与上送上下文；
  /// assistant 行携带的 steps 任务步骤快照重建 TaskProgressView chip
  /// （历史里的工具调用过程不再随重启消失，见 ChatSessionStore 互操作边界注释）。
  void _loadCurrentSession() {
    final ChatSessionStore? store = _store;
    _messages.clear();
    _history.clear();
    if (store == null) return;
    for (final SessionLine line in store.currentMessages()) {
      final ChatRole role = line.role == 'user' ? ChatRole.user : ChatRole.assistant;
      final DateTime time = line.tsMs > 0
          ? DateTime.fromMillisecondsSinceEpoch(line.tsMs)
          : DateTime.fromMillisecondsSinceEpoch(0);
      final _UiMessage m = _UiMessage(role: role, text: line.content, time: time);
      // 恢复步骤快照：落盘已是收尾态，running 不会出现在磁盘上；若出现
      // （异常数据）也不渲染成永久旋转的假进度，诚实转为「未收到结果」。
      for (final TaskStep s in line.steps) {
        m.restoredSteps.add(s.status == TaskStepStatus.running
            ? TaskStep.interrupted(
                tool: s.tool, argumentsPreview: s.argumentsPreview)
            : s);
      }
      _messages.add(m);
      _history.add(ChatMessage(role: role, content: line.content, time: time));
    }
  }

  /// 切到指定会话（忙时禁止切换，避免流目标漂移）。
  void _switchSession(String id) {
    final ChatSessionStore? store = _store;
    if (store == null || _busy) return;
    if (id == store.currentId) return;
    store.setCurrent(id);
    setState(_loadCurrentSession);
    _scrollToBottom();
  }

  void _newSession() {
    final ChatSessionStore? store = _store;
    if (store == null || _busy) return;
    store.createSession();
    setState(_loadCurrentSession);
  }

  void _deleteSession(String id) {
    final ChatSessionStore? store = _store;
    if (store == null || _busy) return;
    store.deleteSession(id);
    setState(_loadCurrentSession);
  }

  // ------------------------------------------------------------- 手动压缩（G1）
  /// 当前 LLM 上下文的估算占用字符（所有 content 长度之和，与 compactHistory 同口径）。
  int get _historyCharsUsed {
    int t = 0;
    for (final ChatMessage m in _history) {
      t += m.content.length;
    }
    return t;
  }

  /// 真实跑一次 compactHistory，决定「压缩上下文」按钮的可用态与 tooltip。
  ///
  /// 诚实原则：只有**真能折出东西**（didCompact=true）才可点；
  /// 无历史 / 预算内 / user 轮次不足 → 禁用并给 tooltip 说清原因，绝不渲染假动作。
  ({bool canCompact, ContextCompaction compaction}) get _compactPlan {
    if (_busy || _history.isEmpty) {
      return (
        canCompact: false,
        compaction: const ContextCompaction(
          messages: <ChatMessage>[],
          didCompact: false,
          compressedUserTurns: 0,
          charsUsed: 0,
        ),
      );
    }
    final ContextCompaction c = AiClient.compactHistory(_history);
    return (canCompact: c.didCompact, compaction: c);
  }

  /// 按钮 tooltip：禁用时诚实说明原因（无历史 / 未达门槛），启用时说明机制。
  String _compactTooltip(
    AppLocalizations l10n, {
    required bool isEmpty,
    required bool canCompact,
  }) {
    if (isEmpty) return l10n.chatCompactEmpty;
    if (!canCompact) {
      return l10n.chatCompactBelow(
        _historyCharsUsed,
        AppTokens.kAiContextBudgetChars,
      );
    }
    return l10n.chatCompactReady;
  }

  /// 手动压缩：真实调用 compactHistory（G1 规则法折叠，不二次 LLM、不 mock）。
  ///
  /// 效果只作用于两层：
  ///   * 上送层——把 `_history` 换成折叠版（占位 system + 最近 N 轮原文），
  ///     下一次 send 就上送更小的提示；
  ///   * 展示层——在折叠边界插入一行诚实占位说明（真实折叠条数）。
  /// 绝不删落盘原文：ChatSessionStore 里的完整会话记录原样保留，气泡也不删。
  void _onManualCompact() {
    if (_busy) return;
    final ContextCompaction c = AiClient.compactHistory(_history);
    if (!c.didCompact) return; // 禁用态已拦截，双保险。
    setState(() {
      _history
        ..clear()
        ..addAll(c.messages);
      _messages.insert(_foldMarkerInsertIndex(), _UiMessage.foldMarker(c.compressedUserTurns));
    });
    _scrollToBottom();
  }

  /// 把折叠边界映射回 `_messages`：convo（剥前导 system 后）从「上一个折叠标记之后」
  /// 与气泡一一对齐；新标记插在被折走的最后一条气泡之后。规则与 compactHistory 共用。
  int _foldMarkerInsertIndex() {
    final int boundary = AiClient.findCompactBoundary(
      _history,
      keepRecentUserTurns: AppTokens.kAiKeepRecentUserTurns,
    );
    int sysEnd = 0;
    while (sysEnd < _history.length &&
        _history[sysEnd].role == ChatRole.system) {
      sysEnd++;
    }
    int lastMarker = -1;
    for (int i = 0; i < _messages.length; i++) {
      if (_messages[i].isFoldMarker) lastMarker = i;
    }
    int insertAt = lastMarker + (boundary - sysEnd) + 1;
    if (insertAt < 0) insertAt = 0;
    if (insertAt > _messages.length) insertAt = _messages.length;
    return insertAt;
  }

  /// 重命名会话：弹一个带输入框的对话框，预填当前标题；确认后写回 store。
  /// 重命名入口（铅笔 IconButton）默认 48x48 触控，≥ AppTokens.touchMin。
  Future<void> _renameSession(BuildContext context, String id) async {
    final ChatSessionStore? store = _store;
    if (store == null || _busy) return;
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    SessionInfo? target;
    for (final SessionInfo s in store.sessions) {
      if (s.id == id) {
        target = s;
        break;
      }
    }
    if (target == null) return;
    final TextEditingController controller =
        TextEditingController(text: target.title);
    final String? newTitle = await showDialog<String>(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: Text(l10n.chatRenameTitle, style: AppTokens.sectionTitle),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: AppTokens.body,
          cursorColor: AppTokens.accent,
          decoration: InputDecoration(
            hintText: l10n.chatRenameHint,
            isDense: true,
          ),
          onSubmitted: (String v) => Navigator.of(dialogContext).pop(v),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text(l10n.chatCancel),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.of(dialogContext).pop(controller.text),
            child: Text(l10n.chatSave),
          ),
        ],
      ),
    );
    controller.dispose();
    if (newTitle == null) return;
    store.renameSession(id, newTitle);
    // 重命名在抽屉里触发：关掉抽屉回到对话页，并刷新会话栏标题。
    if (context.mounted) Navigator.of(context).maybePop();
    if (mounted) setState(() {});
  }

  // ------------------------------------------------------------- 发送 / 重试
  /// 模板入口：把草稿填入输入框（可编辑），不自动发送。
  void _onPickTemplate(String draft) {
    if (_busy) return;
    setState(() {
      _controller.text = draft;
      _controller.selection = TextSelection.collapsed(
        offset: _controller.text.length,
      );
    });
  }

  Future<void> _send() async {
    final String text = _controller.text.trim();
    if (text.isEmpty || _busy || !widget.isConfigured) return;

    final DateTime now = DateTime.now();
    final _UiMessage userMsg = _UiMessage(
      role: ChatRole.user,
      text: text,
      time: now,
    );
    final _UiMessage assistantMsg = _UiMessage(
      role: ChatRole.assistant,
      text: '',
      time: DateTime.now(),
    )..streaming = true;

    setState(() {
      _messages.add(userMsg);
      _history.add(ChatMessage(
        role: ChatRole.user,
        content: text,
        time: now,
      ));
      _persistUserLine(text, now);
      _messages.add(assistantMsg);
      _busy = true;
      _controller.clear();
    });
    _scrollToBottom();

    final AiClient client = widget.clientFactory();
    _sub = client.complete(history: List<ChatMessage>.from(_history)).listen(
      (ChatStreamEvent event) {
        if (!mounted) return;
        setState(() => _handleEvent(event, assistantMsg));
        _scrollToBottom();
      },
      onError: (Object e) {
        if (!mounted) return;
        setState(() {
          assistantMsg.streaming = false;
          assistantMsg.error = _errText(e);
          // 错误保留已流式内容：不擦除 partial，把它固化到存储。
          _finalizeAssistantOnError(assistantMsg);
          _busy = false;
        });
      },
      onDone: () {
        if (!mounted) return;
        setState(() {
          assistantMsg.streaming = false;
          // 正常收尾才把 assistant 行固化；且仅一次（onDone 只触发一次）。
          // 纯工具轮（有步骤、无正文）也固化——步骤快照不能随重启丢失；
          // 空正文不进上送上下文（无信息量）。
          if (assistantMsg.error == null &&
              (assistantMsg.text.isNotEmpty ||
                  assistantMsg.toolCalls.isNotEmpty)) {
            if (assistantMsg.text.isNotEmpty) {
              _history.add(ChatMessage(
                role: ChatRole.assistant,
                content: assistantMsg.text,
                time: assistantMsg.time,
              ));
            }
            _persistAssistantLine(assistantMsg, incomplete: false);
          }
          _busy = false;
        });
      },
    );
  }

  Future<void> _retry() async {
    if (_busy) return;
    // 找到最后一条 assistant 错误气泡，删掉后重跑当前 history。
    int idx = _messages.length - 1;
    while (idx >= 0 && _messages[idx].error == null) {
      idx--;
    }
    if (idx < 0) return;
    final _UiMessage failed = _messages[idx];
    // 重试前先把上一条错误行从存储里撤掉（它是半截），重跑成功后重新固化。
    setState(() {
      _messages.removeAt(idx);
      failed.error = null;
      failed.text = '';
      failed.streaming = true;
      failed.toolCalls.clear();
      // 重跑即重算步骤：旧快照（含恢复行）不残留，避免新旧步骤混排。
      failed.restoredSteps.clear();
      _messages.add(failed);
      _busy = true;
    });
    _scrollToBottom();

    final AiClient client = widget.clientFactory();
    _sub = client.complete(history: List<ChatMessage>.from(_history)).listen(
      (ChatStreamEvent event) {
        if (!mounted) return;
        setState(() => _handleEvent(event, failed));
        _scrollToBottom();
      },
      onError: (Object e) {
        if (!mounted) return;
        setState(() {
          failed.streaming = false;
          failed.error = _errText(e);
          _finalizeAssistantOnError(failed);
          _busy = false;
        });
      },
      onDone: () {
        if (!mounted) return;
        setState(() {
          failed.streaming = false;
          // 与 _send.onDone 同规则：正文或工具步骤任一非空即固化。
          if (failed.error == null &&
              (failed.text.isNotEmpty || failed.toolCalls.isNotEmpty)) {
            if (failed.text.isNotEmpty) {
              _history.add(ChatMessage(
                role: ChatRole.assistant,
                content: failed.text,
                time: failed.time,
              ));
            }
            _persistAssistantLine(failed, incomplete: false);
          }
          _busy = false;
        });
      },
    );
  }

  // ------------------------------------------------------------- 持久化缝
  /// 收尾时把 UI 层工具调用记录固化成 TaskStep 快照（随会话历史落盘）。
  ///
  /// 诚实边界：收尾时仍未收到结果的调用（流被中断）不落成 running——
  /// 那会让恢复后的 chip 永久旋转假进度；用 [TaskStep.interrupted] 落为
  /// 「未收到结果」失败态，绝不臆造执行结果。
  List<TaskStep> _stepsForPersist(_UiMessage m) {
    return m.toolCalls.map((_UiToolCall c) {
      final TaskStep step = TaskStep.fromCall(
        tool: c.name,
        argumentsPreview: c.argumentsPreview,
        done: c.done,
        result: c.result,
      );
      return step.status == TaskStepStatus.running
          ? TaskStep.interrupted(
              tool: c.name, argumentsPreview: c.argumentsPreview)
          : step;
    }).toList(growable: false);
  }

  /// 落盘一条 user 行，并把当前会话标记为「流式回复进行中」。
  void _persistUserLine(String text, DateTime now) {
    final ChatSessionStore? store = _store;
    if (store == null) return;
    store.appendLine(
      store.currentId,
      SessionLine(
        role: 'user',
        content: text,
        kind: 'chat',
        tsMs: now.millisecondsSinceEpoch,
      ),
    );
    store.setIncomplete(store.currentId, true);
  }

  /// 正常收尾：固化 assistant 行（含任务步骤快照），清 incomplete
  /// （去重：只在 onDone 调一次）。
  void _persistAssistantLine(_UiMessage m, {required bool incomplete}) {
    final ChatSessionStore? store = _store;
    if (store == null) return;
    store.appendLine(
      store.currentId,
      SessionLine(
        role: 'assistant',
        content: m.text,
        kind: 'chat',
        tsMs: m.time.millisecondsSinceEpoch,
        steps: _stepsForPersist(m),
      ),
    );
    store.setIncomplete(store.currentId, incomplete);
  }

  /// 出错：保留已流式 partial 文本并固化（不清空），结束 incomplete 标记。
  void _finalizeAssistantOnError(_UiMessage m) {
    final ChatSessionStore? store = _store;
    if (store == null) return;
    final List<TaskStep> steps = _stepsForPersist(m);
    if (m.text.isNotEmpty || steps.isNotEmpty) {
      store.appendLine(
        store.currentId,
        SessionLine(
          role: 'assistant',
          content: m.text,
          kind: 'chat',
          tsMs: m.time.millisecondsSinceEpoch,
          steps: steps,
        ),
      );
    }
    store.setIncomplete(store.currentId, false);
  }

  void _handleEvent(ChatStreamEvent event, _UiMessage assistantMsg) {
    switch (event) {
      case ChatTextEvent(:final String delta):
        assistantMsg.text += delta;
      case ToolCallStartedEvent(:final String name, :final String argumentsPreview):
        assistantMsg.toolCalls.add(
          _UiToolCall(name: name, argumentsPreview: argumentsPreview),
        );
      case ToolCallFinishedEvent(:final String name, :final String result):
        for (int i = assistantMsg.toolCalls.length - 1; i >= 0; i--) {
          final _UiToolCall c = assistantMsg.toolCalls[i];
          if (!c.done && c.name == name) {
            c.result = result;
            c.done = true;
            break;
          }
        }
      case AssistantTurnDoneEvent():
        assistantMsg.streaming = false;
      case ContextCompactedEvent(
          :final int compressedUserTurns,
          :final int charsUsed,
          :final int budgetChars,
        ):
        // 自动压缩真实发生：与手动压缩共用统一纯函数出文案，弹一次瞬态提示。
        // 不动 _history 本体（下一次 send 仍按阈值自动折）；这里只做可观测告知。
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              compactionStatusText(
                compressedTurns: compressedUserTurns,
                charsUsed: charsUsed,
                budgetChars: budgetChars,
              ),
            ),
            duration: AppTokens.kAiCompactionNotice,
          ),
        );
      case AiErrorEvent(:final String message):
        assistantMsg.streaming = false;
        assistantMsg.error = message;
        // 流内错误事件：保留已流式 partial 并固化（不擦除）。
        // 与 .listen(onError:) 互斥——本路径触发时 onError 回调不触发。
        _finalizeAssistantOnError(assistantMsg);
    }
  }

  String _errText(Object e) =>
      e is AiException ? e.message : 'Network error: $e';

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: AppTokens.animShort,
        curve: Curves.easeOut,
      );
    });
  }

  // ------------------------------------------------------------- 构建
  @override
  Widget build(BuildContext context) {
    if (!widget.isConfigured) {
      return Scaffold(
        backgroundColor: AppTokens.bgMain,
        body: SafeArea(child: _buildUnconfigured()),
      );
    }
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      drawer: _store == null ? null : _buildSessionDrawer(),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            if (_store != null) _buildSessionBar(),
            Expanded(child: _buildList()),
            if (widget.manualMode) _buildModeStrip(),
            // 任务模板入口：空闲时显示；点击仅把可编辑草稿填入输入框，不自动发送。
            if (!_busy)
              TaskTemplatesBar(onSelect: _onPickTemplate),
            _buildInputBar(),
          ],
        ),
      ),
    );
  }

  /// 会话栏：≡ 抽屉 / 当前标题 / 压缩上下文 / 新建会话。全部走 touchMin 触控目标。
  Widget _buildSessionBar() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final String title = _store?.current?.title ?? '';
    final ({bool canCompact, ContextCompaction compaction}) plan = _compactPlan;
    final bool historyEmpty = _history.isEmpty;
    return Container(
      height: AppTokens.topBarH,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.spacingS),
      decoration: const BoxDecoration(
        color: AppTokens.bgBar,
        border: Border(bottom: BorderSide(color: AppTokens.cardEdge)),
      ),
      child: Row(
        children: <Widget>[
          // Builder 取得 ChatPage 自身 Scaffold 内部的 context，才能 openDrawer
          // （State.context 在 Scaffold 之上，会命中外层 Scaffold）。
          Builder(
            builder: (BuildContext inner) => IconButton(
              icon: const Icon(Icons.menu),
              tooltip: l10n.chatSessionListTooltip,
              color: AppTokens.textSecondary,
              onPressed: () => Scaffold.of(inner).openDrawer(),
            ),
          ),
          Expanded(
            child: Text(
              title.isEmpty ? l10n.chatSessionTitleFallback : title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTokens.sectionTitle,
            ),
          ),
          // 手动压缩上下文：IconButton 默认 48x48 触控（≥ touchMin=44）。
          // 无历史 / 未达压缩门槛 → 禁用 + 诚实 tooltip（不渲染假动作）。
          IconButton(
            icon: const Icon(Icons.unfold_less_outlined),
            tooltip: _compactTooltip(l10n,
                isEmpty: historyEmpty, canCompact: plan.canCompact),
            color: plan.canCompact
                ? AppTokens.accent
                : AppTokens.textAt(AppTokens.textAlphaFaint),
            onPressed: plan.canCompact ? _onManualCompact : null,
          ),
          // AI 工具能力清单（只读对照页）入口。
          IconButton(
            icon: const Icon(Icons.build_circle_outlined),
            tooltip: l10n.chatToolsTooltip,
            color: AppTokens.textSecondary,
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const ToolsCatalogPage(),
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.add_comment_outlined),
            tooltip: l10n.chatNewSessionTooltip,
            color: AppTokens.accent,
            onPressed: _busy ? null : _newSession,
          ),
        ],
      ),
    );
  }

  /// 会话抽屉：顶部「新建会话」，下列全部会话（含未完成角标 + 删除）。
  Widget _buildSessionDrawer() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final ChatSessionStore store = _store!;
    final List<SessionInfo> sessions = store.sessions;
    return Drawer(
      backgroundColor: AppTokens.bgMain,
      child: SafeArea(
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.all(AppTokens.spacingM),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _busy ? null : _newSession,
                  icon: const Icon(Icons.add),
                  label: Text(l10n.chatNewSessionButton),
                ),
              ),
            ),
            const Divider(height: 1, color: AppTokens.divider),
            Expanded(
              child: ListView.builder(
                itemCount: sessions.length,
                itemBuilder: (BuildContext context, int i) {
                  final SessionInfo s = sessions[i];
                  final bool selected = s.id == store.currentId;
                  return ListTile(
                    minTileHeight: AppTokens.touchMin,
                    selected: selected,
                    selectedTileColor: AppTokens.selectedFill,
                    leading: Icon(
                      Icons.chat_bubble_outline,
                      size: AppTokens.iconSizeInlineLg,
                      color: selected
                          ? AppTokens.selectedText
                          : AppTokens.textAt(AppTokens.textAlphaTertiary),
                    ),
                    title: Text(
                      s.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTokens.body.copyWith(
                        color: selected ? AppTokens.selectedText : AppTokens.textPrimary,
                      ),
                    ),
                    subtitle: s.incomplete
                        ? Text(
                            l10n.chatSessionIncomplete,
                            style: AppTokens.auxiliary.copyWith(
                              fontSize: AppTokens.annotationFontSize,
                              color: AppTokens.warning,
                            ),
                          )
                        : null,
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        // 重命名入口：IconButton 默认 48x48，≥ AppTokens.touchMin(44)。
                        IconButton(
                          icon: const Icon(Icons.drive_file_rename_outline),
                          tooltip: l10n.chatRenameTooltip,
                          color: AppTokens.textAt(AppTokens.textAlphaTertiary),
                          onPressed:
                              _busy ? null : () => _renameSession(context, s.id),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete_outline),
                          tooltip: l10n.chatDeleteTooltip,
                          color: AppTokens.textAt(AppTokens.textAlphaTertiary),
                          onPressed: _busy ? null : () => _deleteSession(s.id),
                        ),
                      ],
                    ),
                    onTap: _busy ? null : () {
                      Navigator.of(context).pop();
                      _switchSession(s.id);
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// 手动模式安静标注：一行次要文字，不使用警示色。
  Widget _buildModeStrip() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingL,
        vertical: AppTokens.spacingS,
      ),
      decoration: const BoxDecoration(
        border: Border(
          top: BorderSide(color: AppTokens.cardEdge),
        ),
      ),
      child: Text(
        l10n.chatManualModeStrip,
        style: AppTokens.auxiliary.copyWith(
          fontSize: AppTokens.annotationFontSize,
          color: AppTokens.textAt(AppTokens.textAlphaTertiary),
        ),
      ),
    );
  }

  Widget _buildUnconfigured() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return EmptyState(
      icon: Icons.key_outlined,
      title: l10n.chatUnconfiguredTitle,
      message: l10n.chatUnconfiguredMessage,
      actionLabel: l10n.chatOpenSettings,
      onAction: widget.onOpenSettings,
    );
  }

  Widget _buildList() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    if (_messages.isEmpty) {
      // 有存储时：空会话引导「新建会话」；纯内存模式退化为一行提示。
      if (_store != null) {
        return EmptyState(
          icon: Icons.chat_bubble_outline,
          title: l10n.chatEmptyTitle,
          message: l10n.chatEmptyMessage,
          actionLabel: l10n.chatNewSessionButton,
          onAction: _busy ? null : _newSession,
        );
      }
      return Center(
        child: Text(
          l10n.chatEmptyTitle,
          style: AppTokens.auxiliary
              .copyWith(color: AppTokens.textAt(AppTokens.textAlphaTertiary)),
        ),
      );
    }
    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingL),
      itemCount: _messages.length,
      itemBuilder: (BuildContext context, int i) => _buildBubble(_messages[i]),
    );
  }

  /// 气泡里渲染的步骤列表：实时行用 [toolCalls] 现算，恢复行用
  /// [restoredSteps] 快照——两者语义一致（tool/args/status/summary），
  /// 历史恢复的工具步骤随会话落盘后在这里重建 TaskProgressView chip。
  List<TaskStep> _stepsForDisplay(_UiMessage m) {
    if (m.toolCalls.isNotEmpty) {
      return m.toolCalls
          .map((_UiToolCall c) => TaskStep.fromCall(
                tool: c.name,
                argumentsPreview: c.argumentsPreview,
                done: c.done,
                result: c.result,
              ))
          .toList(growable: false);
    }
    return m.restoredSteps;
  }

  Widget _buildBubble(_UiMessage m) {
    // 折叠占位标记：不是气泡，一行居中次要色说明（真实折叠轮次）。
    if (m.isFoldMarker) {
      final AppLocalizations l10n = AppLocalizations.of(context)!;
      return Padding(
        padding: const EdgeInsets.symmetric(
          vertical: AppTokens.spacingS,
          horizontal: AppTokens.spacingL,
        ),
        child: Center(
          child: Text(
            l10n.chatFoldMarker(m.foldedTurns),
            textAlign: TextAlign.center,
            style: AppTokens.auxiliary.copyWith(
              fontSize: AppTokens.annotationFontSize,
              color: AppTokens.textAt(AppTokens.textAlphaTertiary),
            ),
          ),
        ),
      );
    }

    final bool isUser = m.role == ChatRole.user;
    final bool isError = m.error != null;
    // 气泡最大宽度占屏比：移动端聊天通用 0.78，避免长气泡撑满整行。
    final double maxW = MediaQuery.of(context).size.width * 0.78;

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(
          vertical: AppTokens.spacingS,
          horizontal: AppTokens.spacingL,
        ),
        constraints: BoxConstraints(maxWidth: maxW),
        padding: const EdgeInsets.all(AppTokens.spacingL),
        decoration: isUser
            // 用户气泡：accent 12% 填充 / 30% 描边（对话层专属低饱和表面）。
            ? BoxDecoration(
                color: AppTokens.accent.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(AppTokens.radiusCard),
                border: Border.all(
                  color: AppTokens.accent.withValues(alpha: 0.30),
                ),
              )
            : AppTokens.cardDecoration(),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            if (isError)
              _buildErrorBody(m)
            else if (isUser)
              SelectableText(m.text, style: AppTokens.body)
            else ...<Widget>[
                if (_stepsForDisplay(m).isNotEmpty)
                  TaskProgressView(steps: _stepsForDisplay(m)),
                SelectableText.rich(
                  TextSpan(
                    children: <InlineSpan>[
                      TextSpan(text: m.text, style: AppTokens.body),
                      if (m.streaming && m.text.isNotEmpty)
                        TextSpan(
                          text: ' ▋',
                          style: AppTokens.body.copyWith(color: AppTokens.accent),
                        ),
                    ],
                  ),
                ),
              ],
            const SizedBox(height: AppTokens.spacingS),
            Text(
              _formatTime(m.time),
              style: AppTokens.auxiliary.copyWith(
                fontSize: AppTokens.annotationFontSize,
                color: AppTokens.textAt(AppTokens.textAlphaTertiary),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildErrorBody(_UiMessage m) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        // 出错也如实展示本轮真实工具步骤（已执行的不因出错而藏起来）。
        if (_stepsForDisplay(m).isNotEmpty) ...<Widget>[
          TaskProgressView(steps: _stepsForDisplay(m)),
        ],
        // 错误气泡保留已流式内容：先展示 partial 正文，再展示错误说明。
        if (m.text.isNotEmpty) ...<Widget>[
          SelectableText(m.text, style: AppTokens.body),
          const SizedBox(height: AppTokens.spacingS),
        ],
        Row(
          children: <Widget>[
            const Icon(Icons.error_outline,
                size: AppTokens.iconSizeInline, color: AppTokens.danger),
            const SizedBox(width: AppTokens.spacingS),
            Expanded(
              child: Text(
                m.error!,
                style: AppTokens.auxiliary.copyWith(color: AppTokens.danger),
              ),
            ),
          ],
        ),
        const SizedBox(height: AppTokens.spacingS),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton(
            onPressed: _busy ? null : _retry,
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(
                horizontal: AppTokens.spacingM,
                vertical: AppTokens.spacingS,
              ),
              // 重试按钮紧凑高度：气泡内辅助操作，不占满 touchMin。
              minimumSize: const Size(0, 28),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(l10n.chatRetry),
          ),
        ),
      ],
    );
  }

  Widget _buildInputBar() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return Container(
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: const BoxDecoration(
        color: AppTokens.bgBar,
        border: Border(
          top: BorderSide(color: AppTokens.cardEdge),
        ),
      ),
      child: SafeArea(
        top: false,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: <Widget>[
            Expanded(
              child: Container(
                constraints: const BoxConstraints(minHeight: AppTokens.touchMin),
                decoration: AppTokens.cardDecoration(),
                padding: const EdgeInsets.symmetric(
                  horizontal: AppTokens.spacingM,
                ),
                alignment: Alignment.center,
                child: TextField(
                  controller: _controller,
                  enabled: widget.isConfigured && !_busy,
                  minLines: 1,
                  maxLines: 5,
                  textInputAction: TextInputAction.newline,
                  // 输入即重建，让发送按钮的启用态跟随草稿是否非空。
                  onChanged: (_) => setState(() {}),
                  style: AppTokens.body,
                  cursorColor: AppTokens.accent,
                  decoration: InputDecoration(
                    border: InputBorder.none,
                    hintText: l10n.chatInputHint,
                    hintStyle: const TextStyle(color: AppTokens.textSecondary),
                  ),
                ),
              ),
            ),
            const SizedBox(width: AppTokens.spacingM),
            IconButton(
              onPressed: (widget.isConfigured && !_busy && _controller.text.trim().isNotEmpty)
                  ? () => unawaited(_send())
                  : null,
              icon: const Icon(Icons.send),
              color: AppTokens.accent,
              disabledColor: AppTokens.textAt(AppTokens.textAlphaFaint),
            ),
          ],
        ),
      ),
    );
  }

  String _formatTime(DateTime t) {
    final String h = t.hour.toString().padLeft(2, '0');
    final String m = t.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }
}
