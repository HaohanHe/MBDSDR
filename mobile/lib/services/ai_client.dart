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
  Stream<ChatStreamEvent> complete({required List<ChatMessage> history}) async* {
    final List<ChatMessage> working = List<ChatMessage>.from(history);
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
