// SPDX-License-Identifier: MIT
// 时空状态卡片：纯模型（SpacetimeStatus.fromServices）空态/真实两分支 + widget 渲染。
// 红线：无硬件时四格必须落到诚实空态（system 钟告诫 / 无 fix / 无目标 / 未补偿），
// 绝不编造坐标、多普勒数字或 GNSS 授时。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/gnss/gnss_fix.dart';
import 'package:mbdsdr_mobile/pages/spacetime_status.dart';

GnssFix _fix({
  String source = 'none',
  double? lat,
  double? lon,
  int? sats,
  String? utc,
  double? hdop,
}) =>
    GnssFix(
      source: source,
      latitude: lat,
      longitude: lon,
      satellites: sats,
      utcTime: utc,
      hdop: hdop,
      fixQuality: (lat != null && lon != null) ? 1 : 0,
    );

void main() {
  group('SpacetimeStatus.fromServices 诚实空态', () {
    test('无 fix + 未连接 + 无目标 -> 四格全空态', () {
      final s = SpacetimeStatus.fromServices(fix: GnssFix.empty);
      expect(s.timeSource.text, contains('system'));
      expect(s.timeSource.role, SpRole.warn); // 本机时钟，诚实告诫
      expect(s.gnss.text, '无 fix');
      expect(s.gnss.role, SpRole.neutral);
      expect(s.target.text, '无接收目标');
      expect(s.doppler.text, '未补偿（无目标）');
    });

    test('有 NMEA 语句但无定位（fixQuality=0）-> 仍诚实无 fix', () {
      final s = SpacetimeStatus.fromServices(
        fix: _fix(source: 'real', utc: '072545.00'),
      );
      // 带 UTC -> 时间源认 GNSS 授时（即便没定位）。
      expect(s.timeSource.text, contains('GNSS'));
      expect(s.timeSource.role, SpRole.ok);
      expect(s.gnss.text, '无 fix');
    });
  });

  group('真实分支', () {
    test('真实 fix + UTC -> 时间源 GNSS、GNSS 格出坐标', () {
      final s = SpacetimeStatus.fromServices(
        fix: _fix(source: 'real', lat: 39.9042, lon: 116.4074, sats: 9, utc: '072545.00'),
      );
      expect(s.timeSource.text, contains('GNSS 072545.00'));
      expect(s.gnss.text, contains('39.90420,116.40740'));
      expect(s.gnss.text, contains('星9'));
      // 未带 HDOP（旧用例不传）-> 不追加该段，保持向后兼容。
      expect(s.gnss.text, isNot(contains('HDOP')));
    });

    test('真实 fix 带 HDOP -> 与桌面 spTileGnss 同口径展示 HDOP x.x', () {
      final s = SpacetimeStatus.fromServices(
        fix: _fix(source: 'real', lat: 39.9042, lon: 116.4074, sats: 9,
            utc: '072545.00', hdop: 0.8),
      );
      expect(s.gnss.text, contains('39.90420,116.40740'));
      expect(s.gnss.text, contains('星9'));
      // 桌面格式："坐标 · 星N · HDOP 0.8"。
      expect(s.gnss.text, contains('HDOP 0.8'));
      expect(s.gnss.role, SpRole.ok);
    });

    test('已连接 + 有频率但无目标 -> 接收目标显示当前频率', () {
      final s = SpacetimeStatus.fromServices(
        fix: GnssFix.empty,
        radioConnected: true,
        freqHz: 137100000,
      );
      expect(s.target.text, contains('137.1000 MHz'));
      // 无目标 -> 多普勒仍空态（不能因为连接了就编一个补偿值）。
      expect(s.doppler.text, '未补偿（无目标）');
    });

    test('有目标 + 真实多普勒 -> 显示补偿值数字；无 Doppler 不显示数字', () {
      final withDop = SpacetimeStatus.fromServices(
        fix: GnssFix.empty,
        targetName: 'NOAA-19',
        dopplerHz: -12.5,
      );
      expect(withDop.target.text, contains('NOAA-19'));
      expect(withDop.doppler.text, contains('补偿值 -12.5 Hz'));

      final noDop = SpacetimeStatus.fromServices(
        fix: GnssFix.empty,
        targetName: 'NOAA-19',
      );
      // 有目标但没有真实 range-rate -> 不编数字（仍"未补偿"，区别于"补偿中"）。
      expect(noDop.doppler.text, '未补偿（无目标）');
    });
  });

  testWidgets('widget：空态渲染四格标题与诚实文案', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: SpacetimeStatusCard()),
    ));
    expect(find.text('时空状态'), findsOneWidget);
    expect(find.text('时间源'), findsOneWidget);
    expect(find.text('接收目标'), findsOneWidget);
    expect(find.text('多普勒补偿'), findsOneWidget);
    expect(find.text('GNSS 定位'), findsOneWidget);
    expect(find.text('无 fix'), findsOneWidget);
    expect(find.text('未补偿（无目标）'), findsOneWidget);
    expect(find.text('无接收目标'), findsOneWidget);
  });
}
