// ============================================================================
// AiClient —— 硅基流动（OpenAI 兼容）流式 + function-calling 客户端
// ----------------------------------------------------------------------------
// * 用 dart:io HttpClient 发起 SSE 流式请求；
// * ChatTransport 是可注入的请求缝，测试时注入 fake 即可完全离线；
// * apiKey 仅由构造传入，错误信息绝不回显 key。
// ============================================================================

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../app/tokens.dart';
import '../models/chat_message.dart';
import 'tool_arguments_validator.dart';

/// 请求缝：给定 url / headers / body，返回一个「按行交付」的字符串流。
/// 默认实现走 dart:io HttpClient；测试注入 fake 即可离线。
typedef ChatTransport = Future<Stream<String>> Function(
  Uri url,
  Map<String, String> headers,
  String body,
);

/// 工具定义：外壳模块（例如调谐 radio）在接线时注册真正的工具。
@immutable
class AiTool {
  final String name;
  final String description;

  /// JSON Schema（draft 7），原样进 request.tools[].function.parameters。
  final Map<String, dynamic> parameters;

  /// 真正执行工具；入参是已 JSON.parse 的 arguments map；返回文本结果。
  final Future<String> Function(Map<String, dynamic> arguments) execute;

  const AiTool({
    required this.name,
    required this.description,
    required this.parameters,
    required this.execute,
  });
}

/// 业务异常。message 已经过滤，不包含 apiKey。
class AiException implements Exception {
  final String message;
  AiException(this.message);

  @override
  String toString() => 'AiException: $message';
}

// ---------------------------------------------------------------------------
// 流式事件
// ---------------------------------------------------------------------------

sealed class ChatStreamEvent {
  const ChatStreamEvent();
}

/// assistant 文本增量。
class ChatTextEvent extends ChatStreamEvent {
  final String delta;
  const ChatTextEvent(this.delta);
}

/// 模型要求调用一个工具（开始执行）。
class ToolCallStartedEvent extends ChatStreamEvent {
  final String name;
  final String argumentsPreview;
  const ToolCallStartedEvent({
    required this.name,
    required this.argumentsPreview,
  });
}

/// 工具执行完毕，result 是回灌给模型的文本结果。
class ToolCallFinishedEvent extends ChatStreamEvent {
  final String name;
  final String result;
  const ToolCallFinishedEvent({
    required this.name,
    required this.result,
  });
}

/// 一轮 assistant 回复完成（无更多 tool_calls）。
class AssistantTurnDoneEvent extends ChatStreamEvent {
  const AssistantTurnDoneEvent();
}

/// 网络 / 鉴权 / 协议错误。
class AiErrorEvent extends ChatStreamEvent {
  final String message;
  const AiErrorEvent(this.message);
}

/// 自动压缩（send 阈值触发）真实发生了一次历史折叠。
///
/// 与手动压缩共用同一组真实口径（折叠条数 + 折叠前占用 / 预算字符估算），
/// 由 UI 用统一纯函数 [compactionStatusText] 出文案，绝不伪造计数。
class ContextCompactedEvent extends ChatStreamEvent {
  const ContextCompactedEvent({
    required this.compressedUserTurns,
    required this.charsUsed,
    required this.budgetChars,
  });

  /// 被折叠（不再逐句上送）的 user 轮次数。
  final int compressedUserTurns;

  /// 折叠前估算的总占用字符（所有 content 长度之和，粗代理）。
  final int charsUsed;

  /// 本次触发所用的字符预算（具名常量，非裸数）。
  final int budgetChars;
}

/// 压缩状态文案纯函数：自动压缩（send 阈值触发）与手动压缩两条路径**共用同一口径**，
/// 禁在 UI 里散落裸拼文案。
///
/// 给真实折叠条数 + 真实「占用 / 预算」字符估算；并诚实说明原文仍保留（未删落盘）。
String compactionStatusText({
  required int compressedTurns,
  required int charsUsed,
  required int budgetChars,
}) {
  return '已折叠 $compressedTurns 轮早期对话为占位'
      '（占用约 $charsUsed / 预算 $budgetChars 字符；完整原文仍在本会话记录中，可回看，未删除）';
}

// ---------------------------------------------------------------------------
// SSE chunk 解析
// ---------------------------------------------------------------------------

/// 单个流式 delta 里的 tool_call 分片（按 index 累积）。
@immutable
class ToolCallDelta {
  final int index;
  final String? id;
  final String? name;
  final String? argumentsFragment;

  const ToolCallDelta({
    required this.index,
    this.id,
    this.name,
    this.argumentsFragment,
  });
}

/// 一次 SSE data 行解析出的内容。
@immutable
class ChatCompletionChunk {
  final String? contentDelta;

  /// 思维链增量（delta.reasoning_content），与 contentDelta 物理隔离、逐字累积。
  final String? reasoningDelta;
  final List<ToolCallDelta>? toolCallDeltas;
  final String? finishReason;

  const ChatCompletionChunk({
    this.contentDelta,
    this.reasoningDelta,
    this.toolCallDeltas,
    this.finishReason,
  });

  static ChatCompletionChunk? fromJson(Map<String, dynamic> json) {
    final Object? choicesRaw = json['choices'];
    if (choicesRaw is! List || choicesRaw.isEmpty) return null;
    final Object? firstRaw = choicesRaw.first;
    if (firstRaw is! Map<String, dynamic>) return null;

    String? content;
    String? reasoning;
    List<ToolCallDelta>? toolDeltas;

    final Object? deltaRaw = firstRaw['delta'];
    if (deltaRaw is Map<String, dynamic>) {
      final Object? contentRaw = deltaRaw['content'];
      if (contentRaw is String) content = contentRaw;

      final Object? reasoningRaw = deltaRaw['reasoning_content'];
      if (reasoningRaw is String) reasoning = reasoningRaw;

      final Object? tcsRaw = deltaRaw['tool_calls'];
      if (tcsRaw is List) {
        toolDeltas = <ToolCallDelta>[];
        for (final Object? tcRaw in tcsRaw) {
          if (tcRaw is! Map<String, dynamic>) continue;
          final Object? fnRaw = tcRaw['function'];
          String? name;
          String? args;
          if (fnRaw is Map<String, dynamic>) {
            final Object? n = fnRaw['name'];
            if (n is String) name = n;
            final Object? a = fnRaw['arguments'];
            if (a is String) args = a;
          }
          final Object? idxRaw = tcRaw['index'];
          final int idx = idxRaw is int ? idxRaw : 0;
          final Object? idRaw = tcRaw['id'];
          final String? id = idRaw is String ? idRaw : null;
          toolDeltas.add(
            ToolCallDelta(index: idx, id: id, name: name, argumentsFragment: args),
          );
        }
      }
    }

    final Object? finishRaw = firstRaw['finish_reason'];
    final String? finish = finishRaw is String ? finishRaw : null;

    return ChatCompletionChunk(
      contentDelta: content,
      reasoningDelta: reasoning,
      toolCallDeltas: toolDeltas,
      finishReason: finish,
    );
  }
}

class _AccEntry {
  String? id;
  String? name;
  final StringBuffer arguments = StringBuffer();
}

/// 按 index 累积 delta.tool_calls 分片，产出完整 ToolCallRecord 列表。
class ToolCallAccumulator {
  final Map<int, _AccEntry> _entries = <int, _AccEntry>{};

  void addDeltas(List<ToolCallDelta> deltas) {
    for (final ToolCallDelta d in deltas) {
      final _AccEntry entry =
          _entries.putIfAbsent(d.index, () => _AccEntry());
      final String? id = d.id;
      if (id != null && id.isNotEmpty) entry.id ??= id;
      final String? name = d.name;
      if (name != null && name.isNotEmpty) entry.name ??= name;
      final String? frag = d.argumentsFragment;
      if (frag != null) entry.arguments.write(frag);
    }
  }

  List<ToolCallRecord> buildRecords() {
    final List<int> indices = _entries.keys.toList()..sort();
    return indices.map((int i) {
      final _AccEntry e = _entries[i]!;
      return ToolCallRecord(
        id: e.id ?? '',
        name: e.name ?? '',
        argumentsJson: e.arguments.toString(),
      );
    }).toList();
  }
}

// ---------------------------------------------------------------------------
// 历史折叠（G1 AI 上下文压缩）
// ---------------------------------------------------------------------------

/// [AiClient.compactHistory] 的结果：真正上送模型的消息序列 + 诚实的折叠标记。
@immutable
class ContextCompaction {
  const ContextCompaction({
    required this.messages,
    required this.didCompact,
    required this.compressedUserTurns,
    required this.charsUsed,
  });

  /// 折叠后实际用于请求的消息序列（调用方按需再拷贝）。
  final List<ChatMessage> messages;

  /// 是否真的发生了折叠（false = 预算内，原样全量直传）。
  final bool didCompact;

  /// 被折叠（不再逐句上送）的 user 轮次数。
  final int compressedUserTurns;

  /// 折叠前估算的总占用字符（所有 content 长度之和，粗代理）。
  /// 供 UI 统一文案用真实口径，不伪造。
  final int charsUsed;
}

// ---------------------------------------------------------------------------
// AiClient
// ---------------------------------------------------------------------------

class AiClient {
  static const String defaultModel = 'Qwen/Qwen2.5-7B-Instruct';
  static const String _endpoint =
      'https://api.siliconflow.cn/v1/chat/completions';

  final String apiKey;
  final String model;
  final List<AiTool> tools;
  final ChatTransport _transport;

  AiClient({
    required this.apiKey,
    String? model,
    this.tools = const <AiTool>[],
    ChatTransport? transport,
  })  : model = model ?? defaultModel,
        _transport = transport ?? _defaultHttpClientTransport;

  // ------------------------------------------------------------- thinking 配置映射
  /// 按模型名决定本轮是否下发 OpenAI 兼容的 thinking 参数。
  ///
  /// 学习笔记结论（两家方向相反但底层共识一致）：
  ///   * DeepSeek-V3.2 / GLM-4.7 = 官方 interleaved thinking 两模型，
  ///     工具流里开着思考并逐字保留 reasoning_content 才是稳定特性 → 恒开；
  ///   * MiMo v2.6 系：对话轮开思考，但「开着 thinking 调 tool」是官方点名的
  ///     不稳定信号 → 工具轮（[toolRound]=true）关思考；
  ///   * 其余（默认 Qwen 等）：不下发 thinking 参数，走服务端默认。
  ///
  /// 返回 (enabled, budget)；enabled=false 时调用方不应把 thinking 键写进请求体。
  @visibleForTesting
  static ({bool enabled, int budget}) thinkingConfigForModel(
    String model, {
    required bool toolRound,
  }) {
    const int budget = AppTokens.kThinkingBudgetTokens;
    if (model.contains('DeepSeek-V3.2') || model.contains('GLM-4.7')) {
      return (enabled: true, budget: budget);
    }
    if (model.contains('mimo-v2.6')) {
      // 对话轮开、工具轮关。
      return toolRound
          ? (enabled: false, budget: budget)
          : (enabled: true, budget: budget);
    }
    return (enabled: false, budget: budget);
  }

  // ------------------------------------------------------------- OpenAI 请求体
  @visibleForTesting
  static Map<String, dynamic> buildRequestJson({
    required List<ChatMessage> messages,
    required List<AiTool> tools,
    required bool stream,
    required String model,
    bool thinkingEnabled = false,
    int thinkingBudget = AppTokens.kThinkingBudgetTokens,
  }) {
    final Map<String, dynamic> body = <String, dynamic>{
      'model': model,
      'stream': stream,
      'messages': messages.map((ChatMessage m) => m.toApiJson()).toList(),
      'tools': tools
          .map(
            (AiTool t) => <String, dynamic>{
              'type': 'function',
              'function': <String, dynamic>{
                'name': t.name,
                'description': t.description,
                'parameters': t.parameters,
              },
            },
          )
          .toList(),
    };
    // 仅在显式开启时下发；关闭/默认模型不下发，走服务端默认。
    if (thinkingEnabled) {
      body['enable_thinking'] = true;
      body['thinking_budget'] = thinkingBudget;
    }
    return body;
  }

  // ------------------------------------------------------------- Anthropic 请求体（接口层，不接网络）
  /// Anthropic Messages 形状映射（MiMo /anthropic/v1）：system 提为顶层参数、
  /// tools 用 `{type:"custom", input_schema}`、消息用内容块。
  /// 仅做形状映射，不在默认网络路径启用；便于将来切 Anthropic 通道时复用。
  @visibleForTesting
  static Map<String, dynamic> buildAnthropicRequestJson({
    required List<ChatMessage> messages,
    required List<AiTool> tools,
    required bool stream,
    required String model,
  }) {
    final StringBuffer systemBuf = StringBuffer();
    final List<Map<String, dynamic>> outMessages = <Map<String, dynamic>>[];

    for (final ChatMessage m in messages) {
      if (m.role == ChatRole.system) {
        if (systemBuf.isNotEmpty) systemBuf.write('\n');
        systemBuf.write(m.content);
        continue;
      }
      switch (m.role) {
        case ChatRole.user:
          outMessages.add(<String, dynamic>{
            'role': 'user',
            'content': <Map<String, dynamic>>[
              <String, dynamic>{'type': 'text', 'text': m.content},
            ],
          });
        case ChatRole.assistant:
          final List<Map<String, dynamic>> blocks = <Map<String, dynamic>>[];
          if (m.content.isNotEmpty) {
            blocks.add(<String, dynamic>{'type': 'text', 'text': m.content});
          }
          final List<ToolCallRecord>? calls = m.toolCalls;
          if (calls != null) {
            for (final ToolCallRecord t in calls) {
              blocks.add(<String, dynamic>{
                'type': 'tool_use',
                'id': t.id,
                'name': t.name,
                'input': _safeParseObject(t.argumentsJson),
              });
            }
          }
          outMessages.add(<String, dynamic>{
            'role': 'assistant',
            'content': blocks,
          });
        case ChatRole.tool:
          // 工具结果在 Anthropic 里归并进下一条 user 消息的 tool_result 块。
          outMessages.add(<String, dynamic>{
            'role': 'user',
            'content': <Map<String, dynamic>>[
              <String, dynamic>{
                'type': 'tool_result',
                'tool_use_id': m.toolCallId,
                'content': m.content,
              },
            ],
          });
        case ChatRole.system:
          break;
      }
    }

    return <String, dynamic>{
      'model': model,
      'stream': stream,
      if (systemBuf.isNotEmpty) 'system': systemBuf.toString(),
      'messages': outMessages,
      'tools': tools
          .map(
            (AiTool t) => <String, dynamic>{
              'type': 'custom',
              'name': t.name,
              'description': t.description,
              'input_schema': t.parameters,
            },
          )
          .toList(),
    };
  }

  // ------------------------------------------------------------- 历史折叠（G1）
  /// 定位折叠边界：前导 system 指令整段保留不参与折叠；其后从尾部向前数，
  /// 保留最近 [keepRecentUserTurns] 个 user 轮原文逐句上送。
  ///
  /// 返回「recent 段在 [history] 里的**绝对起始下标**」——即 history[0..boundary) 中
  /// 除去前导 system 的旧轮次会被折走。user 轮次不足 [keepRecentUserTurns] 时返回 sysEnd
  /// （oldMessages 为空 → 不折叠，保住当前提问）。
  ///
  /// 抽出为静态 helper：compactHistory 与 ChatPage「折叠占位气泡定位」共用同一规则，
  /// 避免两处边界判定漂移。ChatPage 手动压缩定位占位气泡也会调用，故为公开 API。
  static int findCompactBoundary(
    List<ChatMessage> history, {
    required int keepRecentUserTurns,
  }) {
    int sysEnd = 0;
    while (sysEnd < history.length &&
        history[sysEnd].role == ChatRole.system) {
      sysEnd++;
    }
    int userSeen = 0;
    int boundary = sysEnd; // 默认：user 轮次不足 → 不折。
    for (int i = history.length - 1; i >= sysEnd; i--) {
      if (history[i].role == ChatRole.user) {
        userSeen++;
        if (userSeen >= keepRecentUserTurns) {
          boundary = i;
          break;
        }
      }
    }
    return boundary;
  }

  /// 发送前把「超预算」的旧轮次折叠成一条诚实占位；预算内则原样直传。
  ///
  /// 机制（学桌面 cpp/src/ai/ai_context.cpp `compactContext` 的思路，自写 Dart、不抄码）：
  ///   1. 先量「占用」：所有消息 content 字符数之和（移动端无分词器，用字符数粗代理）；
  ///   2. 占用 ≤ 预算 → 原样返回，一条不动；
  ///   3. 超预算 → 从尾部向前数，保留最近 [keepRecentUserTurns] 个 user 轮次原文逐句上送，
  ///      其之前的旧轮次整体折叠成**一条 system 占位**；
  ///   4. 占位是**规则法诚实摘要**（不发第二次 LLM 请求、不 mock 摘要）：只报「折叠了 N 轮」
  ///      并逐条列出旧轮里每个 user 的诉求（截断预览）。绝不编造助手回复内容。
  ///
  /// 不静默丢原文：折叠只作用在「上送给模型的提示」这一层；会话完整原文仍由
  /// ChatSessionStore 持久化、在 UI 里可回看——本函数不删任何落盘数据。
  /// 前导 system 指令永不折叠（它们是设定，不是对话历史）。
  ///
  /// 公开 API：除 send 自动压缩外，ChatPage「压缩上下文」按钮也直接调用它做手动折叠。
  static ContextCompaction compactHistory(
    List<ChatMessage> history, {
    int budgetChars = AppTokens.kAiContextBudgetChars,
    int keepRecentUserTurns = AppTokens.kAiKeepRecentUserTurns,
  }) {
    // 前导 system（指令）整段保留，不参与折叠。
    int sysEnd = 0;
    while (sysEnd < history.length &&
        history[sysEnd].role == ChatRole.system) {
      sysEnd++;
    }
    final List<ChatMessage> systemMsgs = history.sublist(0, sysEnd);
    final List<ChatMessage> convo = history.sublist(sysEnd);

    // 占用估计：字符数粗代理。
    int total = 0;
    for (final ChatMessage m in history) {
      total += m.content.length;
    }
    if (total <= budgetChars || convo.isEmpty) {
      return ContextCompaction(
        messages: history,
        didCompact: false,
        compressedUserTurns: 0,
        charsUsed: total,
      );
    }

    // 从尾部向前定位折叠边界：保留最近 N 个 user 轮次原文（共用规则）。
    final int boundary =
        findCompactBoundary(history, keepRecentUserTurns: keepRecentUserTurns);
    final List<ChatMessage> oldMessages = history.sublist(sysEnd, boundary);
    final List<ChatMessage> recent = history.sublist(boundary);
    if (oldMessages.isEmpty) {
      return ContextCompaction(
        messages: history,
        didCompact: false,
        compressedUserTurns: 0,
        charsUsed: total,
      );
    }

    // 规则法诚实占位：数 user 轮 + 逐条列诉求（截断）。不调 LLM，不编造。
    int compressedUserTurns = 0;
    final List<String> asks = <String>[];
    for (final ChatMessage m in oldMessages) {
      if (m.role != ChatRole.user) continue;
      compressedUserTurns++;
      final String s = m.content.trim();
      final String preview = s.length > AppTokens.kAiFoldAskPreviewChars
          ? '${s.substring(0, AppTokens.kAiFoldAskPreviewChars)}…'
          : s;
      asks.add(preview);
    }
    final StringBuffer buf = StringBuffer(
      '此前 $compressedUserTurns 轮对话已折叠为占位（不逐句上送模型；'
      '完整原文仍保存在本会话记录中，可随时回看，并非删除）。',
    );
    if (asks.isNotEmpty) {
      buf.write('用户曾依次询问：「${asks.join('」「')}」。助手已相应回复，细节从略。');
    }
    final ChatMessage placeholder = ChatMessage(
      role: ChatRole.system,
      content: buf.toString(),
      time: DateTime.now(),
    );

    return ContextCompaction(
      messages: <ChatMessage>[...systemMsgs, placeholder, ...recent],
      didCompact: true,
      compressedUserTurns: compressedUserTurns,
      charsUsed: total,
    );
  }

  static Object? _safeParseObject(String json) {
    try {
      final Object? decoded = jsonDecode(json);
      if (decoded is Map<String, dynamic>) return decoded;
    } on FormatException {
      // 忽略：占位为空 map，不影响形状断言。
    }
    return const <String, dynamic>{};
  }

  // ------------------------------------------------------------- SSE 行解析
  /// 解析单行 SSE：去掉 'data: ' 前缀；`[DONE]` 返回 null；
  /// 非 data 行 / 非法 JSON 返回 null（静默跳过）。
  @visibleForTesting
  static ChatCompletionChunk? parseSseDataLine(String line) {
    final String trimmed = line.trim();
    if (!trimmed.startsWith('data:')) return null;
    final String payload = trimmed.substring(5).trimLeft();
    if (payload == '[DONE]') return null;
    if (payload.isEmpty) return null;
    try {
      final Object? decoded = jsonDecode(payload);
      if (decoded is! Map<String, dynamic>) return null;
      return ChatCompletionChunk.fromJson(decoded);
    } on FormatException {
      return null;
    }
  }

  // ------------------------------------------------------------- 高层循环
  /// 跑一轮对话（内部自动处理 tool-calling 多轮）。
  ///
  /// [compactBudgetChars] / [keepRecentUserTurns] 为历史折叠预算（见 [compactHistory]）；
  /// 默认走 AppTokens 具名常量，测试可注入极小预算触发折叠，生产不覆盖。
  Stream<ChatStreamEvent> complete({
    required List<ChatMessage> history,
    int compactBudgetChars = AppTokens.kAiContextBudgetChars,
    int keepRecentUserTurns = AppTokens.kAiKeepRecentUserTurns,
  }) async* {
    // G1：history 超预算时先把旧轮折叠成诚实占位，再把「折叠后的提示」发给模型。
    // 只压缩上送提示层，不删会话落盘原文（见 compactHistory 注释）。
    final ContextCompaction compaction = compactHistory(
      history,
      budgetChars: compactBudgetChars,
      keepRecentUserTurns: keepRecentUserTurns,
    );
    // 真实折叠发生 → 吐一条可观测事件，让 UI 用统一纯函数出诚实文案（真实条数/占用）。
    if (compaction.didCompact) {
      yield ContextCompactedEvent(
        compressedUserTurns: compaction.compressedUserTurns,
        charsUsed: compaction.charsUsed,
        budgetChars: compactBudgetChars,
      );
    }
    final List<ChatMessage> working = List<ChatMessage>.from(compaction.messages);
    int round = 0;

    while (round < AppTokens.kMaxToolRounds) {
      round++;

      String assistantText = '';
      final StringBuffer reasoningBuf = StringBuffer();
      final ToolCallAccumulator accumulator = ToolCallAccumulator();
      String? finishReason;

      // 1) 发起流式请求。
      //    工具轮（round>1，刚把工具结果回灌）与对话轮按模型配置决定 thinking 开关。
      final ({bool enabled, int budget}) thinking =
          thinkingConfigForModel(model, toolRound: round > 1);
      final Stream<String> lines;
      try {
        final Map<String, dynamic> reqJson = buildRequestJson(
          messages: working,
          tools: tools,
          stream: true,
          model: model,
          thinkingEnabled: thinking.enabled,
          thinkingBudget: thinking.budget,
        );
        final String body = jsonEncode(reqJson);
        final Map<String, String> headers = <String, String>{
          'Authorization': 'Bearer $apiKey',
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        };
        lines = await _transport(Uri.parse(_endpoint), headers, body);
      } catch (e) {
        yield AiErrorEvent(_friendlyError(e));
        return;
      }

      // 2) 消费 SSE 流：content / reasoning_content / tool_calls 三条流分别累积。
      try {
        await for (final String line in lines) {
          final ChatCompletionChunk? chunk = parseSseDataLine(line);
          if (chunk == null) continue;
          final String? c = chunk.contentDelta;
          if (c != null && c.isNotEmpty) {
            assistantText += c;
            yield ChatTextEvent(c);
          }
          final String? r = chunk.reasoningDelta;
          if (r != null && r.isNotEmpty) {
            reasoningBuf.write(r); // 逐字原样，禁止加工。
          }
          if (chunk.finishReason != null) finishReason = chunk.finishReason;
          final List<ToolCallDelta>? tcs = chunk.toolCallDeltas;
          if (tcs != null && tcs.isNotEmpty) {
            accumulator.addDeltas(tcs);
          }
        }
      } catch (e) {
        yield AiErrorEvent(_friendlyError(e));
        return;
      }

      final String reasoningContent = reasoningBuf.toString();

      // 3) 这一轮是否要求调工具？finishReason=="stop" 视为正常收尾（与 records 空等价）。
      final List<ToolCallRecord> records = accumulator.buildRecords();
      if (records.isEmpty || finishReason == 'stop') {
        yield const AssistantTurnDoneEvent();
        return;
      }

      // 4) 把带 tool_calls 的 assistant 消息（含逐字 reasoning）追加进工作列表。
      working.add(
        ChatMessage(
          role: ChatRole.assistant,
          content: assistantText,
          reasoningContent: reasoningContent,
          time: DateTime.now(),
          toolCalls: records,
        ),
      );

      // 5) 逐个执行工具，把结果以 tool 角色回灌。
      //    执行前先按 schema 校验 arguments：失败不调用 execute，错误 JSON 直接回灌。
      for (final ToolCallRecord rec in records) {
        yield ToolCallStartedEvent(
          name: rec.name,
          argumentsPreview: _previewArgs(rec.argumentsJson),
        );

        String result;
        try {
          final Object? parsed = jsonDecode(rec.argumentsJson);
          final AiTool? tool = _findTool(rec.name);
          if (tool == null) {
            result = 'Error: tool "${rec.name}" is not registered.';
          } else if (parsed is! Map<String, dynamic>) {
            result = 'Error: arguments is not a JSON object.';
          } else {
            final ValidationResult v =
                validateToolArguments(parsed, tool.parameters);
            if (!v.ok) {
              result = validationErrorToToolResult(rec.name, v);
            } else {
              result = await tool.execute(parsed);
            }
          }
        } catch (e) {
          result = 'Error executing tool "${rec.name}": ${_friendlyError(e)}';
        }

        yield ToolCallFinishedEvent(name: rec.name, result: result);
        working.add(
          ChatMessage(
            role: ChatRole.tool,
            content: result,
            time: DateTime.now(),
            toolCallId: rec.id,
          ),
        );
      }
      // 进入下一轮：把工具结果再发给模型拿最终回复
    }

    yield const AiErrorEvent(
        'Reached max tool-call rounds (${AppTokens.kMaxToolRounds}) without a final answer.');
  }

  AiTool? _findTool(String name) {
    for (final AiTool t in tools) {
      if (t.name == name) return t;
    }
    return null;
  }

  static String _previewArgs(String argsJson) {
    final String s = argsJson.trim();
    return s.length > 60 ? '${s.substring(0, 60)}…' : s;
  }

  static String _friendlyError(Object e) {
    if (e is AiException) return e.message;
    // 其他异常（SocketException 等）的 message 不包含 apiKey，可安全透出。
    return e.toString();
  }

  // ------------------------------------------------------------- 默认 transport
  static Future<Stream<String>> _defaultHttpClientTransport(
    Uri url,
    Map<String, String> headers,
    String body,
  ) async {
    final HttpClient client = HttpClient();
    final HttpClientRequest request = await client.postUrl(url);
    headers.forEach((String k, String v) {
      request.headers.set(k, v);
    });
    request.headers.contentType = ContentType.json;
    request.write(body);

    final HttpClientResponse response = await request.close();
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final String errorBody = await response.transform(utf8.decoder).join();
      client.close(force: true);
      // 注意：绝不把 apiKey 拼进来；errorBody 来自服务端，本身不含 key。
      throw AiException(
        'Request failed (HTTP ${response.statusCode}): ${_truncate(errorBody)}',
      );
    }

    final Stream<String> lines = response
        .transform<String>(utf8.decoder)
        .transform<String>(const LineSplitter());

    return lines.transform<String>(
      StreamTransformer<String, String>.fromHandlers(
        handleDone: (EventSink<String> sink) {
          client.close();
          sink.close();
        },
        handleError: (Object e, StackTrace st, EventSink<String> sink) {
          client.close(force: true);
          sink.addError(e, st);
        },
      ),
    );
  }

  static String _truncate(String s) =>
      s.length > 500 ? '${s.substring(0, 500)}…' : s;
}
