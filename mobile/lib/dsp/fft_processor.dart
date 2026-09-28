// 真实频谱处理器：加 Hann 窗 → 复数 FFT → fftshift → 归一化 dB。
//
// 使用 fftea 包做实数/复数 FFT。频率轴严格居中：bin N/2 对应中心频率，
// binToHz(index) = centerHz + (index - N/2) * fs / N。
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:fftea/fftea.dart';

/// 一帧频谱。db 长度 == fftSize，已按窗增益归一化并截断到 [minDb, 0]。
class SpectrumFrame {
  /// 每个 bin 的功率谱（dBFS 相对单位幅度复正弦）。
  final Float64List db;

  /// 中心频率（Hz）。
  final double centerFreqHz;

  /// 采样率（Hz）。
  final double sampleRateHz;

  /// FFT 点数。
  final int fftSize;

  const SpectrumFrame({
    required this.db,
    required this.centerFreqHz,
    required this.sampleRateHz,
    required this.fftSize,
  });

  /// bin index → 频率（Hz）。index 0 对应最低频率，index N/2 对应中心频率。
  double binToHz(int index) =>
      centerFreqHz + (index - fftSize / 2) * sampleRateHz / fftSize;

  /// 单个 bin 宽（Hz）。
  double get binWidthHz => sampleRateHz / fftSize;
}

/// 复频谱分析器。可复用同一个对象对多段最新 IQ 做 FFT。
class FftProcessor {
  /// FFT 点数（必须为 2 的幂）。
  final int fftSize;

  /// dB 下限（低于此值截到该值）。
  final double minDb;

  late final FFT _fft;
  late final Float64List _window;
  late final double _windowSum;
  late final Float64x2List _scratch;

  FftProcessor({this.fftSize = 2048, this.minDb = -120}) {
    if (fftSize < 2 || fftSize & (fftSize - 1) != 0) {
      throw ArgumentError('fftSize 必须为 2 的幂，得到 $fftSize');
    }
    _fft = FFT(fftSize);
    // Hann 窗（周期式对称：0.5 - 0.5*cos(2π n / (N-1))）。
    final w = Float64List(fftSize);
    var sum = 0.0;
    for (var n = 0; n < fftSize; n++) {
      final v = 0.5 - 0.5 * math.cos(2 * math.pi * n / (fftSize - 1));
      w[n] = v;
      sum += v;
    }
    _window = w;
    _windowSum = sum;
    _scratch = Float64x2List(fftSize);
  }

  /// 对最新 [fftSize] 点复 IQ 做一次 FFT，输出居中的 dB 谱。
  ///
  /// [i]/[q] 长度应 >= fftSize；取末尾 fftSize 个采样。
  SpectrumFrame compute(
    Float64List i,
    Float64List q, {
    required double centerFreqHz,
    required double sampleRateHz,
  }) {
    final n = fftSize;
    final end = i.length;
    final start = end - n;
    // 加窗并写入复数缓冲。
    for (var k = 0; k < n; k++) {
      final w = _window[k];
      _scratch[k] = Float64x2(i[start + k] * w, q[start + k] * w);
    }
    _fft.inPlaceFft(_scratch);

    // fftshift：把 DC(bin0) 移到中间。shifted[k] = orig[(k + N/2) mod N]。
    final half = n ~/ 2;
    final out = Float64List(n);
    for (var k = 0; k < n; k++) {
      final c = _scratch[(k + half) % n];
      final mag = math.sqrt(c.x * c.x + c.y * c.y) / _windowSum;
      var db = 20 * math.log(mag <= 0 ? 1e-12 : mag) / math.ln10;
      if (db < minDb) db = minDb;
      out[k] = db;
    }
    return SpectrumFrame(
      db: out,
      centerFreqHz: centerFreqHz,
      sampleRateHz: sampleRateHz,
      fftSize: n,
    );
  }
}
