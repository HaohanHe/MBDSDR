// rtl_tcp 客户端 —— 真实协议实现（参考 osmo.com/osmocom rtl_tcp 公开协议）。
//
// 协议要点（来自 rtl_tcp 服务端约定）：
//   * 下行命令均为 5 字节：1 字节命令号 + 4 字节大端 uint32 参数。
//   * 上行数据为服务端持续推送的 uint8 交错 IQ 字节流（I,Q,I,Q,…），
//     每字节为无符号 8-bit 软采样，按 (b - 127.5) / 127.5 归一化到 [-1, 1]。
//
// 本类用 dart:io 的 Socket 直连，不依赖任何第三方 SDR 库。
library;

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import '../dsp/iq.dart';
import '../models/radio_state.dart';

/// rtl_tcp 命令号（与服务端 rtl_tcp.c 一致）。
abstract final class RtlTcpCommand {
  static const int setFrequency = 0x01;
  static const int setSampleRate = 0x02;
  static const int setGainMode = 0x03; // 0=自动, 1=手动
  static const int setGain = 0x04; // 单位 0.1 dB
  static const int setFreqCorrection = 0x05; // PPM
  static const int setIfGain = 0x06;
  static const int setAgcMode = 0x08; // 1=开
  static const int setDirectSampling = 0x09; // 0=关, 1=I, 2=Q
  static const int setGainByIndex = 0x0d;
  static const int setBiasTee = 0x0e; // 1=开
}

/// 真实 rtl_tcp 连接。生命周期：构造 → [connect] → 收发 → [disconnect]。
class RtlTcpClient {
  Socket? _socket;
  StreamSubscription<Uint8List>? _sub;

  ConnectionStatus _status = ConnectionStatus.disconnected;
  String? _lastError;

  /// 上一次错误描述（[ConnectionStatus.error] 时有效）。
  String? get lastError => _lastError;

  ConnectionStatus get status => _status;

  final _statusCtrl = StreamController<ConnectionStatus>.broadcast();
  final _iqCtrl = StreamController<IqBlock>.broadcast();

  /// 连接状态变化广播。
  Stream<ConnectionStatus> get onStatusChanged => _statusCtrl.stream;

  /// 解码后的 IQ 采样块流。Socket 异常会作为 error 事件发到本流。
  Stream<IqBlock> get iqStream => _iqCtrl.stream;

  // ------------------------------------------------------------- 协议编码

  /// 构造一条 5 字节 rtl_tcp 命令：1 字节命令号 + 4 字节大端 uint32 参数。
  /// 纯函数，便于单测。
  static Uint8List buildCommand(int cmd, int param) {
    final buf = Uint8List(5);
    buf[0] = cmd & 0xFF;
    // 大端（network order）写入 32 位无符号参数。
    buf[1] = (param >>> 24) & 0xFF;
    buf[2] = (param >>> 16) & 0xFF;
    buf[3] = (param >>> 8) & 0xFF;
    buf[4] = param & 0xFF;
    return buf;
  }

  /// 把一段**偶数长度**的交错 uint8 IQ 字节解码为归一化 I/Q 浮点。
  /// 纯函数，便于单测。奇数字节的拼包由 [RtlTcpClient] 在流里处理。
  static ({Float32List i, Float32List q}) decodeIq(Uint8List bytes) {
    final pairCount = bytes.length ~/ 2;
    final i = Float32List(pairCount);
    final q = Float32List(pairCount);
    for (var n = 0; n < pairCount; n++) {
      final ib = bytes[n * 2];
      final qb = bytes[n * 2 + 1];
      i[n] = (ib - 127.5) / 127.5;
      q[n] = (qb - 127.5) / 127.5;
    }
    return (i: i, q: q);
  }

  // ------------------------------------------------------------- 连接

  /// 连接到 rtl_tcp 服务端。连接成功后即开始接收 IQ 流。
  Future<void> connect(String host, int port) async {
    if (_status == ConnectionStatus.connecting ||
        _status == ConnectionStatus.connected) {
      return;
    }
    _setStatus(ConnectionStatus.connecting);
    try {
      // Socket 保存在 _socket，由 disconnect()/dispose() 关闭（close_sinks 无法跨方法追踪）。
      // ignore: close_sinks
      final socket = await Socket.connect(host, port);
      // rtl_tcp 是持续大流量上行，关闭 nagle 以降低延迟。
      socket.setOption(SocketOption.tcpNoDelay, true);
      _socket = socket;

      // 处理半包/粘包：最多携带 1 个未配对字节。
      Uint8List carry = Uint8List(0);
      _sub = socket.listen(
        (data) {
          final total = Uint8List(carry.length + data.length)
            ..setRange(0, carry.length, carry)
            ..setRange(carry.length, carry.length + data.length, data);
          final pairCount = total.length ~/ 2;
          final used = pairCount * 2;
          if (pairCount > 0) {
            final payload = Uint8List.sublistView(total, 0, used);
            final decoded = decodeIq(payload);
            _iqCtrl.add(
              IqBlock(i: decoded.i, q: decoded.q, timestamp: DateTime.now()),
            );
          }
          carry = used < total.length
              ? Uint8List.sublistView(total, used)
              : Uint8List(0);
        },
        onError: (Object e, StackTrace st) {
          _fail('Socket 错误: $e');
          _iqCtrl.addError(e, st);
        },
        onDone: () {
          // 服务端关闭：若非主动 disconnect，视为断开。
          if (_status == ConnectionStatus.connected ||
              _status == ConnectionStatus.connecting) {
            _setStatus(ConnectionStatus.disconnected);
          }
        },
        cancelOnError: false,
      );
      _setStatus(ConnectionStatus.connected);
    } catch (e) {
      _fail('连接失败: $e');
      _iqCtrl.addError(e, StackTrace.current);
    }
  }

  void _setStatus(ConnectionStatus s) {
    _status = s;
    if (!_statusCtrl.isClosed) _statusCtrl.add(s);
  }

  void _fail(String msg) {
    _lastError = msg;
    _setStatus(ConnectionStatus.error);
  }

  Future<void> _send(int cmd, int param) async {
    final socket = _socket;
    if (socket == null) return;
    socket.add(buildCommand(cmd, param));
    await socket.flush();
  }

  // ------------------------------------------------------------- 调谐命令

  /// 调谐频率（Hz）。
  Future<void> setFrequencyHz(int hz) =>
      _send(RtlTcpCommand.setFrequency, hz);

  /// 采样率（Hz）。
  Future<void> setSampleRateHz(int hz) =>
      _send(RtlTcpCommand.setSampleRate, hz);

  /// 增益模式：true=自动，false=手动。
  Future<void> setGainMode({required bool automatic}) =>
      _send(RtlTcpCommand.setGainMode, automatic ? 0 : 1);

  /// 手动增益（dB），内部换算为 0.1 dB 单位取整。
  Future<void> setGainDb(double db) =>
      _send(RtlTcpCommand.setGain, (db * 10).round());

  /// 频率校正（PPM）。
  Future<void> setPpm(int ppm) =>
      _send(RtlTcpCommand.setFreqCorrection, ppm);

  /// 板载 AGC：true=开。
  Future<void> setAgcMode({required bool on}) =>
      _send(RtlTcpCommand.setAgcMode, on ? 1 : 0);

  /// IF 增益（0.1 dB 单位）。
  Future<void> setIfGain(int tenthsDb) =>
      _send(RtlTcpCommand.setIfGain, tenthsDb);

  /// 按增益 index 设置增益（0.1 dB 单位）。
  Future<void> setGainByIndex(int index, int tenthsDb) =>
      _send(RtlTcpCommand.setGainByIndex, (index << 16) | (tenthsDb & 0xFFFF));

  /// bias-T 供电：true=开。
  Future<void> setBiasTee({required bool on}) =>
      _send(RtlTcpCommand.setBiasTee, on ? 1 : 0);

  // ------------------------------------------------------------- 断开

  /// 关闭并销毁 Socket，释放资源。
  Future<void> disconnect() async {
    await _sub?.cancel();
    _sub = null;
    final socket = _socket;
    _socket = null;
    if (socket != null) {
      try {
        socket.destroy();
      } catch (_) {
        // destroy 幂等，忽略。
      }
    }
    _setStatus(ConnectionStatus.disconnected);
  }

  /// 释放控制器（本对象不可复用后调用）。
  Future<void> dispose() async {
    await disconnect();
    await _statusCtrl.close();
    await _iqCtrl.close();
  }
}
