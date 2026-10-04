// SPDX-License-Identifier: MIT
// ChatSessionStore 单元测试 —— 全部注入 InMemoryKvStore，不碰真实 SharedPreferences。
// 覆盖：首跑单空会话 / 索引+会话 JSON 形状与桌面同形 / CRUD / 切换 / 删除永不归零 /
//       incomplete 标记 / 新 user 行清 incomplete / 损坏索引回退。
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
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

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('首跑', () {
    test('空存储 → 恰好一个空会话「新会话」并被选中，不预置假对话', () async {
      final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
      await store.load();
      expect(store.sessions.length, 1);
      expect(store.sessions.single.title, '新会话');
      expect(store.currentId, store.sessions.single.id);
      expect(store.currentMessages(), isEmpty);
    });
  });

  group('JSON 形状（与桌面同形）', () {
    test('index.json + sessions/<id>.json 字段名对齐桌面', () async {
      final _MemKv kv = _MemKv();
      final ChatSessionStore store = ChatSessionStore(kv: kv);
      await store.load();
      final String id = store.currentId;

      store.appendLine(
        id,
        const SessionLine(role: 'user', content: '你好', kind: 'chat', tsMs: 1700000000000),
      );
      store.appendLine(
        id,
        const SessionLine(role: 'assistant', content: '在的', kind: 'chat'),
      );

      // index.json 形状。
      final Map<String, dynamic> index =
          jsonDecode(kv.getString('aiSessionsIndex')!) as Map<String, dynamic>;
      expect(index['version'], ChatSessionStore.kIndexVersion);
      expect(index['current'], id);
      final List<dynamic> sessions = index['sessions']! as List<dynamic>;
      expect(sessions.length, 1);
      final Map<String, dynamic> info = sessions.single! as Map<String, dynamic>;
      expect(info['id'], id);
      expect(info['title'], '新会话');
      expect(info.containsKey('updatedAt'), isTrue);
      expect(info.containsKey('incomplete'), isTrue);

      // sessions/<id>.json 形状。
      final Map<String, dynamic> doc =
          jsonDecode(kv.getString('aiSession.$id')!) as Map<String, dynamic>;
      expect(doc['id'], id);
      expect(doc['title'], '新会话');
      final List<dynamic> msgs = doc['messages']! as List<dynamic>;
      expect(msgs.length, 2);
      final Map<String, dynamic> m0 = msgs[0]! as Map<String, dynamic>;
      expect(m0['role'], 'user');
      expect(m0['content'], '你好');
      expect(m0['kind'], 'chat');
      expect(m0['ts'], 1700000000000); // 有 ts 才写
      final Map<String, dynamic> m1 = msgs[1]! as Map<String, dynamic>;
      expect(m1['role'], 'assistant');
      expect(m1['content'], '在的');
      expect(m1.containsKey('ts'), isFalse); // ts=0 时省略，绝不臆造
    });
  });

  group('CRUD / 切换', () {
    test('新建 → 列表多一条并切到它；切换 → current 跟随', () async {
      final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
      await store.load();
      final String first = store.currentId;

      final String second = store.createSession(title: '第二段');
      expect(store.sessions.length, 2);
      expect(store.currentId, second);

      store.setCurrent(first);
      expect(store.currentId, first);
      store.setCurrent('不存在的id');
      expect(store.currentId, first); // 忽略非法 id
    });

    test('删除：多会话时真删；单会话时清空而非删空（永不归零）', () async {
      final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
      await store.load();
      final String a = store.currentId;
      store.appendLine(a, const SessionLine(role: 'user', content: 'hi'));

      final String b = store.createSession();
      expect(store.sessions.length, 2);

      // 删掉 a（非当前）。
      store.deleteSession(a);
      expect(store.sessions.length, 1);
      expect(store.currentId, b);

      // 只剩一个时删除 → 清空而不是删到 0。
      store.deleteSession(b);
      expect(store.sessions.length, 1);
      expect(store.currentMessages(), isEmpty);
      expect(store.sessions.single.title, '新会话');
    });
  });

  group('incomplete 标记', () {
    test('流式开始置 true、收尾置 false；新 user 行自动清', () async {
      final ChatSessionStore store = ChatSessionStore(kv: _MemKv());
      await store.load();
      final String id = store.currentId;

      store.setIncomplete(id, true);
      expect(store.isIncomplete(id), isTrue);
      expect(store.sessions.single.incomplete, isTrue);

      // 新一轮 user 消息自动清掉 incomplete。
      store.appendLine(id, const SessionLine(role: 'user', content: '继续'));
      expect(store.isIncomplete(id), isFalse);
    });
  });

  group('持久化往返 / 健壮性', () {
    test('重载后会话与消息都回来；current 指向正确', () async {
      final _MemKv kv = _MemKv();
      final ChatSessionStore store = ChatSessionStore(kv: kv);
      await store.load();
      final String id = store.currentId;
      store.appendLine(id, const SessionLine(role: 'user', content: '你好'));
      store.appendLine(id, const SessionLine(role: 'assistant', content: '在的'));

      // 新实例从同一 Kv 读回。
      final ChatSessionStore restored = ChatSessionStore(kv: kv);
      await restored.load();
      expect(restored.sessions.length, 1);
      expect(restored.currentId, id);
      expect(restored.currentMessages().length, 2);
      expect(restored.currentMessages().first.content, '你好');
    });

    test('损坏的索引 JSON → 回退到单空会话，不抛', () async {
      final _MemKv kv = _MemKv();
      await kv.setString('aiSessionsIndex', '{{{ not json');
      final ChatSessionStore store = ChatSessionStore(kv: kv);
      await store.load();
      expect(store.sessions.length, 1);
      expect(store.currentMessages(), isEmpty);
    });
  });
}
