// SPDX-License-Identifier: MIT
// ============================================================================
// ChatSessionStore —— 移动端 AI 会话（多会话）持久化
// ----------------------------------------------------------------------------
// 与桌面 cpp/src/ai/ai_session_store.{h,cpp} 的**文档形状**对齐，可互读同一
// 段 JSON；诚实标注容器差异（见下「互操作边界」）。
//
// 索引文档（key = [_kIndexKey]，等价桌面 <dir>/index.json）：
// ```json
// {
//   "version": 1,
//   "current": "<id>",
//   "sessions": [
//     { "id": "...", "title": "...", "updatedAt": <epochMs>, "incomplete": <bool> }
//   ]
// }
// ```
//
// 会话文档（key = aiSession.<id>，等价桌面 <dir>/sessions/<id>.json）：
// ```json
// {
//   "id": "...",
//   "title": "...",
//   "incomplete": <bool>,
//   "messages": [
//     { "role": "user|assistant", "content": "...",
//       "kind": "chat", "ts": <epochMs 可选> }
//   ]
// }
// ```
//
// 互操作边界（诚实）：
//   * 字段名 / 层级 / 取值与桌面逐字段对齐，一段会话 JSON 可在两端之间手工
//     复制；
//   * 但**容器不同**：桌面写真实文件（<AppData>/ai_sessions/index.json +
//     sessions/<id>.json），移动端把同形 JSON 字符串存进 KvStore（SharedPreferences），
//     移动端不读桌面的磁盘目录，桌面也不读移动端的 SharedPreferences；
//   * 移动端只持久化 user/assistant 纯文本行（kind="chat"）。流式期间的 tool_call /
//     tool_result 分片、reasoning_content 属瞬态，不进磁盘——重载后会话恢复为
//     纯文本气泡，历史 tool chip 不重建（这是与桌面「行内工具事件」的已知差异）。
//
// 测试用 InMemoryKvStore 注入，不触碰真实 SharedPreferences。
// ============================================================================

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';

import 'settings_service.dart';

/// 会话列表里的一条轻量索引（不含消息正文）。对齐桌面 SessionInfo。
@immutable
class SessionInfo {
  final String id;
  final String title;

  /// 最近一次追加/改名的时刻（epoch ms）。
  final int updatedAtMs;

  /// 上一条流式回复写到一半被打断（未正常收尾）。
  final bool incomplete;

  const SessionInfo({
    required this.id,
    required this.title,
    required this.updatedAtMs,
    this.incomplete = false,
  });

  SessionInfo copyWith({String? title, int? updatedAtMs, bool? incomplete}) {
    return SessionInfo(
      id: id,
      title: title ?? this.title,
      updatedAtMs: updatedAtMs ?? this.updatedAtMs,
      incomplete: incomplete ?? this.incomplete,
    );
  }
}

/// 一条持久化聊天行。对齐桌面 SessionMessage（只取移动端用到的子集）。
@immutable
class SessionLine {
  /// "user" | "assistant"。
  final String role;
  final String content;

  /// "chat"（移动端当前只写 chat；读端对缺失 kind 回退 role，向后兼容）。
  final String kind;

  /// epoch ms；0 = 未知/旧数据，绝不臆造。
  final int tsMs;

  const SessionLine({
    required this.role,
    required this.content,
    this.kind = 'chat',
    this.tsMs = 0,
  });

  Map<String, dynamic> toJson() {
    final Map<String, dynamic> out = <String, dynamic>{
      'role': role,
      'content': content,
      'kind': kind,
    };
    if (tsMs != 0) out['ts'] = tsMs;
    return out;
  }

  static SessionLine? fromJson(Object? raw) {
    if (raw is! Map<String, dynamic>) return null;
    final Object? role = raw['role'];
    final Object? content = raw['content'];
    if (role is! String || content is! String) return null;
    final Object? kindRaw = raw['kind'];
    final String kind =
        kindRaw is String && kindRaw.isNotEmpty ? kindRaw : role;
    final Object? ts = raw['ts'];
    return SessionLine(
      role: role,
      content: content,
      kind: kind,
      tsMs: ts is num ? ts.toInt() : 0,
    );
  }
}

class ChatSessionStore extends ChangeNotifier {
  ChatSessionStore({required KvStore kv}) : _kv = kv;

  final KvStore _kv;

  /// 索引文档落盘键（等价桌面 index.json）。
  static const String _kIndexKey = 'aiSessionsIndex';

  /// 索引 schema 版本（桌面 kIndexVersion=1）。
  static const int kIndexVersion = 1;

  /// 首个空会话默认标题（与桌面一致）。
  static const String kDefaultTitle = '新会话';

  final Map<String, SessionInfo> _meta = <String, SessionInfo>{};
  final List<String> _order = <String>[]; // 最旧在前
  final Map<String, List<SessionLine>> _cache = <String, List<SessionLine>>{};
  String _currentId = '';

  bool _loaded = false;

  /// 是否已 load() 过。
  bool get isLoaded => _loaded;

  /// 会话列表（最旧在前，不可变视图）。
  List<SessionInfo> get sessions => List<SessionInfo>.unmodifiable(
        _order.map((String id) => _meta[id]).whereType<SessionInfo>(),
      );

  String get currentId => _currentId;

  SessionInfo? get current {
    if (_currentId.isEmpty) return null;
    return _meta[_currentId];
  }

  /// 当前会话的消息（不可变视图）。
  List<SessionLine> currentMessages() => messagesFor(_currentId);

  List<SessionLine> messagesFor(String id) {
    final List<SessionLine>? cached = _cache[id];
    if (cached != null) {
      return List<SessionLine>.unmodifiable(cached);
    }
    return const <SessionLine>[];
  }

  // ------------------------------------------------------------------ CRUD
  /// 新建一个空会话并切到它；持久化并通知。返回新会话 id。
  String createSession({String title = ''}) {
    final String id = _makeId();
    final int now = DateTime.now().millisecondsSinceEpoch;
    final SessionInfo info = SessionInfo(
      id: id,
      title: title.trim().isEmpty ? kDefaultTitle : title.trim(),
      updatedAtMs: now,
    );
    _meta[id] = info;
    _order.add(id);
    _cache[id] = <SessionLine>[];
    _currentId = id;
    _persistIndex();
    _persistSession(id);
    notifyListeners();
    return id;
  }

  /// 切到指定会话；id 不存在或就是当前则忽略。
  void setCurrent(String id) {
    if (!_meta.containsKey(id) || id == _currentId) return;
    _currentId = id;
    _persistIndex();
    notifyListeners();
  }

  /// 删除会话。永不允许零会话：只剩一个时清空它而不是删掉。
  void deleteSession(String id) {
    if (!_meta.containsKey(id)) return;
    if (_order.length == 1) {
      final SessionInfo only = _meta[id]!;
      _meta[id] = only.copyWith(
        title: kDefaultTitle,
        updatedAtMs: DateTime.now().millisecondsSinceEpoch,
        incomplete: false,
      );
      _cache[id] = <SessionLine>[];
      _persistIndex();
      _persistSession(id);
      notifyListeners();
      return;
    }
    _meta.remove(id);
    _cache.remove(id);
    _order.remove(id);
    if (_currentId == id) _currentId = _order.last;
    _persistIndex();
    notifyListeners();
  }

  // ------------------------------------------------------------------ 重命名
  /// 重命名会话：更新 index 里的 title 与会话文档里的 title（同一处真值，两份落盘同步）。
  ///
  /// id 不存在 → 静默忽略；新标题 trim 后为空 → 不改名（不把会话清成空白标题）。
  /// 重命名不改动消息内容，仅刷新 updatedAtMs。
  void renameSession(String id, String title) {
    final SessionInfo? info = _meta[id];
    if (info == null) return;
    final String trimmed = title.trim();
    if (trimmed.isEmpty) return;
    if (trimmed == info.title) return; // 无变化不写盘。
    _meta[id] = info.copyWith(
      title: trimmed,
      updatedAtMs: DateTime.now().millisecondsSinceEpoch,
    );
    _persistIndex();
    _persistSession(id);
    notifyListeners();
  }

  // ------------------------------------------------------------------ 消息
  /// 追加一条消息。新 user 行自动清掉上一次的 incomplete 标记（操作者已翻篇）。
  void appendLine(String id, SessionLine line) {    if (!_meta.containsKey(id)) return;
    (_cache[id] ??= <SessionLine>[]).add(line);
    final SessionInfo info = _meta[id]!;
    final bool clearsIncomplete = line.role == 'user' && info.incomplete;
    _meta[id] = info.copyWith(
      updatedAtMs: DateTime.now().millisecondsSinceEpoch,
      incomplete: clearsIncomplete ? false : null,
    );
    _persistIndex();
    _persistSession(id);
    notifyListeners();
  }

  /// 标记当前会话「流式回复写到一半」。新一轮开始 = true，正常收尾 = false。
  void setIncomplete(String id, bool incomplete) {
    final SessionInfo? info = _meta[id];
    if (info == null || info.incomplete == incomplete) return;
    _meta[id] = info.copyWith(incomplete: incomplete);
    _persistIndex();
    _persistSession(id);
    notifyListeners();
  }

  bool isIncomplete(String id) => _meta[id]?.incomplete ?? false;

  // ------------------------------------------------------------------ 加载
  /// 启动时读回索引与各会话。非法/缺失一律回退到「恰好一个空会话」，绝不抛。
  Future<void> load() async {
    _loaded = true;
    final String? raw = _kv.getString(_kIndexKey);
    if (raw == null || raw.isEmpty) {
      createSession(title: kDefaultTitle);
      return;
    }
    try {
      final Object? decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        createSession(title: kDefaultTitle);
        return;
      }
      final Object? sessionsRaw = decoded['sessions'];
      if (sessionsRaw is! List || sessionsRaw.isEmpty) {
        createSession(title: kDefaultTitle);
        return;
      }
      for (final Object? s in sessionsRaw) {
        if (s is! Map<String, dynamic>) continue;
        final Object? id = s['id'];
        final Object? title = s['title'];
        if (id is! String || id.isEmpty) continue;
        final Object? updatedAt = s['updatedAt'];
        final Object? incomplete = s['incomplete'];
        _meta[id] = SessionInfo(
          id: id,
          title: title is String && title.isNotEmpty ? title : kDefaultTitle,
          updatedAtMs: updatedAt is num ? updatedAt.toInt() : 0,
          incomplete: incomplete == true,
        );
        _order.add(id);
        _cache[id] = _readSessionLines(id);
      }
      if (_order.isEmpty) {
        createSession(title: kDefaultTitle);
        return;
      }
      final Object? current = decoded['current'];
      final String currentId = current is String ? current : '';
      _currentId = (_meta.containsKey(currentId)) ? currentId : _order.last;
    } on FormatException {
      // 索引损坏：回退到单个空会话，不崩。
      _meta.clear();
      _order.clear();
      _cache.clear();
      createSession(title: kDefaultTitle);
    }
  }

  List<SessionLine> _readSessionLines(String id) {
    final String? raw = _kv.getString(_sessionKey(id));
    if (raw == null || raw.isEmpty) return <SessionLine>[];
    try {
      final Object? decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) return <SessionLine>[];
      final Object? msgs = decoded['messages'];
      if (msgs is! List) return <SessionLine>[];
      return msgs
          .map(SessionLine.fromJson)
          .whereType<SessionLine>()
          .toList(growable: true);
    } on FormatException {
      return <SessionLine>[];
    }
  }

  // ------------------------------------------------------------------ 落盘
  String _sessionKey(String id) => 'aiSession.$id';

  void _persistIndex() {
    final Map<String, dynamic> obj = <String, dynamic>{
      'version': kIndexVersion,
      'current': _currentId,
      'sessions': _order
          .map((String id) => _meta[id])
          .whereType<SessionInfo>()
          .map((SessionInfo i) => <String, dynamic>{
                'id': i.id,
                'title': i.title,
                'updatedAt': i.updatedAtMs,
                'incomplete': i.incomplete,
              })
          .toList(growable: false),
    };
    unawaited(_kv.setString(_kIndexKey, jsonEncode(obj)));
  }

  void _persistSession(String id) {
    final SessionInfo? info = _meta[id];
    if (info == null) return;
    final Map<String, dynamic> obj = <String, dynamic>{
      'id': id,
      'title': info.title,
      'incomplete': info.incomplete,
      'messages': (_cache[id] ?? const <SessionLine>[])
          .map((SessionLine l) => l.toJson())
          .toList(growable: false),
    };
    unawaited(_kv.setString(_sessionKey(id), jsonEncode(obj)));
  }

  /// 生成一个足够唯一的会话 id（无 uuid 包依赖；时间微秒 + 随机数）。
  String _makeId() {
    final int micros = DateTime.now().microsecondsSinceEpoch;
    final int rand = Random().nextUint32();
    return '${micros.toRadixString(36)}-${rand.toRadixString(36)}';
  }
}

/// 扩展：给 Random 一个无符号 32bit 随机数（dart 核心无直接 API）。
extension on Random {
  int nextUint32() => nextInt(4294967296);
}
