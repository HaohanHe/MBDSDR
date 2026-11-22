// 真实 FM 解调：正交鉴频 + 信道化 + 去加重。
//
// 正交鉴频（交叉积）：
//   dphase = atan2(I[n]*Q[n-1] - Q[n]*I[n-1], I[n]*I[n-1] + Q[n]*Q[n-1])
// atan2 自动把相位差解卷绕到 (-π, π]，输出正比于瞬时频偏。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'filters.dart';

/// FM 解调器公共基类（状态ful，跨调用保持滤波器/鉴频历史）。
abstract class FmDemod {
  /// 输入采样率（Hz）。
  final double inputRateHz;

  FmDemod({required this.inputRateHz});

  Float64x2? _prev;

  /// 处理一段复采样，返回单声道音频浮点。
  Float32List process(Float64x2List iq);

  /// 正交鉴频：输入复序列 → 瞬时相位差分，归一到 [-1, 1]。
  ///
  /// 原始鉴频输出是「弧度/样点」（atan2 值域 ±π），而下游
  /// `floatTo16BitPcm` 的契约是 [-1, 1] 归一化样本。不归一化会把 ±π
  /// 直接送进 16-bit 满量程：模拟器实测 25 s 真实记录中 **39% 样本贴
  /// 在 ±32767**（其中 -32767 出现 24.9 万次），属静默削顶失真。
  /// 这里统一除以 π（物理最大相位差），两种解调模式共用同一出口。
  ///
  /// 返回 Float32List 直接作为音频帧：省掉「Float64 计算 → 再建一份
  /// Float32」的整块拷贝（每块最多 3 万个样本、每秒数十块）。
  Float32List _discriminate(Float64x2List x) {
    final out = Float32List(x.length);
    for (var n = 0; n < x.length; n++) {
      final cur = x[n];
      final p = _prev;
      if (p == null) {
        out[n] = 0;
      } else {
        final cross = cur.x * p.y - cur.y * p.x;
        final dot = cur.x * p.x + cur.y * p.y;
        out[n] = math.atan2(cross, dot) / math.pi;
      }
      _prev = cur;
    }
    return out;
  }
}

/// 窄带 FM 解调（对讲机/业余段，12.5kHz 信道）。
///
/// 本版 NFM 不启用去加重（业余段通常无需）；鉴频输出已归一到 [-1, 1]
/// （除以 π），因此 12.5 kHz 信道内 6.25 kHz 频偏约对应 0.25 满量程。
class NfmDemod extends FmDemod {
  final Channelizer _chan;

  NfmDemod({required super.inputRateHz})
    : _chan = Channelizer(
        taps: firLowpassTaps(
          cutoffNorm: 6250 / inputRateHz,
          numTaps: 129,
        ),
        decimation: (inputRateHz / 51200).round().clamp(1, 100000),
        sampleRateHz: inputRateHz,
      );

  @override
  Float32List process(Float64x2List iq) {
    final channelized = _chan.process(iq);
    // NFM 不强制去加重；鉴频结果已归一到 [-1, 1]，直接作为音频帧返回。
    return _discriminate(channelized);
  }
}

/// 宽带 FM 解调（广播 FM，200kHz 信道）。
class WfmDemod extends FmDemod {
  final Channelizer _chan;

  /// 75µs 去加重系数（在第一级抽取率上）。
  final double _deemphAlpha;

  /// 去加重状态。
  double _deemphState = 0;

  /// 第二级抽取因子（降到 ~48k）。
  final int _finalDecimation;
  int _finalCount = 0;

  WfmDemod({required super.inputRateHz})
    : _chan = Channelizer(
        taps: firLowpassTaps(
          cutoffNorm: 100000 / inputRateHz,
          numTaps: 161,
        ),
        decimation: _decimTo(inputRateHz, 256000),
        sampleRateHz: inputRateHz,
      ),
      _deemphAlpha =
          _deemphAlphaFor(inputRateHz, _decimTo(inputRateHz, 256000)),
      _finalDecimation =
          (inputRateHz / _decimTo(inputRateHz, 256000) / 48000)
              .round()
              .clamp(1, 1000);

  /// 选最接近 targetRate 的整数抽取因子。
  static int _decimTo(double inputRateHz, double targetRateHz) =>
      (inputRateHz / targetRateHz).round().clamp(1, 100000);

  /// 第一级抽取率上的 75µs RC 去加重系数：α = dt/(RC+dt) = 1/(midRate·RC+1)。
  static double _deemphAlphaFor(double inputRateHz, int decim1) {
    final midRate = inputRateHz / decim1;
    return 1 / (midRate * 75e-6 + 1);
  }

  @override
  Float32List process(Float64x2List iq) {
    final channelized = _chan.process(iq);
    final freq = _discriminate(channelized);

    // 75µs RC 去加重（单极点低通）：y[n] = y[n-1] + α(x[n]-y[n-1])。
    // 预分配输出，避免逐样本 List.add 的扩容与装箱（旧实现每次 append
    // 都可能触发 growable 列表扩容，256k/s 下是持续的 GC 压力）。
    final cap = freq.length ~/ _finalDecimation + 2;
    final audio = Float32List(cap);
    var written = 0;
    for (var n = 0; n < freq.length; n++) {
      _deemphState += _deemphAlpha * (freq[n] - _deemphState);
      // 第二级整数抽取到 ~48k。去加重本身是低通，兼作抗混叠。
      if (_finalCount == 0) {
        audio[written++] = _deemphState;
      }
      _finalCount = (_finalCount + 1) % _finalDecimation;
    }
    return written == cap ? audio : Float32List.sublistView(audio, 0, written);
  }
}
