// RemoteDecoderPanel：mock HTTP 返回真实契约结构 -> 面板渲染断言；
// 服务不可达 -> 诚实空态；VOR 未锁定 -> 不画指针、方位读「—」。
//
// transport 注入 fake，完全离线。轮询间隔传极大值，避免定时器在断言期间自触发；
// initState 的首次拉取用 pump 推进微任务完成。
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/control_hub.dart';
import 'package:mbdsdr_mobile/services/control_hub_client.dart';
import 'package:mbdsdr_mobile/widgets/remote_decoder_panel.dart';

String _bodyFor(String path) {
  switch (path) {
    case '/status':
      return jsonEncode(<String, dynamic>{
        'connected': true,
        'status': 'rx',
        'error_message': '',
        'mode': 'nfm',
        'frequency_hz': 144000000,
        'bandwidth_hz': 12500,
      });
    case '/pocsag_messages':
      return jsonEncode(<String, dynamic>{
        'count': 1,
        'messages': <dynamic>[
          <String, dynamic>{
            'address': 12345,
            'function': 3,
            'text': 'hello alpha',
            'type': 'alpha',
          },
        ],
      });
    case '/m17_calls':
      return jsonEncode(<String, dynamic>{
        'count': 1,
        'calls': <dynamic>[
          <String, dynamic>{
            'src': 'JA1XXX',
            'dst': 'CQCQCQ',
            'type': 0,
            'is_stream': true,
            'crc_ok': true,
            'voice_undecoded': true,
            'meta': '',
            'payload': '',
          },
        ],
      });
    case '/vor_radial':
      return jsonEncode(<String, dynamic>{
        'locked': true,
        'radial_deg': 123.4,
        'quality': 0.82,
        'morse_id': 'ABC',
      });
    default:
      return '{}';
  }
}

ControlHubClient _fakeClient(Future<String> Function(Uri uri) transport) {
  return ControlHubClient(
    host: '127.0.0.1',
    port: 9,
    transport: transport,
    maxAttempts: 1,
  );
}

Widget _wrap(Widget child) => MaterialApp(
      theme: ThemeData.dark(useMaterial3: true),
      home: Scaffold(
        body: SingleChildScrollView(child: child),
      ),
    );

void main() {
  testWidgets('成功：渲染真实远程结果（RIC 地址/文本/M17 呼号/VOR 方位）',
      (tester) async {
    final client = _fakeClient((uri) async => _bodyFor(uri.path));
    await tester.pumpWidget(_wrap(RemoteDecoderPanel(
      client: client,
      pollInterval: const Duration(days: 1),
    )));
    await tester.pump(); // 跑首帧（loading）
    await tester.pump(const Duration(milliseconds: 100)); // 等微任务完成 -> ready

    // 数据来源标注。
    expect(find.text('远程引擎 · POCSAG'), findsOneWidget);
    expect(find.text('远程引擎 · M17'), findsOneWidget);
    expect(find.text('远程引擎 · VOR'), findsOneWidget);

    // POCSAG：RIC 地址 + 解码文本。
    expect(find.text('RIC 12345'), findsOneWidget);
    expect(find.text('hello alpha'), findsOneWidget);

    // M17：呼号 src -> dst。
    expect(find.textContaining('JA1XXX'), findsOneWidget);
    // 语音帧标未解码。
    expect(find.text('语音 · 未解码'), findsOneWidget);

    // VOR：锁定后显示真实方位角度。
    expect(find.text('123°'), findsOneWidget);
    expect(find.text('已锁定'), findsOneWidget);
  });

  testWidgets('服务不可达 -> 诚实空态 + 重试按钮（不伪造数据）', (tester) async {
    final client = _fakeClient(
        (uri) async => throw const SocketException('Connection refused'));
    await tester.pumpWidget(_wrap(RemoteDecoderPanel(
      client: client,
      pollInterval: const Duration(days: 1),
    )));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('无法连接远程解码引擎'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    // 服务不可达时绝不渲染假读数。
    expect(find.text('RIC 12345'), findsNothing);
  });

  testWidgets('未配置服务地址 -> 引导去设置空态', (tester) async {
    await tester.pumpWidget(_wrap(const RemoteDecoderPanel(client: null)));
    await tester.pump();
    expect(find.text('未配置桌面 ControlHub'), findsOneWidget);
  });

  testWidgets('POCSAG 空列表 -> 诚实空态文案', (tester) async {
    await tester.pumpWidget(_wrap(const PocsagMessageList(messages: [])));
    expect(find.text('暂无寻呼消息（桌面引擎无 POCSAG 解码）'), findsOneWidget);
  });

  testWidgets('M17 空列表 -> 诚实空态文案', (tester) async {
    await tester.pumpWidget(_wrap(const M17CallList(calls: [])));
    expect(find.text('暂无 M17 呼叫（桌面引擎无 M17 解码）'), findsOneWidget);
  });

  testWidgets('VOR 未锁定 -> 方位读「—」，不显示伪造方位', (tester) async {
    await tester.pumpWidget(_wrap(const VorRadialView(
      result: VorRadial.unlocked,
    )));
    expect(find.text('—'), findsOneWidget);
    expect(find.text('未锁定'), findsOneWidget);
    expect(find.textContaining('未锁定：不显示方位'), findsOneWidget);
    // 不出现任何伪造角度。
    expect(find.textContaining(RegExp(r'\d+°')), findsNothing);
  });

  testWidgets('VOR 锁定 -> 显示真实方位角度', (tester) async {
    await tester.pumpWidget(_wrap(const VorRadialView(
      result: VorRadial(
        locked: true,
        radialDeg: 90,
        quality: 0.5,
        morseId: 'XYZ',
      ),
    )));
    expect(find.text('90°'), findsOneWidget);
    expect(find.text('已锁定'), findsOneWidget);
    expect(find.textContaining('XYZ'), findsOneWidget);
  });
}
