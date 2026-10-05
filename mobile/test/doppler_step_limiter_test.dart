// SPDX-License-Identifier: MIT
// DopplerStepLimiter 纯逻辑单测（镜像桌面 cpp/src/core/sat_capture.h）。
// 确定性：无时钟/无 IO，仅验证「每拍向目标最多迈 maxStepHz」的收敛语义。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/services/doppler_step_limiter.dart';

void main() {
  group('DopplerStepLimiter（对齐桌面 kDopplerMaxStepHz=${AppTokens.dopplerAutoMaxStepHz.toInt()}）', () {
    test('reset 绑定起始 VFO，不产生步进', () {
      final l = DopplerStepLimiter();
      l.reset(137_100_000);
      expect(l.currentHz, 137_100_000);
      expect(l.armed, isTrue);
    });

    test('一步可达（|diff| ≤ maxStep）：精确落在目标', () {
      final l = DopplerStepLimiter();
      l.reset(1_000_000.0);
      expect(l.advance(1_001_500), 1_001_500); // 差 1500 ≤ 2000
    });

    test('大步跳变：每拍有界步进 ±maxStep，不一步蹦过去', () {
      final l = DopplerStepLimiter();
      l.reset(1_000_000.0);
      // 目标远在 +100000 Hz：每拍只能走 +2000。
      expect(l.advance(1_100_000), 1_002_000);
      expect(l.advance(1_100_000), 1_004_000);
      // 反向同理：从 1_004_000 向 900_000 一步退 2000。
      expect(l.advance(900_000), 1_002_000);
    });

    test('连续多拍有界收敛到目标', () {
      final l = DopplerStepLimiter(maxStepHz: 100);
      l.reset(0);
      double cur = 0;
      for (var i = 0; i < 20; i++) {
        cur = l.advance(500);
      }
      expect(cur, 500, reason: '20 拍 × 100 = 2000 ≥ 500，应收敛到目标');
    });

    test('disarm 后下次 advance 重新绑定（不从旧值硬拉回）', () {
      final l = DopplerStepLimiter();
      l.reset(1_000_000.0);
      l.advance(1_002_000);
      l.disarm();
      expect(l.armed, isFalse);
      // 再次绑定：直接落到新目标，不沿旧轨迹走。
      expect(l.advance(400_000), 400_000);
      expect(l.armed, isTrue);
    });
  });
}
