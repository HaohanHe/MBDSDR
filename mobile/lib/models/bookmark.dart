// 频点书签值对象：名称 / 频率 / 解调模式 / 带宽。
//
// 与桌面端 cpp/src/ui/bookmark_manager.h 的 Bookmark 字段对齐，但**绝不预存任何
// 电台名/位置**：name 默认可为空（UI 用频率占位），bandwidthHz=0 表示未指定。
// 持久化为 JSON 数组（见 settings_service.dart），读写均做容错，非法项静默丢弃。
library;

import 'radio_state.dart';

/// 一个频点书签。不可变。
class Bookmark {
  const Bookmark({
    required this.name,
    required this.frequencyHz,
    this.mode = '',
    this.bandwidthHz = 0,
  });

  /// 用户自填名称；空串表示未命名（UI 用频率占位显示）。不内置任何台名。
  final String name;

  /// 中心频率（Hz），必须 > 0。
  final int frequencyHz;

  /// 解调模式持久化字符串（'nfm' / 'wfm'）；空串表示未指定。
  /// 与 SettingsService.demodMode 的落盘写法一致（小写）。
  final String mode;

  /// 信道带宽（Hz）；0 表示未指定（移动端带宽由模式派生，此字段先记录备查）。
  final int bandwidthHz;

  /// 模式枚举视图：未指定/未知时返回 null（调用方保持当前模式不动）。
  DemodMode? get modeEnum => switch (mode) {
        'nfm' => DemodMode.nfm,
        'wfm' => DemodMode.wfm,
        _ => null,
      };

  /// 面向 UI 的短标签：有名显示名，无名显示频率。
  String get displayLabel {
    final hz = (frequencyHz / 1e6).toStringAsFixed(4);
    if (name.trim().isEmpty) return '$hz MHz';
    return '${name.trim()} · $hz';
  }

  Bookmark copyWith({String? name, int? frequencyHz, String? mode, int? bandwidthHz}) {
    return Bookmark(
      name: name ?? this.name,
      frequencyHz: frequencyHz ?? this.frequencyHz,
      mode: mode ?? this.mode,
      bandwidthHz: bandwidthHz ?? this.bandwidthHz,
    );
  }

  /// 落盘 JSON。
  Map<String, dynamic> toJson() => <String, dynamic>{
        'name': name,
        'frequencyHz': frequencyHz,
        'mode': mode,
        'bandwidthHz': bandwidthHz,
      };

  /// 从 JSON 读回；非法（缺频率/频率非正）返回 null，由上层丢弃。
  static Bookmark? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final hz = raw['frequencyHz'] ?? raw['freq'];
    if (hz is! num || hz.toInt() <= 0) return null;
    final name = raw['name'];
    final mode = raw['mode'];
    final bw = raw['bandwidthHz'] ?? raw['bw'];
    return Bookmark(
      name: name is String ? name.trim() : '',
      frequencyHz: hz.toInt(),
      mode: mode is String ? mode.trim().toLowerCase() : '',
      bandwidthHz: bw is num ? bw.toInt() : 0,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Bookmark &&
          other.frequencyHz == frequencyHz &&
          other.name == name &&
          other.mode == mode &&
          other.bandwidthHz == bandwidthHz;

  @override
  int get hashCode => Object.hash(name, frequencyHz, mode, bandwidthHz);
}
