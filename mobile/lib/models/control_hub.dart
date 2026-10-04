// ============================================================================
// 桌面 ControlHub 远程解码结果值对象（移动端 = 只读查看器）。
// ----------------------------------------------------------------------------
// 冻结 HTTP 契约（见 docs/learn/phase25/_PHASE25_SPEC.md，并行 A 块实现）：
//   GET /status           -> {connected, status, error_message, mode,
//                             frequency_hz, bandwidth_hz, ...}
//   GET /pocsag_messages  -> {count, messages:[{address,function,text,type}]}
//   GET /m17_calls        -> {count, calls:[{src,dst,type,is_stream,crc_ok,
//                             voice_undecoded,meta,payload}]}
//   GET /vor_radial       -> {locked, radial_deg, quality, morse_id}
//
// 诚实原则：解码发生在桌面引擎；移动端只渲染远程真实结果。所有 fromJson
// 均容错——缺字段/类型不符静默回退安全默认值，绝不伪造读数。VOR 的
// [locked] 是唯一可信门：未锁定时 [radialDeg] 无意义，调用方不得据此画方位。
library;

import 'package:flutter/foundation.dart' show immutable;

// ---------------------------------------------------------------------------
// /status
// ---------------------------------------------------------------------------

/// 桌面引擎整机状态快照（/status 全量的移动端子集）。
///
/// 字段名与 JSON snake_case 对齐；未知键一律忽略（契约用 "..." 表示未来会扩）。
@immutable
class EngineStatus {
  /// 引擎是否已连接接收机。
  final bool connected;

  /// 五态状态字符串（idle/rx/tx/...），原样透传。
  final String status;

  /// 引擎自报的错误信息（无设备/故障原因），空串表示无。
  final String errorMessage;

  /// 当前解调/接收模式字符串。
  final String mode;

  /// 当前中心频率（Hz）。
  final int frequencyHz;

  /// 当前信道带宽（Hz）。
  final int bandwidthHz;

  const EngineStatus({
    required this.connected,
    required this.status,
    required this.errorMessage,
    required this.mode,
    required this.frequencyHz,
    required this.bandwidthHz,
  });

  /// 全部字段缺省回退安全值（未连接/无读数），绝不抛。
  static const EngineStatus empty = EngineStatus(
    connected: false,
    status: '',
    errorMessage: '',
    mode: '',
    frequencyHz: 0,
    bandwidthHz: 0,
  );

  static EngineStatus fromJson(Object? raw) {
    if (raw is! Map) return empty;
    bool asBool(Object? v) => v is bool ? v : false;
    int asInt(Object? v) => v is num ? v.toInt() : 0;
    String asStr(Object? v) => v is String ? v : '';
    return EngineStatus(
      connected: asBool(raw['connected']),
      status: asStr(raw['status']),
      errorMessage: asStr(raw['error_message']),
      mode: asStr(raw['mode']),
      frequencyHz: asInt(raw['frequency_hz']),
      bandwidthHz: asInt(raw['bandwidth_hz']),
    );
  }
}

// ---------------------------------------------------------------------------
// /pocsag_messages
// ---------------------------------------------------------------------------

/// POCSAG 消息载荷类型（与桌面 dsp::PocsagMessage::Type 对齐）。
enum PocsagType {
  unknown,
  numeric,
  alpha;

  /// 面向 UI 的短标签。
  String get label => switch (this) {
        PocsagType.unknown => '未知',
        PocsagType.numeric => '数字',
        PocsagType.alpha => '字母',
      };
}

/// 一条远程 POCSAG 寻呼消息（RIC 地址 + 功能位 + 解码文本）。
@immutable
class PocsagMessage {
  /// RIC 地址（0..2097151）。
  final int address;

  /// 功能位（0..3）。
  final int function;

  /// 解码文本（BCD 数字 / 7-bit ASCII 字母）；可能为空串。
  final String text;

  /// 载荷类型。
  final PocsagType type;

  const PocsagMessage({
    required this.address,
    required this.function,
    required this.text,
    required this.type,
  });

  static PocsagType _parseType(Object? raw) {
    if (raw is String) {
      final String s = raw.trim().toLowerCase();
      if (s == 'numeric' || s == 'num') return PocsagType.numeric;
      if (s == 'alpha' || s == 'ascii') return PocsagType.alpha;
      return PocsagType.unknown;
    }
    if (raw is num) {
      return switch (raw.toInt()) {
        1 => PocsagType.numeric,
        2 => PocsagType.alpha,
        _ => PocsagType.unknown,
      };
    }
    return PocsagType.unknown;
  }

  static PocsagMessage? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final addr = raw['address'];
    if (addr is! num) return null; // 无地址的行无意义，丢弃。
    return PocsagMessage(
      address: addr.toInt(),
      function: raw['function'] is num ? (raw['function'] as num).toInt() : 0,
      text: raw['text'] is String ? raw['text'] as String : '',
      type: _parseType(raw['type']),
    );
  }
}

// ---------------------------------------------------------------------------
// /m17_calls
// ---------------------------------------------------------------------------

/// 一条远程 M17 呼叫/帧（呼号 + 类型 + CRC + 是否语音未解码）。
///
/// 诚实边界：Codec2 不在移动端（也不在通用代码）解码；语音帧以
/// [voiceUndecoded]=true 上报，面板标「语音 · 未解码」，绝不出伪造音频。
@immutable
class M17Call {
  /// 源呼号（全 ones 时桌面给 "BROADCAST"）。
  final String src;

  /// 目的呼号。
  final String dst;

  /// 原始 16-bit LSF TYPE 字。
  final int type;

  /// true=语音/数据流，false=分组包。
  final bool isStream;

  /// LSF CRC-16 是否校验通过。
  final bool crcOk;

  /// 语音帧未解码（Codec2 未内置）。
  final bool voiceUndecoded;

  /// META 字节的展示串（hex 或空）；仅用于排查，不伪造语义。
  final String meta;

  /// 数据帧载荷字节的展示串（hex 或空）；语音帧通常为空。
  final String payload;

  const M17Call({
    required this.src,
    required this.dst,
    required this.type,
    required this.isStream,
    required this.crcOk,
    required this.voiceUndecoded,
    required this.meta,
    required this.payload,
  });

  /// bytes 字段在 JSON 里既可能是 hex/base64 字符串，也可能是数字数组；
  /// 统一收敛为展示串（数字数组 -> 小写 hex），其余原样保留字符串。
  static String _bytesToDisplay(Object? v) {
    if (v is String) return v;
    if (v is List) {
      final buf = StringBuffer();
      for (final Object? b in v) {
        if (b is num) {
          final int byte = b.toInt() & 0xff;
          buf.write(byte.toRadixString(16).padLeft(2, '0'));
        }
      }
      return buf.toString();
    }
    return '';
  }

  static M17Call? fromJson(Object? raw) {
    if (raw is! Map) return null;
    // 至少要有 src/dst 之一才算一条呼叫；否则丢弃。
    final src = raw['src'];
    final dst = raw['dst'];
    if (src is! String && dst is! String) return null;
    return M17Call(
      src: src is String ? src : '',
      dst: dst is String ? dst : '',
      type: raw['type'] is num ? (raw['type'] as num).toInt() : 0,
      isStream: raw['is_stream'] is bool ? raw['is_stream'] as bool : false,
      crcOk: raw['crc_ok'] is bool ? raw['crc_ok'] as bool : false,
      voiceUndecoded: raw['voice_undecoded'] is bool
          ? raw['voice_undecoded'] as bool
          : false,
      meta: _bytesToDisplay(raw['meta']),
      payload: _bytesToDisplay(raw['payload']),
    );
  }
}

// ---------------------------------------------------------------------------
// /vor_radial
// ---------------------------------------------------------------------------

/// 一次远程 VOR 径向读数。
///
/// [locked] 是唯一可信门：false 时 [radialDeg] 无意义，面板不得据此画指针/
/// 显示方位，只显示「未锁定」。[quality]∈[0,1]，是子测量一致性（ resultant R）。
@immutable
class VorRadial {
  /// 是否锁定出可靠径向。
  final bool locked;

  /// 磁方位（度，0..359）；仅当 [locked] 才有意义。
  final double radialDeg;

  /// 信号质量 0..1。
  final double quality;

  /// 台站 Morse ID（1020 Hz 键控实际拼出的字符），空串=尚未解出。
  final String morseId;

  const VorRadial({
    required this.locked,
    required this.radialDeg,
    required this.quality,
    required this.morseId,
  });

  /// 诚实未锁定空态——绝不给方位。
  static const VorRadial unlocked = VorRadial(
    locked: false,
    radialDeg: 0,
    quality: 0,
    morseId: '',
  );

  static VorRadial fromJson(Object? raw) {
    if (raw is! Map) return unlocked;
    return VorRadial(
      locked: raw['locked'] is bool ? raw['locked'] as bool : false,
      radialDeg: raw['radial_deg'] is num
          ? (raw['radial_deg'] as num).toDouble()
          : 0,
      quality:
          raw['quality'] is num ? (raw['quality'] as num).toDouble() : 0,
      morseId: raw['morse_id'] is String ? raw['morse_id'] as String : '',
    );
  }
}
