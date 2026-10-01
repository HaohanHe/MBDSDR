// ============================================================================
// ChatPage —— 流式聊天 UI
// ----------------------------------------------------------------------------
// * 未配置 key：诚实空态 + 去设置按钮，输入框禁用；
// * 已配置：用户气泡（accent）、assistant 气泡（card1 叠加）、内联工具 chip、
//   SelectableText 流式渲染、HH:mm 时间戳、错误气泡可重试；
// * 全部弹性布局，横屏可用；只用 AppTokens 常量。
// ============================================================================

import 'dart:async';

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/chat_message.dart';
import '../models/task_step.dart';
import '../services/ai_client.dart';
import '../widgets/task_progress.dart';

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

  _UiMessage({
    required this.role,
    required this.text,
    required this.time,
  });
}

class ChatPage extends StatefulWidget {
  const ChatPage({
    super.key,
    required this.clientFactory,
    required this.isConfigured,
    this.manualMode = false,
    this.onOpenSettings,
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
  void dispose() {
    _sub?.cancel();
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------- 发送 / 重试
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
          _busy = false;
        });
      },
      onDone: () {
        if (!mounted) return;
        setState(() {
          assistantMsg.streaming = false;
          if (assistantMsg.error == null && assistantMsg.text.isNotEmpty) {
            _history.add(ChatMessage(
              role: ChatRole.assistant,
              content: assistantMsg.text,
              time: assistantMsg.time,
            ));
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
    setState(() {
      _messages.removeAt(idx);
      failed.error = null;
      failed.text = '';
      failed.streaming = true;
      failed.toolCalls.clear();
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
          _busy = false;
        });
      },
      onDone: () {
        if (!mounted) return;
        setState(() {
          failed.streaming = false;
          if (failed.error == null && failed.text.isNotEmpty) {
            _history.add(ChatMessage(
              role: ChatRole.assistant,
              content: failed.text,
              time: failed.time,
            ));
          }
          _busy = false;
        });
      },
    );
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
      case AiErrorEvent(:final String message):
        assistantMsg.streaming = false;
        assistantMsg.error = message;
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
      return _buildUnconfigured();
    }
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      body: SafeArea(
        child: Column(
          children: <Widget>[
            Expanded(child: _buildList()),
            if (widget.manualMode) _buildModeStrip(),
            _buildInputBar(),
          ],
        ),
      ),
    );
  }

  /// 手动模式安静标注：一行次要文字，不使用警示色。
  Widget _buildModeStrip() {
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
        '手动模式：AI 可对话，但调谐等动作不会真正执行',
        style: AppTokens.auxiliary.copyWith(
          fontSize: AppTokens.annotationFontSize,
          color: AppTokens.textAt(AppTokens.textAlphaTertiary),
        ),
      ),
    );
  }

  Widget _buildUnconfigured() {
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            // 未配置引导卡片宽度：单列空态布局约束（一次性布局参数）。
            constraints: const BoxConstraints(maxWidth: 320),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Icon(
                  Icons.key_outlined,
                  size: AppTokens.iconSizeEmpty,
                  color: AppTokens.textAt(AppTokens.textAlphaFaint),
                ),
                const SizedBox(height: AppTokens.spacingL),
                const Text('尚未配置 AI API key', style: AppTokens.sectionTitle),
                const SizedBox(height: AppTokens.spacingS),
                const Text(
                  '配置硅基流动 API key 后即可开始对话',
                  textAlign: TextAlign.center,
                  style: AppTokens.auxiliary,
                ),
                const SizedBox(height: AppTokens.spacingL),
                TextButton(
                  onPressed: widget.onOpenSettings,
                  child: const Text('去设置'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildList() {
    if (_messages.isEmpty) {
      return Center(
        child: Text(
          '开始一段对话吧',
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

  Widget _buildBubble(_UiMessage m) {
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
                if (m.toolCalls.isNotEmpty) ...<Widget>[
                  TaskProgressView(
                    steps: m.toolCalls
                        .map((_UiToolCall c) => TaskStep.fromCall(
                              tool: c.name,
                              argumentsPreview: c.argumentsPreview,
                              done: c.done,
                              result: c.result,
                            ))
                        .toList(),
                  ),
                ],
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
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
            child: const Text('重试'),
          ),
        ),
      ],
    );
  }

  Widget _buildInputBar() {
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
                  style: AppTokens.body,
                  cursorColor: AppTokens.accent,
                  decoration: const InputDecoration(
                    border: InputBorder.none,
                    hintText: '输入消息…',
                    hintStyle: TextStyle(color: AppTokens.textSecondary),
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
