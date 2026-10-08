import 'dart:convert';

import '../models/radio_state.dart';
import '../models/satellite.dart';
import '../services/ai_client.dart';
import '../services/radio_controller.dart';
import '../services/sat_passes_provider.dart';
import '../services/tle_client.dart';
import 'tokens.dart';

// ============================================================================
// 把 RadioApi 包装成 AI 可调用的工具集（function calling 真正调谐）。
// ----------------------------------------------------------------------------
// 所有工具的 execute 都不抛异常：参数缺失/越界统一返回 {ok:false,error:...}
// 的 JSON 文本，由对话层展示给用户，而不是让 AI 会话崩掉。
// ============================================================================

String _err(String message) =>
    jsonEncode(<String, dynamic>{'ok': false, 'error': message});

/// P0 断开诚实性：动作工具 execute 前判连接。未连接返回错误 JSON；
/// 已连接返回 null（放行）。
///
/// 红线（A4 §4-P0）：断开时 RadioController 的写方法是空安全 no-op、不抛异常，
/// 若不在此拦截就会一路回 ok:true，让模型误以为真调谐了接收机。
String? _disconnectError(RadioApi radio, String tool) {
  if (radio.status != ConnectionStatus.connected) {
    return _err('接收机未连接，无法执行 $tool');
  }
  return null;
}

/// 手动模式下被 gate 掉的动作统一回给模型的结果：不真正调谐，
/// 仅声明「未执行」，让模型据此向用户解释当前是手动模式。
String _gated(String toolName) => jsonEncode(<String, dynamic>{
      'ok': false,
      'gated': true,
      'error': '手动模式：未执行 $toolName',
      'hint': '当前为手动模式，AI 只对话不动作；切回「AI 接管」后才会真正调谐。',
    });

/// 由射频接口构造 AI 工具集。
///
/// [manualMode] 为 true 时进入「手动模式」：所有**会改变接收机状态/会话**的动作
/// （set_frequency / set_mode / set_gain / set_sample_rate /
/// start_recording / stop_recording）不再真正下发，
/// 而是返回 [_gated] 结果并出现在对话流里；只读的 get_status 保持可用
/// （读取不构成动作）。AI 接管（false，默认）时行为与历史完全一致。
///
/// [passesService] / [station] 为只读卫星过境工具 predict_passes 的数据源与
/// 本站坐标；任一缺失时 predict_passes 回诚实空态（不编造过境）。二者均为
/// 可选注入，缺省不影响其余射频工具。
List<AiTool> buildRadioTools(
  RadioApi radio, {
  bool manualMode = false,
  SatPassesService? passesService,
  Station? station,
}) {
  final List<AiTool> tools = <AiTool>[
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
            'minimum': AppTokens.freqMinHz,
            'maximum': AppTokens.freqMaxHz,
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
          final dc = _disconnectError(radio, 'set_frequency');
          if (dc != null) return dc;
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
          final dc = _disconnectError(radio, 'set_mode');
          if (dc != null) return dc;
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
            'minimum': AppTokens.gainMinDb,
            'maximum': AppTokens.gainMaxDb,
          },
        },
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final dc = _disconnectError(radio, 'set_gain');
          if (dc != null) return dc;
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
            'enum': AppTokens.sampleRatesHz,
          },
        },
        'required': <String>['sample_rate_hz'],
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final dc = _disconnectError(radio, 'set_sample_rate');
          if (dc != null) return dc;
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
      name: 'start_recording',
      description: '开始把当前解调后的音频录制成本地 16-bit 单声道 WAV'
          '（过静噪门后"录听到的"），落盘 .wav + sidecar JSON，结束后可在录音列表回放。'
          '需已连接接收机；重复调用安全（已在录则不另开文件）。'
          '与桌面端 start_recording 对齐——桌面录原始 IQ（SigMF），本端录解调音频'
          '（WAV），产物不同但 AI 入口一致。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{},
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final dc = _disconnectError(radio, 'start_recording');
          if (dc != null) return dc;
          if (radio.recording) {
            return jsonEncode(<String, dynamic>{
              'ok': true,
              'recording': true,
              'already_recording': true,
              'message': '已在录制中，无需重复开始',
            });
          }
          await radio.startRecording();
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'recording': true,
            'message': '开始录制解调音频（WAV）',
            'frequency_hz': radio.freqHz,
            'mode': radio.mode.name,
          });
        } on StateError catch (e) {
          // 未连接 / 未配录音目录：诚实回错，绝不假装录上了。
          return _err('录制未开始: $e');
        } catch (e) {
          return _err('开始录制失败: $e');
        }
      },
    ),
    AiTool(
      name: 'stop_recording',
      description: '停止当前录制，收尾落盘 .wav + sidecar 并写入录音列表。'
          '未在录制时诚实返回（不报错、不造文件）。断连时也允许调用以收尾落盘。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{},
      },
      execute: (Map<String, dynamic> args) async {
        try {
          // stop 不做连接 gate：断连时也应能收尾；是否在录由底层判断。
          final meta = await radio.stopRecording();
          if (meta == null) {
            return jsonEncode(<String, dynamic>{
              'ok': true,
              'recording': false,
              'message': '当前未在录制',
            });
          }
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'recording': false,
            'message': '已停止录制并落盘',
            'wav_file': meta.wavFileName,
            'duration_ms': meta.durationMs,
            'frequency_hz': meta.frequencyHz,
            'mode': meta.mode,
          });
        } catch (e) {
          return _err('停止录制失败: $e');
        }
      },
    ),
    AiTool(
      name: 'set_squelch',
      description: '设置静噪门控。可分别提供 enabled（是否使能静噪）、'
          'threshold_db（手动门限，dBFS，范围 -100 到 -20，越接近 0 越严）、'
          'auto（true = 门限自动跟随噪声底）；至少提供其中一项。'
          '同时给 threshold_db 时按手动门限生效并退出自动跟随。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'enabled': <String, dynamic>{'type': 'boolean'},
          'threshold_db': <String, dynamic>{
            'type': 'number',
            'minimum': AppTokens.squelchThresholdMinDb,
            'maximum': AppTokens.squelchThresholdMaxDb,
          },
          'auto': <String, dynamic>{'type': 'boolean'},
        },
      },
      execute: (Map<String, dynamic> args) async {
        try {
          final dc = _disconnectError(radio, 'set_squelch');
          if (dc != null) return dc;
          final enabled = args['enabled'];
          final threshold = args['threshold_db'];
          final auto = args['auto'];
          if (enabled == null && threshold == null && auto == null) {
            return _err('需要 enabled / threshold_db / auto 至少一项');
          }
          if (enabled is bool) radio.setSquelchEnabled(enabled);
          // 顺序：先自动跟随，再手动门限——同给 threshold_db 时手动覆盖自动，
          // 与频谱页拖门限滑杆「退出自动跟随」语义一致。
          if (auto is bool) radio.setSquelchAuto(auto);
          if (threshold is num) {
            radio.setSquelchThresholdDb(threshold.toDouble());
          }
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'squelch_enabled': radio.squelchEnabled,
            'squelch_auto': radio.squelchAuto,
            'threshold_db': radio.squelchThresholdDb,
            'open': radio.squelchOpen,
          });
        } catch (e) {
          return _err('设置静噪失败: $e');
        }
      },
    ),
    AiTool(
      name: 'get_squelch_status',
      description: '只读：读取静噪门控状态——是否使能、是否自动门限、'
          '当前手动门限（dBFS）、门当前是否开门（true=送声）、实测解调电平（dBFS）。'
          '不需要接收机连接也可读取当前门配置。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{},
      },
      execute: (Map<String, dynamic> args) async {
        try {
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'squelch_enabled': radio.squelchEnabled,
            'squelch_auto': radio.squelchAuto,
            'threshold_db': radio.squelchThresholdDb,
            'open': radio.squelchOpen,
            'level_db': radio.squelchLevelDb,
          });
        } catch (e) {
          return _err('读取静噪状态失败: $e');
        }
      },
    ),
    AiTool(
      name: 'get_status',
      description: '读取当前接收机状态。返回连接状态机 status'
          '（connected/connecting/reconnecting/disconnected/error）、是否就绪 '
          'connected、错误原因 error_message，以及频率、模式、增益、采样率。',
      parameters: const <String, dynamic>{'type': 'object', 'properties': <String, dynamic>{}},
      execute: (Map<String, dynamic> args) async {
        try {
          return jsonEncode(<String, dynamic>{
            'ok': true,
            // connected 仅在真·数据流就绪时为 true；status 保留完整状态机语义，
            // 让 AI 能区分「空闲未连接(disconnected)」与「掉线自动重连中
            // (reconnecting)」——与桌面端 sourceTelemetry(connected) +
            // sourceDropped/sourceError/reconnectRequested 的多信号披露对齐，
            // 而不是把五态压成一个布尔导致 AI 误判为用户已停手。
            'connected': radio.status == ConnectionStatus.connected,
            'status': radio.status.name,
            'error_message': radio.errorMessage,
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
    AiTool(
      name: 'predict_passes',
      description: '只读：预测某卫星未来数小时相对本站的过境（升/降时刻、'
          '最大仰角、方位）。数据源为真实 Celestrak TLE + 本地 SGP4；'
          '无 TLE / 无同名卫星 / 窗口内无过境时诚实返回空态，绝不编造。'
          '本工具只读、不需要接收机连接。',
      parameters: const <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'satellite_name': <String, dynamic>{
            'type': 'string',
            'description': '卫星名称（大小写不敏感，如 "NOAA 19"）。',
          },
          'hours': <String, dynamic>{
            'type': 'number',
            'description': '预测未来多少小时（默认 48，范围 1–168）。',
            'minimum': 1,
            'maximum': 168,
          },
        },
        'required': <String>['satellite_name'],
      },
      execute: (Map<String, dynamic> args) async {
        if (passesService == null) {
          return _err('卫星过境预测未配置（缺少 TLE 数据源）');
        }
        if (station == null) {
          return _err('未配置测站坐标，无法预测过境；请在设置中填写本站位置');
        }
        final rawName = args['satellite_name'];
        if (rawName is! String || rawName.trim().isEmpty) {
          return _err('缺少 satellite_name 参数');
        }
        var hours = 48.0;
        final rawHours = args['hours'];
        if (rawHours is num) {
          hours = rawHours.toDouble();
          if (hours < 1 || hours > 168) {
            return _err('hours 须在 1–168 之间');
          }
        }
        try {
          final passes = await passesService.predict(
            satelliteName: rawName.trim(),
            station: station,
            hours: hours,
          );
          if (passes.isEmpty) {
            return _err('未来 ${hours.round()} 小时内无 ${rawName.trim()} 过境'
                '（窗口内无过境数据，不编造）');
          }
          return jsonEncode(<String, dynamic>{
            'ok': true,
            'satellite': rawName.trim(),
            'passes': passes
                .map((PredictedPass p) => <String, dynamic>{
                      'startUtc': p.startUtc.toUtc().toIso8601String(),
                      'endUtc': p.endUtc.toUtc().toIso8601String(),
                      'maxTimeUtc': p.maxTimeUtc.toUtc().toIso8601String(),
                      'maxElevationDeg':
                          double.parse(p.maxElevationDeg.toStringAsFixed(2)),
                      'azimuthDeg':
                          double.parse(p.azimuthDeg.toStringAsFixed(2)),
                      'riseAzDeg':
                          double.parse(p.riseAzDeg.toStringAsFixed(2)),
                      'setAzDeg': double.parse(p.setAzDeg.toStringAsFixed(2)),
                      'durationSec': p.durationSec,
                    })
                .toList(),
          });
        } on TleFetchException catch (e) {
          return _err('无新鲜 TLE：$e');
        } on SatPassesException catch (e) {
          return _err(e.message);
        } catch (e) {
          return _err('过境预测失败: $e');
        }
      },
    ),
  ];

  if (!manualMode) return tools;

  // 手动模式：只读 get_status 放行，其余动作包一层 gate——不调用 radio，
  // 返回「手动模式：未执行」的 JSON（由对话层 chip 原样展示）。
  const Set<String> mutatingTools = <String>{
    'set_frequency',
    'set_mode',
    'set_gain',
    'set_sample_rate',
    'start_recording',
    'stop_recording',
    'set_squelch',
  };
  return tools.map((AiTool t) {
    if (!mutatingTools.contains(t.name)) return t;
    return AiTool(
      name: t.name,
      description: t.description,
      parameters: t.parameters,
      execute: (Map<String, dynamic> args) => Future<String>.value(_gated(t.name)),
    );
  }).toList();
}
