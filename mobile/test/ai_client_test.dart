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
        parameters: const <String, dynamic>{
          'type': 'object',
          'properties': <String, dynamic>{
            'frequency_hz': <String, dynamic>{'type': 'number'},
          },
        },
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

    test('reasoning_content 逐字累积并在下一轮 assistant 消息原样回传', () async {
      int requestCount = 0;
      final List<String> requestBodies = <String>[];

      final AiTool fakeTool = AiTool(
        name: 'set_frequency',
        description: 'set',
        parameters: const <String, dynamic>{
          'type': 'object',
          'properties': <String, dynamic>{
            'frequency_hz': <String, dynamic>{'type': 'number'},
          },
        },
        execute: (_) async => '{"ok":true}',
      );

      // 第 1 轮：先吐两段 reasoning_content，再吐 tool_calls。
      const String sseRound1 = '''data: {"choices":[{"delta":{"reasoning_content":"我先想一下"}}]}
data: {"choices":[{"delta":{"reasoning_content":"再调工具"}}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_r","type":"function","function":{"name":"set_frequency","arguments":"{\\"frequency_hz\\":145000000}"}}]}}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]''';

      const String sseRound2 = '''data: {"choices":[{"delta":{"content":"已调好。"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        requestBodies.add(body);
        final String sse = requestCount == 1 ? sseRound1 : sseRound2;
        return Stream<String>.fromIterable(sse.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        model: 'deepseek-ai/DeepSeek-V3.2',
        tools: <AiTool>[fakeTool],
        transport: fakeTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      await for (final ChatStreamEvent e in client.complete(
        history: <ChatMessage>[
          ChatMessage(role: ChatRole.user, content: '调谐', time: DateTime(2026)),
        ],
      )) {
        events.add(e);
      }

      expect(requestCount, 2);

      // 第 2 轮请求体里，带 tool_calls 的 assistant 消息必须逐字带 reasoning_content。
      final Map<String, dynamic> secondReq =
          jsonDecode(requestBodies[1]) as Map<String, dynamic>;
      final List<dynamic> msgs = secondReq['messages']! as List<dynamic>;
      final Map<String, dynamic> assistantWithCalls = msgs
          .cast<Map<String, dynamic>>()
          .firstWhere((Map<String, dynamic> m) =>
              m['role'] == 'assistant' && m['tool_calls'] != null);
      expect(assistantWithCalls['reasoning_content'], '我先想一下再调工具');
      expect(events.whereType<AiErrorEvent>(), isEmpty);
    });

    test('非法参数（越界 + 幻觉键）不调用 execute，错误文本以 tool 回灌', () async {
      int executeCalls = 0;
      int requestCount = 0;
      final List<String> requestBodies = <String>[];

      final AiTool strictTool = AiTool(
        name: 'set_frequency',
        description: 'set',
        parameters: const <String, dynamic>{
          'type': 'object',
          'properties': <String, dynamic>{
            'frequency_hz': <String, dynamic>{
              'type': 'number',
              'minimum': 24000000,
              'maximum': 1700000000,
            },
          },
          'required': <String>['frequency_hz'],
        },
        execute: (_) async {
          executeCalls++;
          return '{"ok":true}';
        },
      );

      // frequency_hz=3e99 越界，外加 schema 外键 ghost_key。
      const String sseRound1 = '''data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_b","type":"function","function":{"name":"set_frequency","arguments":"{\\"frequency_hz\\":3e99,\\"ghost_key\\":1}"}}]}}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]''';

      const String sseRound2 = '''data: {"choices":[{"delta":{"content":"参数不合法，我不能这么做。"}}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        requestBodies.add(body);
        final String sse = requestCount == 1 ? sseRound1 : sseRound2;
        return Stream<String>.fromIterable(sse.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        tools: <AiTool>[strictTool],
        transport: fakeTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      await for (final ChatStreamEvent e in client.complete(
        history: <ChatMessage>[
          ChatMessage(role: ChatRole.user, content: '调到 3e99', time: DateTime(2026)),
        ],
      )) {
        events.add(e);
      }

      // execute 一次都没被调用。
      expect(executeCalls, 0);
      expect(requestCount, 2);

      // 第 2 轮请求体里应有一条 role=tool 消息，内容是 ok:false 的校验错误。
      final Map<String, dynamic> secondReq =
          jsonDecode(requestBodies[1]) as Map<String, dynamic>;
      final List<dynamic> msgs = secondReq['messages']! as List<dynamic>;
      final Map<String, dynamic> toolMsg = msgs
          .cast<Map<String, dynamic>>()
          .firstWhere((Map<String, dynamic> m) => m['role'] == 'tool');
      final Map<String, dynamic> toolContent =
          jsonDecode(toolMsg['content']! as String) as Map<String, dynamic>;
      expect(toolContent['ok'], isFalse);
      expect((toolContent['reasons'] as List<dynamic>).join(' '), contains('最大值'));
      expect((toolContent['reasons'] as List<dynamic>).join(' '), contains('未知参数'));

      // 工具结束事件仍发出（携带错误结果），流程不中断。
      expect(events.whereType<ToolCallFinishedEvent>().length, 1);
      expect(events.whereType<AiErrorEvent>(), isEmpty);
    });

    test('第一轮 finish_reason==stop 直接收尾，只发一次请求', () async {
      int requestCount = 0;
      const String sseRound1 = '''data: {"choices":[{"delta":{"content":"不需要工具，直接回答。"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        return Stream<String>.fromIterable(sseRound1.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        transport: fakeTransport,
      );

      final List<ChatStreamEvent> events = <ChatStreamEvent>[];
      await for (final ChatStreamEvent e in client.complete(
        history: <ChatMessage>[
          ChatMessage(role: ChatRole.user, content: 'hi', time: DateTime(2026)),
        ],
      )) {
        events.add(e);
      }

      expect(requestCount, 1);
      expect(events.last, isA<AssistantTurnDoneEvent>());
    });
  });

  group('thinking 参数下发（按模型配置）', () {
    test('DeepSeek-V3.2 请求体含 enable_thinking + thinking_budget', () async {
      Map<String, dynamic>? firstBody;

      const String sseRound1 = '''data: {"choices":[{"delta":{"content":"直接答。"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        firstBody ??= jsonDecode(body) as Map<String, dynamic>;
        return Stream<String>.fromIterable(sseRound1.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        model: 'deepseek-ai/DeepSeek-V3.2',
        transport: fakeTransport,
      );
      await client
          .complete(history: <ChatMessage>[
            ChatMessage(role: ChatRole.user, content: 'hi', time: DateTime(2026)),
          ])
          .drain<void>();

      expect(firstBody!['enable_thinking'], isTrue);
      expect(firstBody!['thinking_budget'], 4096);
    });

    test('MiMo v2.6-pro 对话轮开 thinking、工具轮关 thinking', () async {
      int requestCount = 0;
      final List<Map<String, dynamic>> bodies = <Map<String, dynamic>>[];

      final AiTool fakeTool = AiTool(
        name: 'get_status',
        description: 's',
        parameters: const <String, dynamic>{'type': 'object', 'properties': <String, dynamic>{}},
        execute: (_) async => '{"ok":true}',
      );

      const String sseRound1 = '''data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_m","type":"function","function":{"name":"get_status","arguments":"{}"}}]}}]}
data: [DONE]''';
      const String sseRound2 = '''data: {"choices":[{"delta":{"content":"读完了。"}}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        bodies.add(jsonDecode(body) as Map<String, dynamic>);
        final String sse = requestCount == 1 ? sseRound1 : sseRound2;
        return Stream<String>.fromIterable(sse.split('\n'));
      }

      final AiClient client = AiClient(
        apiKey: 'sk-x',
        model: 'mimo-v2.6-pro',
        tools: <AiTool>[fakeTool],
        transport: fakeTransport,
      );
      await client
          .complete(history: <ChatMessage>[
            ChatMessage(role: ChatRole.user, content: '状态', time: DateTime(2026)),
          ])
          .drain<void>();

      expect(requestCount, 2);
      // 对话轮（第 1 次请求）开 thinking。
      expect(bodies[0]['enable_thinking'], isTrue);
      expect(bodies[0]['thinking_budget'], 4096);
      // 工具轮（第 2 次请求，刚回灌 tool 结果）关 thinking：不下发该键。
      expect(bodies[1].containsKey('enable_thinking'), isFalse);
    });

    test('thinkingConfigForModel 映射表：默认关、SF interleaved 开、MiMo 工具轮关', () {
      expect(AiClient.thinkingConfigForModel('Qwen/Qwen2.5-7B-Instruct', toolRound: false).enabled, isFalse);
      expect(AiClient.thinkingConfigForModel('deepseek-ai/DeepSeek-V3.2', toolRound: true).enabled, isTrue);
      expect(AiClient.thinkingConfigForModel('zai-org/GLM-4.7', toolRound: true).enabled, isTrue);
      expect(AiClient.thinkingConfigForModel('mimo-v2.6-pro', toolRound: false).enabled, isTrue);
      expect(AiClient.thinkingConfigForModel('mimo-v2.6-pro', toolRound: true).enabled, isFalse);
    });
  });

  group('buildAnthropicRequestJson（接口层形状）', () {
    test('system 提为顶层、tools 用 type=custom + input_schema', () {
      final List<ChatMessage> messages = <ChatMessage>[
        ChatMessage(role: ChatRole.system, content: '你是 SDR 助手', time: DateTime(2026)),
        ChatMessage(role: ChatRole.user, content: '调谐到 145M', time: DateTime(2026)),
      ];
      const Map<String, dynamic> params = <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'frequency_hz': <String, dynamic>{'type': 'integer'},
        },
      };
      final AiTool tool = AiTool(
        name: 'set_frequency',
        description: '调谐',
        parameters: params,
        execute: (_) async => '',
      );

      final Map<String, dynamic> body = AiClient.buildAnthropicRequestJson(
        messages: messages,
        tools: <AiTool>[tool],
        stream: false,
        model: 'mimo-v2.6-pro',
      );

      expect(body['system'], '你是 SDR 助手');
      expect(body['model'], 'mimo-v2.6-pro');
      final List<dynamic> tools = body['tools']! as List<dynamic>;
      final Map<String, dynamic> t0 = tools[0]! as Map<String, dynamic>;
      expect(t0['type'], 'custom');
      expect(t0['name'], 'set_frequency');
      expect(t0['input_schema'], params);

      // system 不进 messages。
      final List<dynamic> msgs = body['messages']! as List<dynamic>;
      expect(msgs.length, 1);
      expect((msgs[0]! as Map<String, dynamic>)['role'], 'user');
    });
  });

  group('历史折叠 compactHistory（G1 AI 上下文压缩）', () {
    ChatMessage m(ChatRole r, String c) =>
        ChatMessage(role: r, content: c, time: DateTime(2026));

    // 构造：1 条 system + 3 组 user/assistant（user 文本都很长以撑爆小预算）。
    List<ChatMessage> sampleHistory() => <ChatMessage>[
          m(ChatRole.system, '你是 SDR 助手'),
          m(ChatRole.user, 'U1: ${'x' * 200}'),
          m(ChatRole.assistant, 'R1'),
          m(ChatRole.user, 'U2: ${'y' * 200}'),
          m(ChatRole.assistant, 'R2'),
          m(ChatRole.user, 'U3: ${'z' * 200}'),
          m(ChatRole.assistant, 'R3'),
        ];

    test('预算内 → 原样全量直传，不折叠', () {
      final List<ChatMessage> h = sampleHistory();
      final ContextCompaction c = AiClient.compactHistory(
        h,
        budgetChars: 100000, // 远超
      );
      expect(c.didCompact, isFalse);
      expect(c.compressedUserTurns, 0);
      expect(c.messages, same(h));
    });

    test('超预算 → 折叠最旧轮为 system 占位，保留最近 N 个 user 轮原文', () {
      final List<ChatMessage> h = sampleHistory();
      // keepRecentUserTurns=2 → 保留 U2/U3，折叠 U1。
      final ContextCompaction c = AiClient.compactHistory(
        h,
        budgetChars: 100,
        keepRecentUserTurns: 2,
      );
      expect(c.didCompact, isTrue);
      expect(c.compressedUserTurns, 1, reason: '只折叠了 U1 这一轮 user');

      // 前导 system 指令原样保留在最前。
      expect(c.messages.first.role, ChatRole.system);
      expect(c.messages.first.content, '你是 SDR 助手');

      // 占位是紧跟其后的第二条，role=system，诚实标注折叠且列出 U1 诉求。
      final ChatMessage placeholder = c.messages[1];
      expect(placeholder.role, ChatRole.system);
      expect(placeholder.content, contains('已折叠为占位'));
      expect(placeholder.content, contains('可随时回看')); // 不静默丢原文
      expect(placeholder.content, contains('U1:'));

      // 最近 U2 / U3 原文逐句保留。
      final String joined = c.messages.map((ChatMessage e) => e.content).join('\n');
      expect(joined, contains('U2:'));
      expect(joined, contains('U3:'));
      // U1 的长文本不再作为独立 user 消息上送（被折进占位）。
      final bool u1AsUser = c.messages
          .any((ChatMessage e) => e.role == ChatRole.user && e.content.contains('U1:'));
      expect(u1AsUser, isFalse);
    });

    test('user 轮次少于保留数 → 即便超预算也不折叠（保住当前提问）', () {
      final List<ChatMessage> h = sampleHistory();
      // keepRecentUserTurns=10，总共只有 3 个 user 轮 → 不折叠。
      final ContextCompaction c = AiClient.compactHistory(
        h,
        budgetChars: 100,
        keepRecentUserTurns: 10,
      );
      expect(c.didCompact, isFalse);
      expect(c.compressedUserTurns, 0);
      expect(c.messages.length, h.length);
    });

    test('经 complete()：注入小预算后，占位与最近原文确实进了上送请求体', () async {
      String? firstBody;
      int requestCount = 0;

      const String sseStop = '''data: {"choices":[{"delta":{"content":"ok"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]''';

      Future<Stream<String>> fakeTransport(
        Uri url,
        Map<String, String> headers,
        String body,
      ) async {
        requestCount++;
        firstBody = body;
        return Stream<String>.fromIterable(sseStop.split('\n'));
      }

      final AiClient client = AiClient(apiKey: 'sk-x', transport: fakeTransport);

      final List<ChatMessage> history = <ChatMessage>[
        ChatMessage(role: ChatRole.system, content: 'sys', time: DateTime(2026)),
        ChatMessage(role: ChatRole.user, content: 'OLD: ${'q' * 300}', time: DateTime(2026)),
        ChatMessage(role: ChatRole.assistant, content: 'old reply', time: DateTime(2026)),
        ChatMessage(role: ChatRole.user, content: 'CURRENT ask', time: DateTime(2026)),
      ];

      // keepRecentUserTurns=1 → 保留最后一个 user（CURRENT），折叠 OLD。
      await client
          .complete(
            history: history,
            compactBudgetChars: 100,
            keepRecentUserTurns: 1,
          )
          .drain<void>();

      expect(requestCount, 1);
      final Map<String, dynamic> req =
          jsonDecode(firstBody!) as Map<String, dynamic>;
      final List<dynamic> msgs = req['messages']! as List<dynamic>;

      // 占位消息出现，且列出 OLD 诉求。
      final bool hasPlaceholder = msgs.cast<Map<String, dynamic>>().any(
            (Map<String, dynamic> m) =>
                m['role'] == 'system' &&
                (m['content']! as String).contains('OLD:'),
          );
      expect(hasPlaceholder, isTrue);
      // 当前提问 CURRENT ask 原文保留。
      final bool hasCurrent = msgs.cast<Map<String, dynamic>>().any(
            (Map<String, dynamic> m) => m['content'] == 'CURRENT ask',
          );
      expect(hasCurrent, isTrue);
      // OLD 长文不再作为独立 user 消息上送。
      final bool oldAsUser = msgs.cast<Map<String, dynamic>>().any(
            (Map<String, dynamic> m) =>
                m['role'] == 'user' &&
                (m['content']! as String).startsWith('OLD:'),
          );
      expect(oldAsUser, isFalse);
    });
  });
}
