/// 星名目录：HIP → 中文名 / 西名 / 星官 / 星区 / 星座。
///
/// 为什么单独一层
/// --------------
/// `stars.bin` 里只有坐标与星等，回答不了「我正指着的是什么星」。
/// `scripts/build_mobile_sky_assets.py` 会生成 `star_names.json`
/// （2906 颗有名字的星，含中国星官名与西名），本文件只负责解析与
/// 「准星中心识别」的纯函数，不画 UI。
///
/// 口径
/// ----
/// * 结构错误抛 [FormatException]，由资产层降级为「星名不可用」并写明原因；
///   星名是叠加层，缺了仍能显示星点，但**必须**让用户知道名字不可用；
/// * 显示名优先中文；中文缺失时退回西名；两者都没有就只报 HIP 编号，
///   绝不编一个名字；
/// * 识别结果必须带**距中心角距**，不把 8° 外的星说成「就在准星上」。
library;

import 'dart:convert';

/// 一颗星的名字与文化归属。
class StarNameEntry {
  const StarNameEntry({
    this.zh = '',
    this.en = '',
    this.alt = const <String>[],
    this.asterisms = const <String>[],
    this.regions = const <String>[],
    this.con = '',
  });

  /// 中文名（如「织女一」）。
  final String zh;

  /// 西名（如「Vega」）。
  final String en;

  /// 其他中文别名（如「织女」）。
  final List<String> alt;

  /// 所属星官（可多个，如「织女」）。
  final List<String> asterisms;

  /// 所属星区（如「牛宿」）。
  final List<String> regions;

  /// 星座缩写（如「Lyr」）。
  final String con;

  /// 是否有可展示的名字。
  bool get hasName => zh.isNotEmpty || en.isNotEmpty;

  /// 主显示名：中文优先，其次西名；都没有为空串。
  String get primary => zh.isNotEmpty ? zh : en;

  /// 需要并列展示的西名：与主名相同或为空时返回 null。
  String? get secondary {
    if (en.isEmpty || en == primary) return null;
    return en;
  }
}

/// 解析 `star_names.json`。
///
/// 顶层必须是 `{ "HIP": {...} }`。HIP 解析失败或 ≤0 的条目直接跳过
/// （生成脚本不会产出，出现也不该拖垮整份目录）；条目结构错误必须抛，
/// 因为「名字对不上号」比「没有名字」更危险。
Map<int, StarNameEntry> parseStarNamesJson(String text) {
  final Object? decoded = jsonDecode(text);
  if (decoded is! Map) {
    throw const FormatException('星名目录顶层必须是对象');
  }
  final Map<int, StarNameEntry> out = <int, StarNameEntry>{};
  for (final Object? key in decoded.keys) {
    final int? hip = int.tryParse('$key');
    if (hip == null || hip <= 0) continue;
    final Object? value = decoded[key];
    if (value is! Map) {
      throw FormatException('HIP $hip 的星名条目不是对象');
    }
    String readString(String field) {
      final Object? raw = value[field];
      if (raw == null) return '';
      if (raw is! String) {
        throw FormatException('HIP $hip 的 $field 不是字符串');
      }
      return raw;
    }

    List<String> readList(String field) {
      final Object? raw = value[field];
      if (raw == null) return const <String>[];
      if (raw is! List) {
        throw FormatException('HIP $hip 的 $field 不是数组');
      }
      final List<String> items = <String>[];
      for (final Object? item in raw) {
        if (item is! String) {
          throw FormatException('HIP $hip 的 $field 含非字符串项');
        }
        items.add(item);
      }
      return List<String>.unmodifiable(items);
    }

    out[hip] = StarNameEntry(
      zh: readString('zh'),
      en: readString('en'),
      alt: readList('alt'),
      asterisms: readList('asterisms'),
      regions: readList('regions'),
      con: readString('con'),
    );
  }
  return out;
}

/// 准星中心识别结果（由页面从投影结果构造）。
class CenterStarIdentification {
  const CenterStarIdentification({
    required this.hip,
    required this.vmag,
    required this.separationDeg,
    this.name,
  });

  final int hip;
  final double vmag;

  /// 与视场中心的球面角距（度）。
  final double separationDeg;

  /// 命中星名目录时的名字；未收录为 null。
  final StarNameEntry? name;
}

/// 准星中心文案（纯函数，便于单测与多入口复用）。
///
/// 口径：名字不可用就说不可用；没有命中就说「附近无亮星」；命中但未收录
/// 名字时只报 HIP 与星等，绝不编名。角距保留 1 位小数——它本身就是
/// 「你指得准不准」的读数，不该被四舍五入成 0°。
String centerStarText({
  required bool namesAvailable,
  required CenterStarIdentification? identification,
}) {
  if (!namesAvailable) {
    return '星名数据不可用，无法识别中心星';
  }
  final CenterStarIdentification? id = identification;
  if (id == null) {
    return '中心附近无亮星';
  }
  final String separation = '距中心 ${id.separationDeg.toStringAsFixed(1)}°';
  final StarNameEntry? name = id.name;
  if (name == null || !name.hasName) {
    return 'HIP ${id.hip} · $separation · ${id.vmag.toStringAsFixed(1)}等';
  }
  final String? secondary = name.secondary;
  final String display = secondary == null
      ? name.primary
      : '${name.primary}（$secondary）';
  return '$display · $separation';
}
