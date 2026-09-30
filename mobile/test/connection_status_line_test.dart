// ConnectionStatusLine：安静状态行文案与真实错误透出。
//
// 用内存 FakeRadioApi（实现 RadioApi）脚本化 status / errorMessage，
// 不触碰真实网络。覆盖：运行中掉线 →「设备断开，等待重插」；
// 失败 → 展示真实原因；已连接 → 低调已连接。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/widgets/connection_status_line.dart';

/// 内存脚本化 RadioApi：只记录状态/错误，其余方法空实现。
class FakeRadioApi implements RadioApi {
  FakeRadioApi({
    this.status = ConnectionStatus.disconnected,
    this.errorMessage,
  });

  @override
  ConnectionStatus status;

  @override
  String? errorMessage;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} 本测试不需要');
}

Widget _wrap(Widget child) => MaterialApp(
      theme: ThemeData.dark(useMaterial3: true),
      home: Scaffold(body: Center(child: child)),
    );

void main() {
  testWidgets('运行中掉线（reconnecting）显示「设备断开，等待重插」', (tester) async {
    final radio = FakeRadioApi(status: ConnectionStatus.reconnecting);
    await tester.pumpWidget(_wrap(ConnectionStatusLine(radio: radio)));
    expect(find.text('设备断开，等待重插'), findsOneWidget);
    // 禁用红黄警示大贴纸：不应出现 danger 色。
    expect(find.byWidgetPredicate(
      (w) => w is Text && w.style?.color == AppTokens.danger,
    ), findsNothing);
  });

  testWidgets('失败（error）展示真实错误文案', (tester) async {
    final radio = FakeRadioApi(
      status: ConnectionStatus.error,
      errorMessage: '连接失败: SocketException: Connection refused',
    );
    await tester.pumpWidget(_wrap(ConnectionStatusLine(radio: radio)));
    expect(find.textContaining('Connection refused'), findsOneWidget);
  });

  testWidgets('失败无 message 时回退通用「连接失败」', (tester) async {
    final radio = FakeRadioApi(status: ConnectionStatus.error);
    await tester.pumpWidget(_wrap(ConnectionStatusLine(radio: radio)));
    expect(find.text('连接失败'), findsOneWidget);
  });

  testWidgets('已连接显示低调「rtl_tcp 已连接」', (tester) async {
    final radio = FakeRadioApi(status: ConnectionStatus.connected);
    await tester.pumpWidget(_wrap(ConnectionStatusLine(radio: radio)));
    expect(find.text('rtl_tcp 已连接'), findsOneWidget);
  });

  testWidgets('连接中显示「连接中…」', (tester) async {
    final radio = FakeRadioApi(status: ConnectionStatus.connecting);
    await tester.pumpWidget(_wrap(ConnectionStatusLine(radio: radio)));
    expect(find.text('连接中…'), findsOneWidget);
  });
}
