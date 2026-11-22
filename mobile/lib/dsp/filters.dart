// 简易 DSP 积木：窗函数法 FIR 低通、复混频、整数抽取，以及组合它们的 Channelizer。
//
// 全部用 dart:typed_data 的 Float64x2List 表示复数流，避免外部依赖。
library;

import 'dart:math' as math;
import 'dart:typed_data';

/// 窗函数法（Hann）设计低通 FIR 抽头。
///
/// [cutoffNorm] 为归一化截止频率（0~0.5，即 cutoff / fs），[numTaps] 抽头数（奇数更好）。
/// 返回长度为 [numTaps]、增益归一化到 1 的实抽头。
Float64List firLowpassTaps({required double cutoffNorm, required int numTaps}) {
  assert(cutoffNorm > 0 && cutoffNorm < 0.5, '截止频率须在 (0, 0.5) 内');
  final m = numTaps - 1;
  final h = Float64List(numTaps);
  var sum = 0.0;
  for (var n = 0; n < numTaps; n++) {
    final x = n - m / 2.0;
    double v;
    if (x == 0) {
      v = 2 * cutoffNorm;
    } else {
      v = math.sin(2 * math.pi * cutoffNorm * x) / (math.pi * x);
    }
    // Hann 窗。
    final w = 0.5 - 0.5 * math.cos(2 * math.pi * n / m);
    v *= w;
    h[n] = v;
    sum += v;
  }
  // 直流增益归一化到 1。
  for (var n = 0; n < numTaps; n++) {
    h[n] /= sum;
  }
  return h;
}

/// 状态ful 信道化器：复混频（把 offsetHz 搬基带）→ 抗混叠 FIR 低通 → 整数抽取。
class Channelizer {
  /// 低通抽头（已归一化）。
  final Float64List taps;

  /// 抽取因子（每 [decimation] 个输入样点输出 1 个）。
  final int decimation;

  /// 期望信号相对当前调谐中心的偏移（Hz），会被搬到基带。
  final double offsetHz;

  /// 输入采样率（Hz）。
  final double sampleRateHz;

  late final Float64x2List _line;
  int _pos = 0;
  double _phase = 0;
  int _decimCount = 0;

  Channelizer({
    required this.taps,
    required this.decimation,
    required this.sampleRateHz,
    this.offsetHz = 0,
  }) {
    if (decimation < 1) {
      throw ArgumentError('decimation 必须 >= 1');
    }
    _line = Float64x2List(taps.length);
  }

  /// 信道化后的输出采样率（Hz）。
  double get outputRateHz => sampleRateHz / decimation;

  /// 处理一段连续复采样，返回抽取后的复采样。保持内部状态跨调用连续。
  ///
  /// 抽取型 FIR：每个输入样点只做 O(1) 的写入 + 混频，卷积只在**输出时刻**
  /// 计算一次，复杂度 O(N·fs/D) 而不是 O(N·fs)。旧实现每个输入样点都算完整
  /// 卷积、随后丢掉 39/40 的结果：2.048 MS/s × 161 抽头 = 3.3 亿复数 MAC/s，
  /// Android debug（JIT + 软件渲染）下主 isolate 被打满，实测触发输入分发
  /// ANR。输出样点与旧实现逐点等价（都在每组的第一个样点上取卷积）。
  ///
  /// offsetHz == 0（NFM/WFM 的默认路径）时不跑逐样点 sin/cos 混频。
  Float64x2List process(Float64x2List input) {
    final numTaps = taps.length;
    final outCount = (input.length + decimation - 1) ~/ decimation;
    final out = Float64x2List(outCount);
    final hasOffset = offsetHz != 0.0;
    final mixPerSample = -2 * math.pi * offsetHz / sampleRateHz;
    var written = 0;
    for (var idx = 0; idx < input.length; idx++) {
      final s = input[idx];
      Float64x2 mixed;
      if (hasOffset) {
        // 复混频：x * exp(-i*2π·offset/fs·n)。
        _phase += mixPerSample;
        if (_phase > math.pi) {
          _phase -= 2 * math.pi;
        } else if (_phase < -math.pi) {
          _phase += 2 * math.pi;
        }
        final cosP = math.cos(_phase);
        final sinP = math.sin(_phase);
        mixed = Float64x2(
          s.x * cosP - s.y * sinP,
          s.x * sinP + s.y * cosP,
        );
      } else {
        mixed = s;
      }

      // 环形延迟线：每输入只写一格。
      _line[_pos] = mixed;

      // 只在抽取点上做卷积（从最新样点往回 numTaps 个）。
      if (_decimCount == 0) {
        var accRe = 0.0;
        var accIm = 0.0;
        var j = _pos;
        for (var k = 0; k < numTaps; k++) {
          final v = _line[j];
          final tk = taps[k];
          accRe += tk * v.x;
          accIm += tk * v.y;
          j = j == 0 ? numTaps - 1 : j - 1;
        }
        out[written++] = Float64x2(accRe, accIm);
      }

      _pos = (_pos + 1) % numTaps;
      _decimCount = (_decimCount + 1) % decimation;
    }
    return written == outCount ? out : Float64x2List.sublistView(out, 0, written);
  }
}
