// DeviceInfoCard：设备信息真实回读与诚实空态。
//
// 用内存 FakeRadioApi（实现 RadioApi）脚本化 status / sampleRateHz / freqHz，
// 不触碰真实网络。覆盖：
//   * 已连接 → 渲染真实采样率（Msps）、当前频率（MHz）、后端 rtl_tcp、状态「已连接」；
//   * 未连接 → 状态「未连接」，后端/采样率/频率一律空态「—」，不拿上次配置冒充。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/widgets/device_info_card.dart';

/// 内存脚本化 RadioApi：只记录本次用例关心的字段，其余方法空实现。
class FakeRadioApi implements RadioApi {
  FakeRadioApi({
    this.status = ConnectionStatus.disconnected,
    this.sampleRateHz = 0,
    this.freqHz = 0,
  });

  @override
  ConnectionStatus status;

  @override
  double sampleRateHz;

  @override
  int freqHz;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} 本测试不需要');
}

Widget _wrap(Widget child) => MaterialApp(
      theme: ThemeData.dark(useMaterial3: true),
      home: Scaffold(body: Center(child: child)),
    );

void main() {
  testWidgets('已连接：渲染真实采样率/频率/后端/状态', (tester) async {
    final radio = FakeRadioApi(
      status: ConnectionStatus.connected,
      sampleRateHz: 2.048e6,
      freqHz: 144000000,
    );
    await tester.pumpWidget(_wrap(DeviceInfoCard(radio: radio)));

    expect(find.text('已连接'), findsOneWidget);
    expect(find.text('rtl_tcp'), findsOneWidget);
    // 2.048 MHz → toStringAsFixed(2) = 2.05
    expect(find.text('2.05 Msps'), findsOneWidget);
    // 144.0 MHz → toStringAsFixed(4) = 144.0000
    expect(find.text('144.0000 MHz'), findsOneWidget);
    // 已连接时不应出现空态占位。
    expect(find.text('—'), findsNothing);
  });

  testWidgets('未连接：诚实空态「未连接」，其余读数为「—」', (tester) async {
    // 即便内存里残留上次配置值（2.048e6 / 144 MHz），未连接也不得渲染。
    final radio = FakeRadioApi(
      status: ConnectionStatus.disconnected,
      sampleRateHz: 2.048e6,
      freqHz: 144000000,
    );
    await tester.pumpWidget(_wrap(DeviceInfoCard(radio: radio)));

    expect(find.text('未连接'), findsOneWidget);
    expect(find.text('rtl_tcp'), findsNothing);
    expect(find.text('2.05 Msps'), findsNothing);
    expect(find.text('144.0000 MHz'), findsNothing);
    // 后端/采样率/频率三行均为空态占位。
    expect(find.text('—'), findsNWidgets(3));
  });

  testWidgets('连接中：状态「连接中…」，读数空态', (tester) async {
    final radio = FakeRadioApi(status: ConnectionStatus.connecting);
    await tester.pumpWidget(_wrap(DeviceInfoCard(radio: radio)));

    expect(find.text('连接中…'), findsOneWidget);
    expect(find.text('—'), findsNWidgets(3));
  });
}
