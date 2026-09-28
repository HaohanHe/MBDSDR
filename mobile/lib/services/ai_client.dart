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

import '../models/chat_message.dart';

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
  final List<ToolCallDelta>? toolCallDeltas;
  final String? finishReason;

  const ChatCompletionChunk({
    this.contentDelta,
    this.toolCallDeltas,
    this.finishReason,
  });

  static ChatCompletionChunk? fromJson(Map<String, dynamic> json) {
    final Object? choicesRaw = json['choices'];
    if (choicesRaw is! List || choicesRaw.isEmpty) return null;
    final Object? firstRaw = choicesRaw.first;
    if (firstRaw is! Map<String, dynamic>) return null;

    String? content;
    List<ToolCallDelta>? toolDeltas;

    final Object? deltaRaw = firstRaw['delta'];
    if (deltaRaw is Map<String, dynamic>) {
      final Object? contentRaw = deltaRaw['content'];
      if (contentRaw is String) content = contentRaw;

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
  static const int _maxToolRounds = 6;

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

  // ------------------------------------------------------------- 请求体
  @visibleForTesting
  static Map<String, dynamic> buildRequestJson({
    required List<ChatMessage> messages,
    required List<AiTool> tools,
    required bool stream,
    required String model,
  }) {
    return <String, dynamic>{
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

    while (round < _maxToolRounds) {
      round++;

      String assistantText = '';
      final ToolCallAccumulator accumulator = ToolCallAccumulator();

      // 1) 发起流式请求
      final Stream<String> lines;
      try {
        final Map<String, dynamic> reqJson = buildRequestJson(
          messages: working,
          tools: tools,
          stream: true,
          model: model,
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

      // 2) 消费 SSE 流
      try {
        await for (final String line in lines) {
          final ChatCompletionChunk? chunk = parseSseDataLine(line);
          if (chunk == null) continue;
          final String? c = chunk.contentDelta;
          if (c != null && c.isNotEmpty) {
            assistantText += c;
            yield ChatTextEvent(c);
          }
          final List<ToolCallDelta>? tcs = chunk.toolCallDeltas;
          if (tcs != null && tcs.isNotEmpty) {
            accumulator.addDeltas(tcs);
          }
        }
      } catch (e) {
        yield AiErrorEvent(_friendlyError(e));
        return;
      }

      // 3) 这一轮是否要求调工具？
      final List<ToolCallRecord> records = accumulator.buildRecords();
      if (records.isEmpty) {
        yield const AssistantTurnDoneEvent();
        return;
      }

      // 4) 把带 tool_calls 的 assistant 消息追加进工作列表
      working.add(
        ChatMessage(
          role: ChatRole.assistant,
          content: assistantText,
          time: DateTime.now(),
          toolCalls: records,
        ),
      );

      // 5) 逐个执行工具，把结果以 tool 角色回灌
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
            result = await tool.execute(parsed);
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
        'Reached max tool-call rounds ($_maxToolRounds) without a final answer.');
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
