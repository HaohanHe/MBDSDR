// 一段连续的复数基带 IQ 采样块。
//
// I/Q 均归一化到 [-1, 1] 的浮点（来自 rtl_tcp 的 uint8 字节：(b - 127.5) / 127.5）。
// i 与 q 等长，按采样点一一对应：(i[n], q[n]) 即第 n 个复数采样。
// timestamp 为到达本机的时间（用于后续帧率/延迟统计），不参与 DSP。
library;

import 'dart:typed_data';

/// 一段连续复数基带采样块。
class IqBlock {
  /// 同相分量，长度 == [q].length。
  final Float32List i;

  /// 正交分量，长度 == [i].length。
  final Float32List q;

  /// 数据到达本机的时间戳。
  final DateTime timestamp;

  IqBlock({required this.i, required this.q, required this.timestamp})
    : assert(i.length == q.length, 'I/Q length mismatch');

  /// 采样点数。
  int get length => i.length;
}
