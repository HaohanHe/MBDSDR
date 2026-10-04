// ControlHubClient + 值对象：按冻结 HTTP 契约解析，服务不可达诚实抛错。
//
// 用注入的 fake transport 完全离线：不触碰真实网络。覆盖：
//   * 四路 GET 按真实 JSON 形状解析（地址/文本/呼号/CRC/方位/质量/Morse）；
//   * 缺字段/类型不符容错回退，不抛；
//   * 服务不可达（SocketException）-> 抛 ControlHubException，文案诚实；
//   * 非 JSON 响应 -> 抛协议错。
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/control_hub.dart';
import 'package:mbdsdr_mobile/services/control_hub_client.dart';

/// 按路径返回冻结契约形状的真实 JSON。
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
        'count': 2,
        'messages': <dynamic>[
          <String, dynamic>{
            'address': 12345,
            'function': 3,
            'text': 'hello alpha',
            'type': 'alpha',
          },
          <String, dynamic>{
            'address': 999,
            'function': 0,
            'text': '123456',
            'type': 'numeric',
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

ControlHubClient _clientWith(Future<String> Function(Uri uri) transport) {
  return ControlHubClient(
    host: '127.0.0.1',
    port: 9999,
    transport: transport,
    maxAttempts: 1, // 测试不重试，瞬时失败立即翻成错误。
  );
}

void main() {
  group('值对象 fromJson 容错', () {
    test('PocsagMessage 解析 address/function/text/type', () {
      final PocsagMessage m = PocsagMessage.fromJson(<String, dynamic>{
            'address': 12345,
            'function': 3,
            'text': 'hi',
            'type': 'alpha',
          })!;
      expect(m.address, 12345);
      expect(m.function, 3);
      expect(m.text, 'hi');
      expect(m.type, PocsagType.alpha);
    });

    test('PocsagMessage 缺地址丢弃；type 数字枚举容错', () {
      expect(PocsagMessage.fromJson(<String, dynamic>{'text': 'x'}), isNull);
      expect(PocsagMessage.fromJson(<String, dynamic>{'address': 1, 'type': 1})!.type,
          PocsagType.numeric);
    });

    test('M17Call 解析呼号/CRC/语音未解码；bytes 数组转 hex', () {
      final M17Call c = M17Call.fromJson(<String, dynamic>{
            'src': 'JA1XXX',
            'dst': 'CQCQCQ',
            'is_stream': true,
            'crc_ok': true,
            'voice_undecoded': true,
            'payload': <int>[0xde, 0xad],
          })!;
      expect(c.src, 'JA1XXX');
      expect(c.dst, 'CQCQCQ');
      expect(c.crcOk, isTrue);
      expect(c.voiceUndecoded, isTrue);
      expect(c.payload, 'dead'); // 数字数组 -> 小写 hex
    });

    test('VorRadial 未锁定 -> unlocked，不据噪声造方位', () {
      final VorRadial v = VorRadial.fromJson(<String, dynamic>{'locked': false});
      expect(v.locked, isFalse);
      expect(v.radialDeg, 0);
    });

    test('EngineStatus 缺字段回退 empty', () {
      expect(EngineStatus.fromJson('garbage'), EngineStatus.empty);
    });
  });

  group('ControlHubClient（fake transport）', () {
    test('四路 GET 解析真实契约形状', () async {
      final c = _clientWith((uri) async => _bodyFor(uri.path));

      final EngineStatus s = await c.fetchStatus();
      expect(s.connected, isTrue);
      expect(s.frequencyHz, 144000000);
      expect(s.mode, 'nfm');

      final List<PocsagMessage> pocsag = await c.fetchPocsagMessages();
      expect(pocsag.length, 2);
      expect(pocsag.first.address, 12345);
      expect(pocsag.first.text, 'hello alpha');
      expect(pocsag[1].type, PocsagType.numeric);

      final List<M17Call> calls = await c.fetchM17Calls();
      expect(calls.single.src, 'JA1XXX');
      expect(calls.single.voiceUndecoded, isTrue);

      final VorRadial vor = await c.fetchVorRadial();
      expect(vor.locked, isTrue);
      expect(vor.radialDeg, closeTo(123.4, 0.01));
      expect(vor.morseId, 'ABC');
    });

    test('无数据 -> count:0 / locked:false 诚实空态形状', () async {
      final c = _clientWith((uri) async {
        switch (uri.path) {
          case '/pocsag_messages':
            return jsonEncode(<String, dynamic>{'count': 0, 'messages': <dynamic>[]});
          case '/m17_calls':
            return jsonEncode(<String, dynamic>{'count': 0, 'calls': <dynamic>[]});
          case '/vor_radial':
            return jsonEncode(<String, dynamic>{
              'locked': false,
              'radial_deg': 0,
              'quality': 0,
              'morse_id': '',
            });
          default:
            return '{}';
        }
      });
      expect(await c.fetchPocsagMessages(), isEmpty);
      expect(await c.fetchM17Calls(), isEmpty);
      expect((await c.fetchVorRadial()).locked, isFalse);
    });

    test('服务不可达（SocketException）-> ControlHubException 诚实文案', () async {
      final c = _clientWith((uri) async =>
          throw const SocketException('Connection refused'));
      expect(
        () => c.fetchStatus(),
        throwsA(isA<ControlHubException>().having(
          (e) => e.message,
          'message',
          contains('无法连接桌面 ControlHub'),
        )),
      );
    });

    test('非 JSON 响应 -> 协议错', () async {
      final c = _clientWith((uri) async => '<html>502</html>');
      expect(() => c.fetchStatus(), throwsA(isA<ControlHubException>()));
    });
  });
}
