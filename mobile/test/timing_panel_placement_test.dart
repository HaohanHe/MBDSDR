// 授时面板的位置与按需创建测试（spec Task 6.1 / 规则 2）。
//
// 钉死两件事：
//   1. 观测主界面（频谱页）不再出现授时面板——底层机制不占主界面；
//   2. 设置页「时间与授时」默认收起：收起时不创建 TimingPanel（1 Hz 计时器
//      不跑），展开才创建，再收起即销毁。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/l10n/gen/app_localizations.dart';
import 'package:mbdsdr_mobile/pages/settings_page.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';
import 'package:mbdsdr_mobile/widgets/timing_panel.dart';

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

Widget _wrapLocale(Widget home, {Locale locale = const Locale('zh')}) =>
    MaterialApp(
      debugShowCheckedModeBanner: false,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: locale,
      home: home,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('频谱页不再内嵌授时面板（底层机制不占观测主界面）', (tester) async {
    await tester.pumpWidget(_wrapLocale(
      Scaffold(
        body: SpectrumPage(
          controller: RadioController(),
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    ));
    await tester.pump();

    expect(find.byType(TimingPanel), findsNothing);
    expect(find.text('无 GNSS 授时'), findsNothing);
    expect(find.textContaining('系统 '), findsNothing);
  });

  testWidgets('设置页「时间与授时」按需创建：收起不建、展开才建、再收即销毁',
      (tester) async {
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = SettingsService(
      kv: _MemKv(),
      secure: _MemSecure(),
    );
    await settings.load();
    final RadioController radio = RadioController();

    await tester.pumpWidget(_wrapLocale(
      Scaffold(body: SettingsPage(settings: settings, radio: radio)),
    ));
    await tester.pump();

    // 分区标题在设备信息之后：滚到可见（ListView 懒构建）。
    await tester.scrollUntilVisible(
      find.text('时间与授时'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('时间与授时'), findsOneWidget);
    // 收起态：面板（及其 1 Hz 计时器）根本没被创建。
    expect(find.byType(TimingPanel), findsNothing);

    await tester.tap(find.text('时间与授时'));
    await tester.pumpAndSettle();
    expect(find.byType(TimingPanel), findsOneWidget);
    expect(find.text('无 GNSS 授时'), findsOneWidget);
    expect(find.textContaining('不伪造 fix'), findsOneWidget);

    // 再收起：maintainState=false -> 子组件销毁，计时器随之取消。
    await tester.tap(find.text('时间与授时'));
    await tester.pumpAndSettle();
    expect(find.byType(TimingPanel), findsNothing);

    // 卸载以停掉任何残余周期 Timer。
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('小屏 320×640 展开「时间与授时」不溢出', (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = SettingsService(
      kv: _MemKv(),
      secure: _MemSecure(),
    );
    await settings.load();
    final RadioController radio = RadioController();

    await tester.pumpWidget(_wrapLocale(
      Scaffold(body: SettingsPage(settings: settings, radio: radio)),
    ));
    await tester.pump();

    await tester.scrollUntilVisible(
      find.text('时间与授时'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('时间与授时'));
    await tester.pumpAndSettle();

    // 小屏展开后不得出现 RenderFlex overflow / 布局异常。
    expect(tester.takeException(), isNull);
    expect(find.byType(TimingPanel), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('en locale：时间分区与授时面板显示英文', (tester) async {
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = SettingsService(
      kv: _MemKv(),
      secure: _MemSecure(),
    );
    await settings.load();
    final RadioController radio = RadioController();

    await tester.pumpWidget(_wrapLocale(
      Scaffold(body: SettingsPage(settings: settings, radio: radio)),
      locale: const Locale('en'),
    ));
    await tester.pump();

    await tester.scrollUntilVisible(
      find.text('Time & timing'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Time & timing'));
    await tester.pumpAndSettle();

    // 面板内容也走 gen-l10n，不再中英混排。
    expect(find.text('No GNSS timing'), findsOneWidget);
    expect(find.textContaining('System '), findsOneWidget);
    expect(find.text('无 GNSS 授时'), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('ja locale：时间分区与授时面板显示日文', (tester) async {
    tester.view.physicalSize = const Size(400, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final SettingsService settings = SettingsService(
      kv: _MemKv(),
      secure: _MemSecure(),
    );
    await settings.load();
    final RadioController radio = RadioController();

    await tester.pumpWidget(_wrapLocale(
      Scaffold(body: SettingsPage(settings: settings, radio: radio)),
      locale: const Locale('ja'),
    ));
    await tester.pump();

    await tester.scrollUntilVisible(
      find.text('時刻と同期'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('時刻と同期'));
    await tester.pumpAndSettle();

    expect(find.text('GNSS 時刻なし'), findsOneWidget);
    expect(find.textContaining('システム '), findsOneWidget);
    expect(find.text('无 GNSS 授时'), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });
}
