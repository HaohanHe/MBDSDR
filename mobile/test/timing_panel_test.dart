// 授时面板三态与诚实空态测试。
//
// 注入 fake TimingSource 覆盖 hasFix / noFix / noModule；系统钟恒为真实时钟文本。
// 移动端无 NMEA，默认态必须显示「无 GNSS 授时」，绝不伪造 fix。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/widgets/timing_panel.dart';

class _FakeSource implements TimingSource {
  final TimingQuality quality;
  final DateTime? fix;
  final DateTime sys;
  _FakeSource({required this.quality, this.fix, required this.sys});

  @override
  TimingSnapshot snapshot() => TimingSnapshot(
        quality: quality,
        systemTimeUtc: sys,
        gnssFixTimeUtc: fix,
      );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // 每个用例手动构造后卸载，避免周期 Timer 挂起报错。
  Future<void> pumpPanel(WidgetTester tester, TimingSource src) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: TimingPanel(source: src)),
    ));
    await tester.pump();
  }

  testWidgets('noModule（移动端默认）：诚实空态「无 GNSS 授时」，显示真实系统钟',
      (tester) async {
    final sys = DateTime.utc(2026, 10, 1, 8, 0, 0);
    await pumpPanel(tester, _FakeSource(quality: TimingQuality.noModule, sys: sys));
    expect(find.text('无 GNSS 授时'), findsOneWidget);
    expect(find.textContaining('系统 08:00:00 UTC'), findsOneWidget);
    expect(find.textContaining('Δt'), findsNothing);
    // 卸载以停掉周期 Timer。
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('noFix：显示「GNSS 无 fix」，不显示 Δt', (tester) async {
    final sys = DateTime.utc(2026, 10, 1, 8, 0, 1);
    await pumpPanel(tester, _FakeSource(quality: TimingQuality.noFix, sys: sys));
    expect(find.text('GNSS 无 fix'), findsOneWidget);
    expect(find.textContaining('Δt'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('hasFix：显示 fix 时间与 Δt(ms)，绝不空态', (tester) async {
    final sys = DateTime.utc(2026, 10, 1, 8, 0, 2, 120); // +120ms
    final fix = DateTime.utc(2026, 10, 1, 8, 0, 2, 60); // fix 早 60ms
    await pumpPanel(tester, _FakeSource(quality: TimingQuality.hasFix, fix: fix, sys: sys));
    expect(find.textContaining('Δt 60 ms'), findsOneWidget);
    expect(find.textContaining('fix 08:00:02'), findsOneWidget);
    expect(find.text('无 GNSS 授时'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 2));
  });

  test('TimingSnapshot.delta：无 fix 为 null；有 fix 为绝对差', () {
    final sys = DateTime.utc(2026, 1, 1, 0, 0, 0, 200);
    final s = TimingSnapshot(
      quality: TimingQuality.noFix,
      systemTimeUtc: DateTime.utc(2026, 1, 1, 0, 0, 0, 200),
    );
    expect(s.delta, isNull);
    final h = TimingSnapshot(
      quality: TimingQuality.hasFix,
      systemTimeUtc: sys,
      gnssFixTimeUtc: DateTime.utc(2026, 1, 1, 0, 0, 0, 50),
    );
    expect(h.delta!.inMilliseconds, 150);
  });
}
