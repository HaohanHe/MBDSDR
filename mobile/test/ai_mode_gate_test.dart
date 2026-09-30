// AI 模式 gate：AI 接管 vs 手动。
//
// 手动模式下：写动作（set_frequency 等）不得真正调用 RadioApi，
// 只返回「手动模式：未执行」的 JSON；只读 get_status 仍放行。
// AI 接管模式下：set_frequency 必须真正下发频率。
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/ai_tools.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';

/// 记录写调用的内存 FakeRadioApi。
class RecordingRadio implements RadioApi {
  int setFrequencyCalls = 0;
  int? lastFreqHz;

  @override
  ConnectionStatus status = ConnectionStatus.connected;

  @override
  String? errorMessage;

  @override
  int freqHz = 100000000;

  @override
  DemodMode mode = DemodMode.nfm;

  @override
  double gainDb = 20;

  @override
  bool autoGain = true;

  @override
  double sampleRateHz = 2.048e6;

  @override
  double volume = 1;

  @override
  bool muted = false;

  @override
  Future<void> setFrequencyHz(int hz) async {
    setFrequencyCalls++;
    lastFreqHz = hz;
    freqHz = hz;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} 本测试不需要');
}

void main() {
  test('手动模式：set_frequency 不真正调谐，返回 gated 结果', () async {
    final radio = RecordingRadio();
    final tools = buildRadioTools(radio, manualMode: true);
    final setFreq = tools.firstWhere((t) => t.name == 'set_frequency');

    final result = await setFreq.execute(<String, dynamic>{'frequency_mhz': 145.0});
    final decoded = jsonDecode(result) as Map<String, dynamic>;

    expect(radio.setFrequencyCalls, 0, reason: '手动模式不得真正下发频率');
    expect(decoded['gated'], true);
    expect(decoded['error'], contains('手动模式：未执行'));
  });

  test('手动模式：get_status 只读放行，返回真实状态', () async {
    final radio = RecordingRadio();
    final tools = buildRadioTools(radio, manualMode: true);
    final getStatus = tools.firstWhere((t) => t.name == 'get_status');

    final result = await getStatus.execute(<String, dynamic>{});
    final decoded = jsonDecode(result) as Map<String, dynamic>;

    expect(decoded['ok'], true);
    expect(decoded['frequency_hz'], 100000000);
  });

  test('AI 接管（默认）：set_frequency 真正下发频率', () async {
    final radio = RecordingRadio();
    final tools = buildRadioTools(radio); // manualMode 默认 false
    final setFreq = tools.firstWhere((t) => t.name == 'set_frequency');

    final result = await setFreq.execute(<String, dynamic>{'frequency_mhz': 145.0});
    final decoded = jsonDecode(result) as Map<String, dynamic>;

    expect(radio.setFrequencyCalls, 1);
    expect(radio.lastFreqHz, 145000000);
    expect(decoded['ok'], true);
  });
}
