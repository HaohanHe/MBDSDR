// 录音列表：空态诚实、有元数据时按时间/频率/模式渲染。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/pages/recordings_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
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

class _MemSecure implements SecureStore {
  @override
  Future<String?> get(String k) async => null;
  @override
  Future<void> set(String k, String v) async {}
  @override
  Future<void> delete(String k) async {}
}

/// 只读 fake：RecordingsPage 只读取连接/静噪/频率/模式。
class _StatusRadio implements RadioApi {
  @override
  ConnectionStatus get status => ConnectionStatus.disconnected;
  @override
  bool get squelchEnabled => false;
  @override
  bool get squelchOpen => false;
  @override
  int get freqHz => 137100000;
  @override
  DemodMode get mode => DemodMode.wfm;
  @override
  dynamic noSuchMethod(Invocation i) =>
      throw UnimplementedError(i.memberName.toString());
}

Widget _wrap(Widget child) => MaterialApp(home: child);

void main() {
  testWidgets('空态：无录音时显示「暂无录音」且无假条目', (tester) async {
    final settings = SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    await tester.pumpWidget(_wrap(RecordingsPage(
      radio: _StatusRadio(),
      settings: settings,
    )));
    expect(find.text('暂无录音'), findsOneWidget);
    expect(find.textContaining('暂未实现真实文件录制'), findsOneWidget);
    // 音频路由状态真实反映「未连接」。
    expect(find.textContaining('未连接'), findsOneWidget);
  });

  testWidgets('有元数据时：按频率/模式/时间渲染列表', (tester) async {
    final settings = SettingsService(kv: _MemKv(), secure: _MemSecure());
    await settings.load();
    settings.addRecording(RecordingMeta(
      startedAtEpochMs:
          DateTime.utc(2024, 6, 1, 10, 30).millisecondsSinceEpoch,
      frequencyHz: 145800000,
      mode: 'wfm',
      note: 'NOAA 19',
    ));
    await tester.pumpWidget(_wrap(RecordingsPage(
      radio: _StatusRadio(),
      settings: settings,
    )));
    expect(find.text('暂无录音'), findsNothing);
    expect(find.textContaining('145.8000 MHz'), findsOneWidget);
    expect(find.textContaining('NOAA 19'), findsOneWidget);
  });
}
