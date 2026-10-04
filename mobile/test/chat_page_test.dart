// SPDX-License-Identifier: MIT
// ChatPage widget 测试 —— 全部注入 fake ChatTransport，禁止真实联网。
// 覆盖：
//   a) 无 key → 诚实空态（引导去设置，不假输出）；
//   b) 会话 CRUD / 切换渲染；
//   c) 流式行完成固化去重（assistant 行只落盘一次）；
//   d) 出错时保留已流式 partial 内容。
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/pages/chat_page.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';
import 'package:mbdsdr_mobile/services/chat_session_store.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';

class _MemKv implements KvStore {
  final Map<String, Object?> d = <String, Object?>{};
  @override
  String? getString(String k) => d[k] as String?;
  @override
  Future<void> setString(String k, String v) async => d[k] = v;
  @override
  double? getDouble(String k) => d[k] as double?;
  @override
  Future<void> setDouble(String k, double v) async => d[k] = v;
  @override
  int? getInt(String k) => d[k] as int?;
  @override
  Future<void> setInt(String k, int v) async => d[k] = v;
  @override
  bool? getBool(String k) => d[k] as bool?;
  @override
  Future<void> setBool(String k, bool v) async => d[k] = v;
  @override
  Future<void> remove(String k) async => d.remove(k);
}

/// 构造一个返回固定 SSE 文本、随后正常 done 的 fake transport。
ChatTransport _textTransport(String text) {
  return (Uri url, Map<String, String> headers, String body) async {
    final String chunk =
        'data: {"choices":[{"delta":{"content":${jsonEncode(text)}}}]}';
    return Stream<String>.fromIterable(<String>[
      chunk,
      'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
      'data: [DONE]',
    ]);
  };
}

Widget _wrap(ChatPage page) => MaterialApp(
      debugShowCheckedModeBanner: false,
      home: Scaffold(body: page),
    );

bool _spanHas(InlineSpan? span, String s) {
  bool walk(InlineSpan? sp) {
    if (sp is TextSpan) {
      if (sp.text?.contains(s) ?? false) return true;
      return (sp.children ?? <InlineSpan>[]).any(walk);
    }
    return false;
  }

  return walk(span);
}

/// 覆盖 Text / SelectableText / SelectableText.rich 的文本查找（气泡用 rich）。
Finder findRich(String s) => find.byWidgetPredicate((Widget w) {
      if (w is SelectableText) {
        if (w.data?.contains(s) ?? false) return true;
        return _spanHas(w.textSpan, s);
      }
      if (w is Text) return w.data?.contains(s) ?? false;
      return false;
    });

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('无 key → 诚实空态：引导去设置，不渲染任何气泡', (tester) async {
    bool openedSettings = false;
    await tester.pumpWidget(_wrap(ChatPage(
      isConfigured: false,
      clientFactory: () => AiClient(apiKey: 'x'),
      onOpenSettings: () => openedSettings = true,
    )));
    await tester.pump();

    expect(find.text('尚未配置 AI API key'), findsOneWidget);
    expect(find.text('去设置'), findsOneWidget);
    expect(find.byType(TextField), findsNothing,
        reason: '未配置时输入框应禁用/不出现');

    await tester.tap(find.text('去设置'));
    await tester.pump();
    expect(openedSettings, isTrue);
  });

  testWidgets('会话列表：新建 + 切换渲染正确', (tester) async {
    final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
    await store.load();
    final String s1 = store.currentId;
    store.appendLine(s1, const SessionLine(role: 'user', content: '第一条会话的问题'));
    store.appendLine(s1, const SessionLine(role: 'assistant', content: '第一条会话的回答'));

    final String s2 = store.createSession(title: '第二段');
    store.appendLine(s2, const SessionLine(role: 'user', content: '第二段的问题'));

    await tester.pumpWidget(_wrap(ChatPage(
      isConfigured: true,
      store: store,
      clientFactory: () => AiClient(apiKey: 'x', transport: _textTransport('ok')),
    )));
    await tester.pump();

    // 当前是新建的第二段：只看到第二段的问题。
    expect(findRich('第二段的问题'), findsOneWidget);
    expect(findRich('第一条会话的回答'), findsNothing);

    // 打开抽屉，看到两个会话：当前「第二段」与「新会话」(s1)。
    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();
    expect(find.text('第二段'), findsWidgets,
        reason: '顶栏与抽屉行都应出现当前会话');
    expect(find.text('新会话'), findsOneWidget, reason: '抽屉里应列出第一段');
    // 切回第一段（标题「新会话」那条）。
    await tester.tap(find.text('新会话'));
    await tester.pumpAndSettle();
    expect(findRich('第一条会话的回答'), findsOneWidget);
    expect(findRich('第二段的问题'), findsNothing);
  });

  testWidgets('流式行：partial 显示，完成固化且 assistant 行只落盘一次（去重）',
      (tester) async {
    final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
    await store.load();

    await tester.pumpWidget(_wrap(ChatPage(
      isConfigured: true,
      store: store,
      clientFactory: () => AiClient(apiKey: 'x', transport: _textTransport('Hello')),
    )));
    await tester.pump();

    await tester.enterText(find.byType(TextField), '你好');
    await tester.pump();
    await tester.tap(find.byIcon(Icons.send));
    // 让 SSE 流跑完。
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    // UI 上 assistant 完整文本只出现一次。
    expect(findRich('Hello'), findsOneWidget);

    // 落盘：user 一行 + assistant 一行，assistant 不重复。
    final List<SessionLine> lines = store.currentMessages();
    expect(lines.length, 2);
    expect(lines[0].role, 'user');
    expect(lines[1].role, 'assistant');
    expect(lines[1].content, 'Hello');
  });

  testWidgets('出错：保留已流式 partial 文本，并展示错误', (tester) async {
    final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
    await store.load();

    // 流式吐了「Par」之后 transport 抛错。
    Future<Stream<String>> flakyTransport(
        Uri url, Map<String, String> headers, String body) async {
      final StreamController<String> c = StreamController<String>();
      c.onListen = () {
        c.add('data: {"choices":[{"delta":{"content":"Par"}}]}');
        c.addError(AiException('Request failed (HTTP 401): Unauthorized'));
        c.close();
      };
      return c.stream;
    }

    await tester.pumpWidget(_wrap(ChatPage(
      isConfigured: true,
      store: store,
      clientFactory: () => AiClient(apiKey: 'x', transport: flakyTransport),
    )));
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'hi');
    await tester.pump();
    await tester.tap(find.byIcon(Icons.send));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    // partial 正文没有被擦除。
    expect(findRich('Par'), findsOneWidget);
    // 错误说明出现。
    expect(find.textContaining('HTTP 401'), findsOneWidget);
    // partial 也被固化，不丢。
    expect(store.currentMessages().last.content, 'Par');
  });
}
