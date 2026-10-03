// 静噪门控：基于解调后音频 RMS（dBFS）的真实电平门，带 attack/decay 平滑与 hangover。
//
// 语义与桌面端 cpp/src/dsp/squelch.h 对齐：
//   * 关闭（enabled=false）：门恒开，音频直通；
//   * 开启（enabled=true）：平滑后的 RMS 电平 >= 门限 → 开门（OPEN），送声；
//     低于门限 → 经 hangover 延时后关门（CLOSED），把该块静音。
//
// 诚实说明：电平来自**真实解调音频帧**的 RMS，不是伪造的 RSSI。移动端真实音频
// 落点见 RadioController._onAudioFrame —— 关门时不再把音频帧送往 PCM sink（改送
// 等长静音），与「手动静音/音量」走同一条 Dart→原生 PCM 路径。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import '../app/tokens.dart';

/// RMS 静噪门（状态ful，跨调用保持平滑/hangover 历史）。
class SquelchGate {
  SquelchGate();

  // -------------------------------------------------- 算法时间常数（具名常量）
  /// attack 时间常数（ms）：电平上升时的跟随速度。
  static const double attackTauMs = 5.0;

  /// decay 时间常数（ms）：电平下降时的衰减速度。
  static const double decayTauMs = 50.0;

  /// hangover（ms）：信号跌过门限后仍保持开门的时长，避免短促断续。
  static const double hangMs = 200.0;

  /// 量测下限（dBFS）：低于此视为无信号，用于静音/空窗。
  static const double minMeasuredDb = -120.0;

  // -------------------------------------------------- 状态
  /// 是否启用门控；false = 恒开门直通。
  bool enabled = false;

  /// 门限（dBFS），取自 AppTokens 区间。
  double thresholdDb = AppTokens.squelchDefaultThresholdDb;

  /// 当前平滑后的真实电平（dBFS）。
  double levelDb = minMeasuredDb;

  /// 当前门是否开门（true=送声，false=静音）。
  bool open = false;

  // -------------------------------------------------- 自动门限
  /// 是否自动门限。true 时门限 = [noiseFloorDb]（实测同域音频 RMS 噪声底）
  /// + [AppTokens.squelchAutoMarginDb]，随噪声底自动跟随；false 时用手动
  /// [thresholdDb]。语义对齐桌面「自动门限」按钮。
  bool autoThreshold = false;

  /// 实测同域噪声底（dBFS，解调后音频 RMS）。非对称跟踪：向安静背景快随、
  /// 向响瞬变慢爬，使真实信号不抬升噪声底。与门控同域（dBFS），不跨域读 IQ。
  double noiseFloorDb = minMeasuredDb;

  bool _nfInit = false;

  double _hangLeftMs = 0;

  /// 复位平滑/hangover/噪声底状态（切频率/重连后调用）。
  /// 保留用户的 [autoThreshold] 选择，只清观察历史。
  void reset() {
    levelDb = minMeasuredDb;
    noiseFloorDb = minMeasuredDb;
    _nfInit = false;
    open = false;
    _hangLeftMs = 0;
  }

  /// 计算一帧真实音频的 RMS dBFS。
  static double rmsDbfs(Float32List audio) {
    if (audio.isEmpty) return minMeasuredDb;
    var sumSq = 0.0;
    for (final s in audio) {
      sumSq += s * s;
    }
    final rms = math.sqrt(sumSq / audio.length);
    if (rms <= 1e-9) return minMeasuredDb;
    return 20 * math.log(rms) / math.ln10;
  }

  /// 处理一帧真实解调音频，更新 [levelDb]/[open]，返回本块是否开门。
  ///
  /// [audioSampleRateHz] 用于把「每块样本数」换算成真实块时长，使 attack/decay
  /// 时间常数与帧率无关。
  bool process(Float32List audio, {required double audioSampleRateHz}) {
    final rawDb = rmsDbfs(audio);
    final blockMs = (audio.length / audioSampleRateHz) * 1000.0;

    // 非对称噪声底跟踪（同域：解调后音频 RMS dBFS）。向安静背景快随
    //（alphaDown）、向响瞬变慢爬（alphaUp）——真实信号过门限时不会抬升底。
    // 对齐桌面 spectrum_engine 的 audioNfDbfs_ 跟踪。
    if (!_nfInit) {
      noiseFloorDb = rawDb;
      _nfInit = true;
    } else if (rawDb < noiseFloorDb) {
      noiseFloorDb = (1 - AppTokens.squelchNfAlphaDown) * noiseFloorDb +
          AppTokens.squelchNfAlphaDown * rawDb;
    } else {
      noiseFloorDb = (1 - AppTokens.squelchNfAlphaUp) * noiseFloorDb +
          AppTokens.squelchNfAlphaUp * rawDb;
    }

    // attack/decay 一阶平滑：上升快、下降慢。
    final attackAlpha = 1 - math.exp(-blockMs / attackTauMs);
    final decayAlpha = 1 - math.exp(-blockMs / decayTauMs);
    final prev = levelDb;
    final alpha = rawDb > prev ? attackAlpha : decayAlpha;
    levelDb = prev + alpha * (rawDb - prev);

    if (!enabled) {
      // 关闭：恒开门直通。
      open = true;
      return open;
    }

    // 自动门限：门限 = 实测噪声底 + 裕量，clamp 到 token 区间。写回 thresholdDb
    // 供 UI 滑杆回读（对齐桌面 applyAutoThreshold：滑杆值 = 跟随结果）。
    if (autoThreshold) {
      thresholdDb = (noiseFloorDb + AppTokens.squelchAutoMarginDb).clamp(
        AppTokens.squelchThresholdMinDb,
        AppTokens.squelchThresholdMaxDb,
      );
    }

    // 门控：过门限立即开门并刷新 hangover；跌出后 hangover 耗尽才关门。
    if (levelDb >= thresholdDb) {
      open = true;
      _hangLeftMs = hangMs;
    } else {
      _hangLeftMs -= blockMs;
      if (_hangLeftMs <= 0) open = false;
    }
    return open;
  }
}
