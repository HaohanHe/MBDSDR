// 信号活动日志：持久化/与书签分离/上限/清空/页面空态与渲染。
//
// 用内存 KvStore 注入，不触碰真实存储。活动日志与书签是两套独立键：
// 加活动不得影响书签，加书签不得写入活动日志。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/pages/activity_log_page.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';
import 'package:provider/provider.dart';

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

class _MemSecure implements SecureStore {
  @override
  Future<String?> get(String k) async => null;
  @override
  Future<void> set(String k, String v) async {}
  @override
  Future<void> delete(String k) async {}
}

SettingsService _newService() =>
    SettingsService(kv: _MemKv(), secure: _MemSecure());

void main() {
  group('SettingsService 活动日志', () {
    test('追加真实观察：newest-first，拒绝假频率', () async {
      final s = _newService();
      await s.load();
      expect(s.addActivity(frequencyHz: 144e6.round(), levelDbfs: -40, mode: 'nfm', epochMsUtc: 1000), isTrue);
      expect(s.addActivity(frequencyHz: 145e6.round(), levelDbfs: -50, mode: 'wfm', epochMsUtc: 2000), isTrue);
      // 后插入的在前（newest-first）。
      expect(s.activities.first.frequencyHz, 145e6.round());
      // 频率 <=0 拒绝。
      expect(s.addActivity(frequencyHz: 0, levelDbfs: -1), isFalse);
      expect(s.addActivity(frequencyHz: -5, levelDbfs: -1), isFalse);
      expect(s.activities.length, 2);
    });

    test('与书签严格分离：互写不串', () async {
      final s = _newService();
      await s.load();
      s.addActivity(frequencyHz: 144e6.round(), levelDbfs: -40, epochMsUtc: 1);
      s.addBookmark(name: '测试台', frequencyHz: 145e6.round(), mode: 'nfm');
      expect(s.activities.length, 1);
      expect(s.bookmarks.length, 1);
      // 活动里那条不是书签；书签也不在活动里。
      expect(s.activities.first.frequencyHz, 144e6.round());
      expect(s.bookmarks.first.frequencyHz, 145e6.round());
    });

    test('超过上限丢最旧；清空后为空', () async {
      final s = _newService();
      await s.load();
      for (var i = 0; i < kActivityLogMaxEntries + 10; i++) {
        s.addActivity(frequencyHz: 100000000 + i, levelDbfs: -60, epochMsUtc: i);
      }
      expect(s.activities.length, kActivityLogMaxEntries);
      // newest-first：最新的在最前。
      expect(s.activities.first.frequencyHz,
          100000000 + (kActivityLogMaxEntries + 10 - 1));
      s.clearActivities();
      expect(s.activities, isEmpty);
    });

    test('落盘并读回（持久化）', () async {
      final kv = _MemKv();
      final s1 = SettingsService(kv: kv, secure: _MemSecure());
      await s1.load();
      s1.addActivity(
          frequencyHz: 137100000, levelDbfs: -42.5, mode: 'wfm', epochMsUtc: 1700000000000);
      // 新实例读同一份 kv。
      final s2 = SettingsService(kv: kv, secure: _MemSecure());
      await s2.load();
      expect(s2.activities.length, 1);
      expect(s2.activities.first.frequencyHz, 137100000);
      expect(s2.activities.first.levelDbfs, closeTo(-42.5, 1e-9));
      expect(s2.activities.first.mode, 'wfm');
    });
  });

  group('ActivityLogPage', () {
    Widget wrapPage(SettingsService s) => MaterialApp(
          home: ChangeNotifierProvider<SettingsService>.value(
            value: s,
            child: const ActivityLogPage(),
          ),
        );

    testWidgets('空态：无记录时诚实空态，清空按钮禁用', (tester) async {
      final s = _newService();
      await s.load();
      await tester.pumpWidget(wrapPage(s));
      expect(find.text('暂无信号活动'), findsOneWidget);
      // 空态时清空按钮不可点。
      expect(
        tester
            .widget<IconButton>(
              find.widgetWithIcon(IconButton, Icons.delete_sweep_outlined),
            )
            .onPressed,
        isNull,
      );
    });

    testWidgets('有记录：渲染频率/模式/电平/来源', (tester) async {
      final s = _newService();
      await s.load();
      s.addActivity(
          frequencyHz: 145800000, levelDbfs: -35.2, mode: 'wfm', epochMsUtc: 1700000000000);
      await tester.pumpWidget(wrapPage(s));
      expect(find.text('暂无信号活动'), findsNothing);
      expect(find.textContaining('145.8000 MHz'), findsOneWidget);
      expect(find.textContaining('WFM'), findsOneWidget);
      expect(find.textContaining('值守'), findsOneWidget);
      expect(find.textContaining('-35.2 dBFS'), findsOneWidget);
    });

    testWidgets('清空：带确认，确认后列表清空', (tester) async {
      final s = _newService();
      await s.load();
      s.addActivity(frequencyHz: 144000000, levelDbfs: -40, epochMsUtc: 1);
      await tester.pumpWidget(wrapPage(s));
      expect(find.textContaining('144.0000 MHz'), findsOneWidget);

      await tester.tap(find.byIcon(Icons.delete_sweep_outlined));
      await tester.pumpAndSettle();
      // 确认对话框出现。
      expect(find.text('清空活动日志'), findsOneWidget);
      await tester.tap(find.text('清空'));
      await tester.pumpAndSettle();
      expect(find.text('暂无信号活动'), findsOneWidget);
      expect(s.activities, isEmpty);
    });
  });
}
