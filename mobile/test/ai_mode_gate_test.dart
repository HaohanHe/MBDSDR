// AI 模式 gate：AI 接管 vs 手动。
//
// 手动模式下：写动作（set_frequency 等）不得真正调用 RadioApi，
// 只返回「手动模式：未执行」的 JSON；只读工具（get_status / get_squelch_status /
// predict_passes / list_* / get_recording_state / get_scan_status）仍放行。
// AI 接管模式下：set_frequency 必须真正下发频率。
//
// 门语义钉死：凡 set_*/start_*/stop_* 命名的动作工具必须登记进 gate 集合
// （漏登记=绕过 arm_manual 人工门，红线），只读工具不得被误 gate。
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/ai_tools.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';
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
  bool recording = false;

  @override
  bool squelchEnabled = true;

  @override
  double squelchThresholdDb = -60;

  @override
  bool squelchOpen = false;

  @override
  double squelchLevelDb = -80;

  @override
  bool squelchAuto = false;

  @override
  bool scanning = false;

  @override
  int? scanHz;

  @override
  double scanProgress = 0;

  @override
  bool scanPaused = false;

  @override
  Future<void> setFrequencyHz(int hz) async {
    setFrequencyCalls++;
    lastFreqHz = hz;
    freqHz = hz;
  }

  @override
  void setMode(DemodMode m) {
    mode = m;
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
    expect(decoded['ok'], isTrue);
  });

  group('门语义：写工具必过 gate、只读工具不误伤', () {
    /// 只读工具白名单（读取不构成动作，手动模式放行）。
    const Set<String> readOnlyNames = <String>{
      'get_status',
      'get_squelch_status',
      'predict_passes',
      'list_bookmarks',
      'list_recordings',
      'get_recording_state',
      'get_scan_status',
    };

    test('手动模式：全部写工具回 gated JSON，一个不漏', () async {
      final radio = RecordingRadio();
      final tools = buildRadioTools(radio, manualMode: true);
      final Set<String> gated = <String>{};
      for (final AiTool t in tools) {
        final Map<String, dynamic> r =
            jsonDecode(await t.execute(const <String, dynamic>{}))
                as Map<String, dynamic>;
        if (r['gated'] == true) gated.add(t.name);
      }
      expect(
        gated,
        <String>{
          'set_frequency',
          'set_mode',
          'set_gain',
          'set_sample_rate',
          'start_recording',
          'stop_recording',
          'set_squelch',
        },
        reason: '写工具集合钉死：新增写工具必须同步登记 gate（否则绕过 arm_manual 人工门）',
      );
      expect(radio.setFrequencyCalls, 0);
    });

    test('命名语义钉死：set_*/start_*/stop_* 必被 gate，get_*/list_*/predict_* 必放行',
        () async {
      final tools = buildRadioTools(RecordingRadio(), manualMode: true);
      for (final AiTool t in tools) {
        final Map<String, dynamic> r =
            jsonDecode(await t.execute(const <String, dynamic>{}))
                as Map<String, dynamic>;
        final bool writeNamed = t.name.startsWith('set_') ||
            t.name.startsWith('start_') ||
            t.name.startsWith('stop_');
        if (writeNamed) {
          expect(r['gated'], isTrue,
              reason: '${t.name} 是写动作，必须走手动门（arm_manual）');
        } else {
          expect(r.containsKey('gated'), isFalse,
              reason: '${t.name} 是只读工具，不得被手动门误伤');
          expect(readOnlyNames.contains(t.name), isTrue,
              reason: '${t.name} 未登记只读白名单，先核对读/写归属');
        }
      }
    });

    test('手动模式：新增只读工具照常真读（不摆拍、不误 gate）', () async {
      final radio = RecordingRadio()
        ..scanning = true
        ..scanHz = 145500000;
      final tools = buildRadioTools(radio, manualMode: true);
      final scan = tools.firstWhere((t) => t.name == 'get_scan_status');
      final Map<String, dynamic> r =
          jsonDecode(await scan.execute(const <String, dynamic>{}))
              as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(r['scanning'], isTrue);
      expect(r['scan_hz'], 145500000);
    });
  });
}
