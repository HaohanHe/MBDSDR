// rtl_tcp 协议字节自检（纯函数，无需真实硬件）。
library;

import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/services/rtl_tcp_client.dart';

void main() {
  group('buildCommand', () {
    test('设频率 100_000_000 Hz 为正确大端字节', () {
      // 100_000_000 = 0x05F5E100。
      final cmd = RtlTcpClient.buildCommand(0x01, 100000000);
      expect(cmd, hasLength(5));
      expect(cmd[0], 0x01);
      expect(cmd[1], 0x05);
      expect(cmd[2], 0xF5);
      expect(cmd[3], 0xE1);
      expect(cmd[4], 0x00);
    });

    test('增益 20.0 dB → 200 (0xC8)', () {
      final tenths = (20.0 * 10).round(); // 200
      final cmd = RtlTcpClient.buildCommand(0x04, tenths);
      expect(cmd[0], 0x04);
      expect(cmd[1], 0x00);
      expect(cmd[2], 0x00);
      expect(cmd[3], 0x00);
      expect(cmd[4], 0xC8);
    });

    test('设采样率 2.048 MHz 大端', () {
      // 2_048_000 = 0x001F4000。
      final cmd = RtlTcpClient.buildCommand(0x02, 2048000);
      expect(cmd[1], 0x00);
      expect(cmd[2], 0x1F);
      expect(cmd[3], 0x40);
      expect(cmd[4], 0x00);
    });

    test('AGC 开 / bias-T 开', () {
      final agc = RtlTcpClient.buildCommand(0x08, 1);
      expect(agc, [0x08, 0, 0, 0, 0x01]);
      final bias = RtlTcpClient.buildCommand(0x0e, 1);
      expect(bias, [0x0e, 0, 0, 0, 0x01]);
    });
  });

  group('decodeIq', () {
    test('交错 uint8 → 归一化浮点', () {
      // I=128 (~0), Q=0 (-1); I=255 (+1), Q=127 (~0)。
      final bytes = Uint8List.fromList([128, 0, 255, 127]);
      final decoded = RtlTcpClient.decodeIq(bytes);
      expect(decoded.i.length, 2);
      expect(decoded.q.length, 2);
      // (128-127.5)/127.5
      expect(decoded.i[0], closeTo(0.5 / 127.5, 1e-6));
      expect(decoded.q[0], closeTo((0 - 127.5) / 127.5, 1e-6));
      expect(decoded.i[1], closeTo((255 - 127.5) / 127.5, 1e-6));
      expect(decoded.q[1], closeTo((127 - 127.5) / 127.5, 1e-6));
    });

    test('中性字节 127/128 近似 0', () {
      final decoded = RtlTcpClient.decodeIq(Uint8List.fromList([127, 128]));
      expect(decoded.i[0].abs() < 0.01, isTrue);
      expect(decoded.q[0].abs() < 0.01, isTrue);
    });
  });
}
