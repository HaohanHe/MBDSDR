import 'dart:typed_data';

/// 把 [-1, 1] 的 Float32 样本线性量化为**有符号 16-bit 小端 PCM**。
///
/// 量化步骤（顺序敏感）：
///  1. 若 [muted] 为 true，直接返回与样本等长的全零帧（每样本 2 字节）；
///  2. 否则每个样本先乘 [volume]（默认 1.0）；
///  3. 再线性映射到 16-bit：`scaled * 32767`，四舍五入后
///     **clamp 到 [-32767, 32767]**（不使用 -32768，避免对称削波）；
///  4. 按**小端（little-endian）**写入，每样本占 2 字节。
///
/// 这是一个**纯函数**：不读全局状态、不触发平台通道、无副作用，
/// 输入相同则输出相同，可直接单测。
///
/// 例：`1.0`（volume=1）→ 0x7FFF = 字节 `[0xFF, 0x7F]`；
///    `-1.0` → 0x8001 = 字节 `[0x01, 0x80]`；`0.0` → `[0x00, 0x00]`。
Uint8List floatTo16BitPcm(
  Float32List samples, {
  double volume = 1.0,
  bool muted = false,
}) {
  final out = Uint8List(samples.length * 2);

  // 静音：整块直接为零（Uint8List 默认即零填充）。
  if (muted) return out;

  final view = ByteData.sublistView(out);
  for (var i = 0; i < samples.length; i++) {
    // 先乘 volume，再映射、再 clamp。
    final scaled = samples[i] * volume;
    // 映射到 [-32767, 32767]。
    var q = (scaled * 32767.0).round();
    if (q > 32767) q = 32767;
    if (q < -32767) q = -32767;
    view.setInt16(i * 2, q, Endian.little);
  }
  return out;
}
