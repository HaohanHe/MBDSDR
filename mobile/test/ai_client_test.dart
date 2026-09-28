// ============================================================================
// AiClient 离线测试 —— 全部注入 fake ChatTransport，禁止真实联网。
// 覆盖：
//   a) buildRequestJson：model / stream / 消息角色映射 / tools 包装
//   b) parseSseDataLine + ToolCallAccumulator：文本分片、tool_call 分片、[DONE]
//   c) complete() 工具循环：2 次请求、工具真被调用、事件序列、最终文本
// ============================================================================

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/chat_message.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';

void main() {
  group('buildRequestJson', () {
    test('serializes model/stream/messages/tools 正确', () {
      final List<ChatMessage> messages = <ChatMessage>[
        ChatMessage(role: ChatRole.system, content: '你是助手', time: DateTime(2026)),
        ChatMessage(role: ChatRole.user, content: '调谐到 145M', time: DateTime(2026)),
        ChatMessage(
          role: ChatRole.assistant,
          content: '',
          time: DateTime(2026),
          toolCalls: <ToolCallRecord>[
            const ToolCallRecord(
              id: 'call_abc',
              name: 'set_frequency',
              argumentsJson: '{"frequency_hz":145000000}',
            ),
          ],
        ),
        ChatMessage(
          role: ChatRole.tool,
          content: '{"ok":true}',
          time: DateTime(2026),
          toolCallId: 'call_abc',
        ),
      ];

      final AiTool tool = AiTool(
        name: 'set_frequency',
        description: '设置中心频率',
        parameters: const <String, dynamic>{
          'type': 'object',
          'properties': <String, dynamic>{
            'frequency_hz': <String, dynamic>{
              'type': 'number',
              'description': '中心频率 Hz',
            },
          },
          'required': <String>['frequency_hz'],
        },
        execute: (_) async => '',
      );

      final Map<String, dynamic> json = AiClient.buildRequestJson(
        messages: messages,
        tools: <AiTool>[tool],
        stream: true,
        model: 'm-test',
      );

      expect(json['model'], 'm-test');
      expect(json['stream'], isTrue);

      final List<dynamic> msgs = json['messages']! as List<dynamic>;
      expect(msgs.length, 4);

      final Map<String, dynamic> m0 = msgs[0]! as Map<String, dynamic>;
      expect(m0['role'], 'system');
      expect(m0['content'], '你是助手');

      final Map<String, dynamic> m1 = msgs[1]! as Map<String, dynamic>;
      expect(m1['role'], 'user');
      expect(m1['content'], '调谐到 145M');

      final Map<String, dynamic> m2 = msgs[2]! as Map<String, dynamic>;
      expect(m2['role'], 'assistant');
      final List<dynamic> tc = m2['tool_calls']! as List<dynamic>;
      expect(tc.length, 1);
      final Map<String, dynamic> tc0 = tc[0]! as Map<String, dynamic>;
      expect(tc0['id'], 'call_abc');
      expect(tc0['type'], 'function');
      final Map<String, dynamic> fn = tc0['function']! as Map<String, dynamic>;
      expect(fn['name'], 'set_frequency');
      expect(fn['arguments'], '{"frequency_hz":145000000}');

      final Map<String, dynamic> m3 = msgs[3]! as Map<String, dynamic>;
      expect(m3['role'], 'tool');
      expect(m3['tool_call_id'], 'call_abc');
      expect(m3['content'], '{"ok":true}');

      final List<dynamic> toolDefs = json['tools']! as List<dynamic>;
      expect(toolDefs.length, 1);
      final Map<String, dynamic> td0 = toolDefs[0]! as Map<String, dynamic>;
      expect(td0['type'], 'function');
      final Map<String, dynamic> tf = td0['function']! as Map<String, dynamic>;
      expect(tf['name'], 'set_frequency');
      expect(tf['description'], '设置中心频率');
      expect(
        tf['parameters'],
        const <String, dynamic>{
          'type': 'object',
          'properties': <String, dynamic>{
            'frequency_hz': <String, dynamic>{
              'type': 'number',
              'description': '中心频率 Hz',
            },
          },
          'required': <String>['frequency_hz'],
        },
      );
    });

    test('assistant 无 tool_calls 时不输出 tool_calls 字段', () {
      final Map<String, dynamic> json = AiClient.buildRequestJson(
        messages: <ChatMessage>[
          ChatMessage(role: ChatRole.assistant, content: '你好', time: DateTime(2026)),
        ],
        tools: const <AiTool>[],
        stream: false,
        model: 'm',
      );
      final Map<String, dynamic> m =
          (json['messages']! as List<dynamic>)[0]! as Map<String, dynamic>;
      expect(m['tool_calls'], isNull);
      expect(m['content'], '你好');
    });
  });

  group('parseSseDataLine + ToolCallAccumulator', () {
    test('文本分片 + 跨片 tool_call 累积 + [DONE]', () {
      // 真实风格 SSE：
      //  - 文本分片
      //  - 一个 tool_call：第 1 片给 id/name，第 2、3 片拼 arguments
      //  - [DONE]
      final List<String> lines = <String>[
        ': SSE stream',
        'data: {"choices":[{"delta":{"content":"好的，"}}]}',
        'data: {"choices":[{"delta":{"content":"正在调谐。"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_9","type":"function","function":{"name":"set_frequency","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"frequency"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"_hz\\":145000000}"}}]}}]}',
        '',
        'data: [DONE]',
      ];

      final ToolCallAccumulator acc = ToolCallAccumulator();
      String text = '';
      int doneLines = 0;

      for (final String line in lines) {
        final ChatCompletionChunk? c = AiClient.parseSseDataLine(line);
        if (line.trim() == 'data: [DONE]') {
          expect(c, isNull);
          doneLines++;
          continue;
        }
        if (line.trim().isEmpty || line.startsWith(':')) {
          expect(c, isNull);
          continue;
        }
        expect(c, isNotNull, reason: 'line=$line');
        if (c!.contentDelta != null) text += c.contentDelta!;
        if (c.toolCallDeltas != null) acc.addDeltas(c.toolCallDeltas!);
      }

      expect(doneLines, 1);
      expect(text, '好的，正在调谐。');

      final List<ToolCallRecord> records = acc.buildRecords();
      expect(records.length, 1);
      expect(records[0].id, 'call_9');
      expect(records[0].name, 'set_frequency');

      final Object? args = jsonDecode(records[0].argumentsJson);
      expect(args, isA<Map<String, dynamic>>());
      final Map<String, dynamic> m = args as Map<String, dynamic>;
      expect(m['frequency_hz'], 145000000);
    });

    test('非法 JSON 行返回 null 不抛异常', () {
      expect(AiClient.parseSseDataLine('data: not-json'), isNull);
      expect(AiClient.parseSseDataLine('data: '), isNull);
      expect(AiClient.parseSseDataLine(': comment'), isNull);
      expect(AiClient.parseSseDataLine(''), isNull);
    });
  });

  group('complete() 工具循环', () {
    test('第一轮要求 tool_call → 执行工具 → 第二轮返回最终文本', () async {
      int requestCount = 0;
      Map<String, dynamic>? capturedArgs;
      final List<String> requestBodies = <String>[];

      final AiTool fakeTool = AiTool(
        name: 'set_frequency',
        description: 'set',
        parameters: const <String, dynamic>{},
        execute: (Map<String, dynamic> args) async {
          capturedArgs = Map<String, dynamic>.from(args);
          return '{"ok":true,"frequency_hz":145000000}';
        },
      );

      // 第 1 次响应：要求调工具
      const String sseRound1 = '''data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"set_frequency","arguments":""}}]}}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"frequency_hz\\":145000000}"}}]}}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]''';

      // 第 2 次响应：最终 assistant 文本
      const String sseRound2 = '''data: {"choices":[{"delta":{"content":"已调谐到 145.0 MHz。"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        requestBodies.add(body);
        // 鉴权头必须带 Bearer，且 key 不回显在错误里。
        expect(headers['Authorization'], startsWith('Bearer '));
        expect(headers['Accept'], 'text/event-stream');
        final String sse = requestCount == 1 ? sseRound1 : sseRound2;
        return Stream<String>.fromIterable(sse.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-test-key-should-not-leak',
        tools: <AiTool>[fakeTool],
        transport: fakeTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      final List<ChatMessage> history = <ChatMessage>[
        ChatMessage(role: ChatRole.user, content: '调谐到 145M', time: DateTime(2026)),
      ];

      await for (final ChatStreamEvent e in client.complete(history: history)) {
        events.add(e);
      }

      // 共 2 次请求
      expect(requestCount, 2);

      // 工具以解析后的参数被调用 1 次
      expect(capturedArgs, isNotNull);
      expect(capturedArgs!['frequency_hz'], 145000000);

      // 第 2 次请求体必须把工具结果以 role:tool 回灌
      expect(requestBodies.length, 2);
      final Map<String, dynamic> secondReq =
          jsonDecode(requestBodies[1]) as Map<String, dynamic>;
      final List<dynamic> secondMsgs = secondReq['messages']! as List<dynamic>;
      final bool hasToolRole = secondMsgs
          .any((dynamic m) => (m! as Map<String, dynamic>)['role'] == 'tool');
      expect(hasToolRole, isTrue);
      final bool hasAssistantToolCalls = secondMsgs.any(
        (dynamic m) {
          final Map<String, dynamic> mm = m! as Map<String, dynamic>;
          return mm['role'] == 'assistant' && mm['tool_calls'] != null;
        },
      );
      expect(hasAssistantToolCalls, isTrue);

      // 事件序列
      final List<Type> types = events.map((ChatStreamEvent e) => e.runtimeType).toList();
      expect(types.indexOf(ToolCallStartedEvent), lessThan(types.indexOf(ToolCallFinishedEvent)));
      expect(types.last, AssistantTurnDoneEvent);
      expect(events.whereType<AiErrorEvent>(), isEmpty);

      // 最终文本
      final String finalText =
          events.whereType<ChatTextEvent>().map((ChatTextEvent e) => e.delta).join();
      expect(finalText, '已调谐到 145.0 MHz。');

      // 工具开始/结束各 1 次
      expect(events.whereType<ToolCallStartedEvent>().length, 1);
      expect(events.whereType<ToolCallFinishedEvent>().length, 1);
    });

    test('工具执行抛异常时，异常作为工具结果文本回灌，不中断流程', () async {
      int requestCount = 0;
      final AiTool boom = AiTool(
        name: 'boom',
        description: 'boom',
        parameters: const <String, dynamic>{},
        execute: (_) async => throw StateError('boom!'),
      );

      const String sseRound1 = '''data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_2","type":"function","function":{"name":"boom","arguments":"{}"}}]}}]}
data: [DONE]''';
      const String sseRound2 = '''data: {"choices":[{"delta":{"content":"工具失败了，但我接着说。"}}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        final String sse = requestCount == 1 ? sseRound1 : sseRound2;
        return Stream<String>.fromIterable(sse.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        tools: <AiTool>[boom],
        transport: fakeTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      await for (final ChatStreamEvent e in client.complete(
        history: <ChatMessage>[
          ChatMessage(role: ChatRole.user, content: 'go', time: DateTime(2026)),
        ],
      )) {
        events.add(e);
      }

      expect(requestCount, 2);
      final ToolCallFinishedEvent finished =
          events.whereType<ToolCallFinishedEvent>().single;
      expect(finished.result, contains('Error executing tool'));
      expect(events.whereType<AiErrorEvent>(), isEmpty);
    });

    test('transport 抛错 → AiErrorEvent，不泄露 key', () async {
      Future<Stream<String>> badTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        throw AiException('Request failed (HTTP 401): Unauthorized');
      }

      final AiClient client = AiClient(
        apiKey: 'sk-secret-key-12345',
        transport: badTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      await for (final ChatStreamEvent e in client.complete(
        history: <ChatMessage>[
          ChatMessage(role: ChatRole.user, content: 'hi', time: DateTime(2026)),
        ],
      )) {
        events.add(e);
      }

      expect(events.length, 1);
      expect(events.first, isA<AiErrorEvent>());
      final AiErrorEvent err = events.first as AiErrorEvent;
      expect(err.message, isNot(contains('sk-secret-key-12345')));
      expect(err.message, contains('401'));
    });
  });
}
