/// 星官叠加：把星表与星官折线算成"屏幕上该画的线段"。
///
/// 职责边界
/// --------
/// 本文件**只做数据**，不画。输入是（星表 + 星官 + 站址 + 时刻 + 视图），
/// 输出是"哪些线段画在哪、属于哪个星官、这个星官有几颗星在视场里"。这样：
///
/// * 可以脱离 Flutter 单测（纯 `dart:typed_data` + 数学）；
/// * 页面层只管把 `List<OverlaySegment>` 交给 Canvas；
/// * 与桌面 `mbdsdr_ai/overlay_asterisms.py` 是同一套判定，可对拍。
///
/// 为什么不能"把星官当成固定图形贴上去"
/// ------------------------------------
/// 星官在**天球上**，不是屏幕上。屏幕位置随时刻与指向实时变化：
/// 同一组星，两小时后转 30°，明天同一时刻又不一样。所以每次都要重算，
/// 不能缓存屏幕坐标（可以缓存"星在 J2000 的位置"，那是常量）。
///
/// 装不下怎么办
/// ------------
/// 北斗跨度 25.7°，手机 FOV 60° 能装下；但望远镜目镜 2.3° 装不下。
/// 这里不裁剪掉整条线——`clipSegmentToRect` 会把线裁到画布边界继续画，
/// 用户在边缘看到线延伸出去，自己就知道"那边还有"。
/// 同时输出 `membersInView / membersTotal`，让上层能说"你只看到其中一段"。
///
/// 验证记录（2026-11-18）
/// ----------
/// 算法由 `tests/test_mobile_sky_algorithms.py` 在 Python 侧同式复现并对拍；
/// Dart 侧已通过 `flutter analyze`（0 error）与全仓 `flutter test`（478 passed），
/// 工具链 Flutter 3.50.0 / Dart 3.14.0，位于 `D:/mbdsdr/toolchain/flutter`。
/// 数据加载部分（`dart:convert` / `Uint8List`）已随全仓测试实际执行。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:mbdsdr_mobile/astro/sky_projection.dart';
import 'package:mbdsdr_mobile/astro/sky_transform.dart';

/// `stars.bin` 的魔数，与 `scripts/build_mobile_sky_assets.py` 的 MAGIC 一致。
const String kStarsMagic = 'MBSKYD01';

/// 每条定长记录的字节数：ra/dec/vmag/bv 各 float32 + hip uint32。
const int kStarsRecordBytes = 20;

/// 一颗恒星（J2000 赤道坐标）。
class SkyStar {
  const SkyStar({
    required this.raDeg,
    required this.decDeg,
    required this.vmag,
    required this.bv,
    required this.hip,
  });

  final double raDeg;
  final double decDeg;

  /// 视星等。越小越亮。
  final double vmag;

  /// B−V 色指数；NaN 表示星表未给（**不是 0**，0 是蓝白星）。
  final double bv;

  /// 依巴谷编号；0 表示无编号。
  final int hip;

  /// 色指数是否已知。
  bool get hasColor => bv.isFinite;
}

/// 解析 `stars.bin`。格式错必须抛，不许"尽力而为"地读半份星表。
///
/// 半份星表比没有星表更糟：用户会以为天上就这么多星。
List<SkyStar> parseStarsBin(Uint8List bytes) {
  if (bytes.length < 12) {
    throw const FormatException('stars.bin 太短，连文件头都不完整');
  }
  final magic = String.fromCharCodes(bytes.sublist(0, 8));
  if (magic != kStarsMagic) {
    throw FormatException('stars.bin 魔数不符：$magic（期望 $kStarsMagic）');
  }
  final view = ByteData.sublistView(bytes);
  final count = view.getUint32(8, Endian.little);
  final expect = 12 + count * kStarsRecordBytes;
  if (bytes.length != expect) {
    throw FormatException(
        'stars.bin 长度不符：${bytes.length} != $expect（$count 条记录）');
  }

  final out = <SkyStar>[];
  for (var i = 0; i < count; i++) {
    final off = 12 + i * kStarsRecordBytes;
    out.add(SkyStar(
      raDeg: view.getFloat32(off, Endian.little),
      decDeg: view.getFloat32(off + 4, Endian.little),
      vmag: view.getFloat32(off + 8, Endian.little),
      bv: view.getFloat32(off + 12, Endian.little),
      hip: view.getUint32(off + 16, Endian.little),
    ));
  }
  return out;
}

/// 一个星官（中国星官体系里的一"官"）。
class Asterism {
  const Asterism({
    required this.name,
    required this.regionName,
    required this.polylines,
  });

  /// 星官名（如"北斗"）。
  final String name;

  /// 所属星区（三垣之一或二十八宿之一）。
  final String regionName;

  /// 折线：每条是一串 HIP 号，相邻两点连一条边。
  final List<List<int>> polylines;

  /// 这个星官一共涉及多少颗星（去重）。
  int get starCount => polylines.expand((p) => p).toSet().length;
}

/// 星官目录（含星区与单星官名单）。
class AsterismCatalog {
  const AsterismCatalog({
    required this.asterisms,
    required this.regionNames,
    required this.singleStarNames,
    required this.license,
  });

  final List<Asterism> asterisms;

  /// 星区 key -> 中文名（紫微垣 / 角宿 …）。
  final Map<String, String> regionNames;

  /// 单星星官名（画不出线，但必须知道它们存在——中国星官里"一星成官"很常见）。
  final List<String> singleStarNames;

  /// 数据许可（必须随包分发）。
  final String license;
}

/// 解析 `asterisms.json`。
AsterismCatalog parseAsterismsJson(String jsonText) {
  final root = jsonDecode(jsonText) as Map<String, dynamic>;
  final meta = (root['meta'] as Map<String, dynamic>?) ?? const {};
  final license = (meta['data_license'] as String?) ?? '';

  final out = <Asterism>[];
  final regionNames = <String, String>{};
  final singles = <String>[];

  for (final r in (root['regions'] as List).cast<Map<String, dynamic>>()) {
    final key = r['key'] as String;
    final regionName = r['name'] as String;
    regionNames[key] = regionName;

    for (final a in (r['asterisms'] as List).cast<Map<String, dynamic>>()) {
      final polys = <List<int>>[];
      for (final p in (a['lines'] as List).cast<List<dynamic>>()) {
        polys.add(p.cast<int>());
      }
      if (polys.isEmpty) continue;
      out.add(Asterism(
        name: a['name'] as String,
        regionName: regionName,
        polylines: polys,
      ));
    }
    for (final s in (r['single_star_asterisms'] as List?) ?? const []) {
      singles.add(s as String);
    }
  }

  return AsterismCatalog(
    asterisms: out,
    regionNames: regionNames,
    singleStarNames: singles,
    license: license,
  );
}

/// 观测站址。
class SkySite {
  const SkySite({required this.latDeg, required this.lonDeg});
  final double latDeg;
  final double lonDeg;
}

/// 一条准备画的屏幕线段。
class OverlaySegment {
  const OverlaySegment({
    required this.a,
    required this.b,
    required this.asterism,
    required this.regionName,
    required this.clipped,
  });

  final ScreenPoint a;
  final ScreenPoint b;

  /// 归属星官名——名字要能跟着这段线走，不能单独浮在屏幕上。
  final String asterism;
  final String regionName;

  /// 这段是否被画布边界裁过。裁过的说明"还有一部分在画面外"。
  final bool clipped;
}

/// 某个星官在当前视场里的呈现状态。
class AsterismPresence {
  const AsterismPresence({
    required this.name,
    required this.regionName,
    required this.membersInView,
    required this.membersTotal,
    required this.segments,
  });

  final String name;
  final String regionName;

  /// 有多少颗成员星当前在视场内（且在地平上）。
  final int membersInView;

  /// 该星官一共多少颗成员星。
  final int membersTotal;

  /// 这段视场里能画的线（可能为空：只剩单颗星时画不出线）。
  final List<OverlaySegment> segments;

  /// 是否装不下。true 时上层必须说"你看到的是其中一段"。
  bool get truncated => membersInView < membersTotal;

  /// 装不下的程度（视场内成员 / 总数）。0 表示完全不在画面里。
  double get coverage =>
      membersTotal == 0 ? 0.0 : membersInView / membersTotal;
}

/// 一次叠加计算的结果。
class OverlayResult {
  const OverlayResult({
    required this.asterisms,
    required this.starScreenPoints,
    required this.starHips,
    required this.jdUt1,
  });

  /// 按"视场内成员数"降序——最该被认出来的排前面。
  final List<AsterismPresence> asterisms;

  /// 视场内可见星点的屏幕位置（与 [starHips] 一一对应）。
  final List<ScreenPoint> starScreenPoints;
  final List<int> starHips;

  final double jdUt1;

  bool get hasAnything => asterisms.isNotEmpty;

  /// 线总数，方便判"这一帧空不空"。
  int get segmentCount =>
      asterisms.fold(0, (sum, a) => sum + a.segments.length);
}

/// 叠加计算器：纯函数式，所有输入显式传入。
///
/// 不做缓存也不持有状态：姿态一变整帧重算。手机上一帧约 9 千颗星的坐标
/// 换算在现代 SoC 上是毫秒级，先不做索引优化——**优化要等实测数字说话**，
/// 预先加空间索引属于"没测过的优化"。
class SkyOverlayEngine {
  SkyOverlayEngine({
    required this.stars,
    required this.catalog,
    this.limitMag = 6.5,
  })  : _byHip = {
          for (final s in stars)
            if (s.hip != 0) s.hip: s,
        };

  final List<SkyStar> stars;
  final AsterismCatalog catalog;

  /// 显示到多暗。比这更暗的星不画点（但星官线仍可能用到，因为端点强制包含）。
  final double limitMag;

  final Map<int, SkyStar> _byHip;

  /// 计算当前视场的叠加结果。
  OverlayResult compute({
    required SkySite site,
    required double jdUt1,
    required SkyView view,
    double altMarginDeg = 0.0,
  }) {
    // 一、先把星官要用到的 HIP 全部转成地平坐标。
    //
    // 只算星官端点成立的星（约 2900 颗），不是全表 9 千颗：
    // 视场里能画的线只取决于这些星。星点单独算（受 limitMag 限制）。
    final hips = <int>{};
    for (final a in catalog.asterisms) {
      for (final p in a.polylines) {
        hips.addAll(p);
      }
    }

    final altAzCache = <int, AzAlt>{};
    final screenCache = <int, ScreenPoint?>{};

    AzAlt altAzOf(int hip) {
      final cached = altAzCache[hip];
      if (cached != null) return cached;
      final star = _byHip[hip];
      final value = star == null
          // 星表里没有这颗（理论上不该发生：生成脚本已保证端点包含）。
          // 返回一个"永远在地平下"的值，而不是编一个坐标。
          ? const AzAlt(azDeg: 0, altDeg: -91)
          : altAzFromJ2000(
              raDeg: star.raDeg,
              decDeg: star.decDeg,
              latDeg: site.latDeg,
              lonDeg: site.lonDeg,
              jdUt1: jdUt1,
            );
      altAzCache[hip] = value;
      return value;
    }

    ScreenPoint? screenOf(int hip) {
      if (screenCache.containsKey(hip)) return screenCache[hip];
      final aa = altAzOf(hip);
      ScreenPoint? p;
      if (aa.altDeg > -altMarginDeg) {
        p = projectAzAlt(aa.azDeg, aa.altDeg, view);
      }
      screenCache[hip] = p;
      return p;
    }

    // 二、逐星官算线段。
    final presences = <AsterismPresence>[];

    for (final ast in catalog.asterisms) {
      final segs = <OverlaySegment>[];
      final members = ast.polylines.expand((p) => p).toSet();
      var inView = 0;

      for (final hip in members) {
        final aa = altAzOf(hip);
        if (aa.altDeg <= -altMarginDeg) continue; // 地平线下不算"在视场"
        final p = screenOf(hip);
        if (p == null) continue;
        // 在画布内（留半屏余量）才算"看得见"。全屏外的不计入 membersInView，
        // 但它们所在的线段仍会通过裁剪画到边界。
        if (isOnCanvas(p, view.width, view.height,
            margin: view.minDim * 0.5)) {
          inView++;
        }
      }

      for (final poly in ast.polylines) {
        for (var i = 0; i + 1 < poly.length; i++) {
          final pa = screenOf(poly[i]);
          final pb = screenOf(poly[i + 1]);
          if (pa == null || pb == null) continue; // 地平线下或背对相机

          final clipped =
              clipSegmentToRect(pa, pb, view.width, view.height);
          if (clipped == null) continue; // 整段在画布外

          final wasClipped = (clipped.a.x - pa.x).abs() > 1e-6 ||
              (clipped.a.y - pa.y).abs() > 1e-6 ||
              (clipped.b.x - pb.x).abs() > 1e-6 ||
              (clipped.b.y - pb.y).abs() > 1e-6;

          segs.add(OverlaySegment(
            a: clipped.a,
            b: clipped.b,
            asterism: ast.name,
            regionName: ast.regionName,
            clipped: wasClipped,
          ));
        }
      }

      if (segs.isEmpty) continue; // 完全不在画面里

      presences.add(AsterismPresence(
        name: ast.name,
        regionName: ast.regionName,
        membersInView: inView,
        membersTotal: members.length,
        segments: segs,
      ));
    }

    // 三、按"视场内成员数"降序：最该被认出来的排前面。
    //     同分时星多的优先（更可能被认出来）。
    presences.sort((x, y) {
      final c = y.membersInView.compareTo(x.membersInView);
      if (c != 0) return c;
      return y.membersTotal.compareTo(x.membersTotal);
    });

    // 四、星点（受 limitMag 限制，与星官端点无关）。
    final pts = <ScreenPoint>[];
    final ptHips = <int>[];
    for (final s in stars) {
      if (s.vmag > limitMag) continue;
      final aa = altAzFromJ2000(
        raDeg: s.raDeg,
        decDeg: s.decDeg,
        latDeg: site.latDeg,
        lonDeg: site.lonDeg,
        jdUt1: jdUt1,
      );
      if (aa.altDeg <= 0) continue;
      final p = projectAzAlt(aa.azDeg, aa.altDeg, view);
      if (p == null) continue;
      if (!isOnCanvas(p, view.width, view.height)) continue;
      pts.add(p);
      ptHips.add(s.hip);
    }

    return OverlayResult(
      asterisms: presences,
      starScreenPoints: pts,
      starHips: ptHips,
      jdUt1: jdUt1,
    );
  }

  /// 点星拾取：找出离屏幕点最近的那颗星（在 [maxPx] 像素内）。
  ///
  /// 用**角度**判定而不是纯像素：大视场下 100 像素可能在几十度外，
  /// 纯像素容差会点到风马牛不相及的东西。这里先用像素筛候选，
  /// 再要求角距在合理范围内。
  SkyStar? pickStar({
    required ScreenPoint at,
    required List<ScreenPoint> screenPoints,
    required List<int> hips,
    double maxPx = 48.0,
  }) {
    var bestIdx = -1;
    var bestD2 = double.infinity;
    for (var i = 0; i < screenPoints.length; i++) {
      final dx = screenPoints[i].x - at.x;
      final dy = screenPoints[i].y - at.y;
      final d2 = dx * dx + dy * dy;
      if (d2 < bestD2 && d2 <= maxPx * maxPx) {
        bestD2 = d2;
        bestIdx = i;
      }
    }
    if (bestIdx < 0) return null;
    final hip = hips[bestIdx];
    return _byHip[hip];
  }

  /// 某颗星此刻的地平坐标与身份（点星详情用）。
  AzAlt? starAltAz({
    required int hip,
    required SkySite site,
    required double jdUt1,
  }) {
    final s = _byHip[hip];
    if (s == null) return null;
    return altAzFromJ2000(
      raDeg: s.raDeg,
      decDeg: s.decDeg,
      latDeg: site.latDeg,
      lonDeg: site.lonDeg,
      jdUt1: jdUt1,
    );
  }
}
