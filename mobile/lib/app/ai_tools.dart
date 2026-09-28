import 'dart:convert';

import '../models/radio_state.dart';
import '../services/ai_client.dart';
import '../services/radio_controller.dart';
import 'tokens.dart';

// ============================================================================
// 把 RadioApi 包装成 AI 可调用的工具集（function calling 真正调谐）。
// ----------------------------------------------------------------------------
// 所有工具的 execute 都不抛异常：参数缺失/越界统一返回 {ok:false,error:...}
// 的 JSON 文本，由对话层展示给用户，而不是让 AI 会话崩掉。
// ============================================================================

String _err(String message) =>
    jsonEncode(<String, dynamic>{'ok': false, 'error': message});

/// 由射频接口构造 AI 工具集。
List<AiTool> buildRadioTools(RadioApi radio) {
  return <AiTool>[
    AiTool(
      name: 'set_frequency',
      description: '把 RTL-SDR 接收机调到指定中心频率。'
          '必须且只能提供 frequency_hz（整数 Hz）或 frequency_mhz（数字 MHz）之一。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'frequency_hz': <String, dynamic>{
            'type': 'integer',
            'description': '目标频率，单位 Hz，例如 109000000。',
          },
          'frequency_mhz': <String, dynamic>{
            'type': 'number',
            'description': '目标频率，单位 MHz，例如 109.0。与 frequency_hz 二选一。',
          },
        },
        'required': <String>[],
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final hzArg = args['frequency_hz'];
          final mhzArg = args['frequency_mhz'];
          double? hz;
          if (hzArg is num) {
            hz = hzArg.toDouble();
          } else if (mhzArg is num) {
            hz = mhzArg.toDouble() * 1e6;
          }
          if (hz == null) {
            return _err('必须提供 frequency_hz 或 frequency_mhz 之一');
          }
          if (hz < AppTokens.freqMinHz || hz > AppTokens.freqMaxHz) {
            return _err('频率超出 RTL-SDR 范围 '
                '${AppTokens.freqMinHz ~/ 1e6}–${AppTokens.freqMaxHz ~/ 1e6} MHz');
          }
          await radio.setFrequencyHz(hz.round());
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'frequency_hz': hz.round(),
          });
        } catch (e) {
          return _err('调谐失败: $e');
        }
      },
    ),
    AiTool(
      name: 'set_mode',
      description: '设置解调模式：nfm = 窄带调频（语音），wfm = 宽带调频（广播）。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'mode': <String, dynamic>{
            'type': 'string',
            'enum': <String>['nfm', 'wfm'],
          },
        },
        'required': <String>['mode'],
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final raw = args['mode'];
          if (raw is! String) return _err('缺少 mode 参数（nfm / wfm）');
          final DemodMode mode;
          try {
            mode = DemodMode.values.byName(raw);
          } catch (_) {
            return _err('未知解调模式: $raw（应为 nfm 或 wfm）');
          }
          radio.setMode(mode);
          return jsonEncode(<String, dynamic>{'ok': true, 'mode': mode.name});
        } catch (e) {
          return _err('设置模式失败: $e');
        }
      },
    ),
    AiTool(
      name: 'set_gain',
      description: '设置增益：传 {auto: true} 启用 AGC，或传 {gain_db: 数字} '
          '手动指定增益（范围 0–49.6 dB）。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'auto': <String, dynamic>{
            'type': 'boolean',
            'description': '是否启用自动增益控制。',
          },
          'gain_db': <String, dynamic>{
            'type': 'number',
            'description': '手动增益，单位 dB，范围 0–49.6。',
          },
        },
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final auto = args['auto'];
          final db = args['gain_db'];
          if (auto is bool) {
            await radio.setAutoGain(auto);
            return jsonEncode(<String, dynamic>{'ok': true, 'auto_gain': auto});
          }
          if (db is num) {
            final d = db.toDouble();
            if (d < AppTokens.gainMinDb || d > AppTokens.gainMaxDb) {
              return _err('增益超出范围 '
                  '${AppTokens.gainMinDb}–${AppTokens.gainMaxDb} dB');
            }
            await radio.setGainDb(d);
            return jsonEncode(<String, dynamic>{'ok': true, 'gain_db': d});
          }
          return _err('需要 auto（布尔）或 gain_db（数字）之一');
        } catch (e) {
          return _err('设置增益失败: $e');
        }
      },
    ),
    AiTool(
      name: 'set_sample_rate',
      description: '设置采样率（Hz），只能取支持的档位之一。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'sample_rate_hz': <String, dynamic>{
            'type': 'number',
            'description': '采样率，单位 Hz。',
          },
        },
        'required': <String>['sample_rate_hz'],
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final raw = args['sample_rate_hz'];
          if (raw is! num) return _err('缺少 sample_rate_hz 参数');
          final rate = raw.toDouble();
          final ok = AppTokens.sampleRatesHz
              .any((r) => (r - rate).abs() < AppTokens.freqStepHz);
          if (!ok) {
            return _err('采样率必须取以下档位之一: '
                '${AppTokens.sampleRatesHz.map((r) => '${(r / 1e6).toStringAsFixed(2)} MHz').join(' / ')}');
          }
          await radio.setSampleRateHz(rate);
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'sample_rate_hz': rate.round(),
          });
        } catch (e) {
          return _err('设置采样率失败: $e');
        }
      },
    ),
    AiTool(
      name: 'get_status',
      description: '读取当前接收机状态（连接、频率、模式、增益、采样率）。',
      parameters: const <String, dynamic>{'type': 'object', 'properties': <String, dynamic>{}},
      execute: (Map<String, dynamic> args) async {
        try {
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'connected': radio.status == ConnectionStatus.connected,
            'frequency_hz': radio.freqHz,
            'mode': radio.mode.name,
            'gain_db': radio.gainDb,
            'auto_gain': radio.autoGain,
            'sample_rate_hz': radio.sampleRateHz,
          });
        } catch (e) {
          return _err('读取状态失败: $e');
        }
      },
    ),
  ];
}
