/// 指天视图的数据包：恒星表 + 星官目录 + 许可。
///
/// 为什么要单独一层
/// ----------------
/// 星表与星官目录是**打包资产**，不是代码常量：解析器 (`parseStarsBin` /
/// `parseAsterismsJson`) 只认字节，不认从哪里读。这一层负责「从哪一个来源
/// 取字节」，并保证**读不到就报错**——半份星表比没有星表更糟：用户会以为
/// 天上就这么多星。
///
/// 诚实边界
/// --------
/// * 资产缺失 / 魔数不符 / 长度不符 / JSON 坏 → 抛 [SkyAssetException]，
///   带上是哪一份资产；上层显示真实原因，不退回空星表。
/// * 不内置任何星表副本：数据只来自 `mobile/assets/sky/`。
library;

import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:mbdsdr_mobile/astro/asterism_overlay.dart';
import 'package:mbdsdr_mobile/astro/star_names.dart';

/// 资产路径（与 `scripts/build_mobile_sky_assets.py` 的产物一致）。
const String kStarsAssetPath = 'assets/sky/stars.bin';
const String kAsterismsAssetPath = 'assets/sky/asterisms.json';
const String kStarNamesAssetPath = 'assets/sky/star_names.json';
const String kSkyLicenseAssetPath = 'assets/sky/SKY_DATA_LICENSE.txt';

/// 读资产失败：消息里必须说清是哪一份、为什么。
class SkyAssetException implements Exception {
  const SkyAssetException(this.asset, this.reason);

  final String asset;
  final String reason;

  @override
  String toString() => '星表资产读取失败（$asset）：$reason';
}

/// 一份可用的天空数据。
class SkyAssets {
  const SkyAssets({
    required this.stars,
    required this.catalog,
    required this.license,
    this.names = const <int, StarNameEntry>{},
    this.namesError = '',
  });

  final List<SkyStar> stars;
  final AsterismCatalog catalog;

  /// 数据许可原文；为空表示资产未带许可文件（上层应显示「许可缺失」）。
  final String license;

  /// HIP → 星名（中文名/西名/星官）。为空表示星名层不可用。
  final Map<int, StarNameEntry> names;

  /// 星名目录不可用的真实原因；空串=未发生错误。
  ///
  /// 星名是**叠加层**：缺失不阻断星点显示，但必须让用户知道
  /// 「现在认不出名字」，不能静默当作没有名字的星。
  final String namesError;

  /// 星名是否可用来做中心识别。
  bool get hasStarNames => names.isNotEmpty;

  bool get isEmpty => stars.isEmpty && catalog.asterisms.isEmpty;

  /// 从字节解析（纯函数，便于用坏样本测反证）。
  ///
  /// 星表/星官仍是「读不到就抛」；星名目录是可选叠加层，解析失败记
  /// [namesError] 而不拖垮整页星空。
  static SkyAssets fromBytes({
    required Uint8List starsBytes,
    required String asterismsJson,
    required String license,
    String? starNamesJson,
    String starNamesError = '',
  }) {
    final List<SkyStar> stars;
    try {
      stars = parseStarsBin(starsBytes);
    } on FormatException catch (e) {
      throw SkyAssetException(kStarsAssetPath, e.message);
    }
    final AsterismCatalog catalog;
    try {
      catalog = parseAsterismsJson(asterismsJson);
    } on FormatException catch (e) {
      throw SkyAssetException(kAsterismsAssetPath, e.message);
    } on TypeError catch (e) {
      throw SkyAssetException(kAsterismsAssetPath, '结构不符：$e');
    }
    Map<int, StarNameEntry> names = const <int, StarNameEntry>{};
    String namesErrorOut = starNamesError;
    if (starNamesJson != null) {
      try {
        names = parseStarNamesJson(starNamesJson);
      } on FormatException catch (e) {
        namesErrorOut = '星名目录解析失败：${e.message}';
      } on TypeError catch (e) {
        namesErrorOut = '星名目录结构不符：$e';
      }
    }
    return SkyAssets(
      stars: stars,
      catalog: catalog,
      license: license,
      names: names,
      namesError: namesErrorOut,
    );
  }
}

/// 资产来源抽象：生产走 Flutter bundle，测试可注入假来源。
abstract class SkyAssetSource {
  Future<SkyAssets> load();
}

/// 从打包资产读取。
class BundleSkyAssetSource implements SkyAssetSource {
  const BundleSkyAssetSource();

  @override
  Future<SkyAssets> load() async {
    final ByteData stars;
    final ByteData asterisms;
    final ByteData license;
    try {
      stars = await rootBundle.load(kStarsAssetPath);
    } catch (e) {
      throw SkyAssetException(kStarsAssetPath, '$e');
    }
    try {
      asterisms = await rootBundle.load(kAsterismsAssetPath);
    } catch (e) {
      throw SkyAssetException(kAsterismsAssetPath, '$e');
    }
    String licenseText = '';
    try {
      license = await rootBundle.load(kSkyLicenseAssetPath);
      licenseText = utf8.decode(license.buffer.asUint8List(), allowMalformed: true);
    } catch (_) {
      // 许可文件缺失不阻断星空显示，但上层必须显示「许可缺失」。
      licenseText = '';
    }
    // 星名目录是叠加层：读不到就降级并写明原因，不影响星点/星官显示。
    String? namesJson;
    String namesError = '';
    try {
      final ByteData names = await rootBundle.load(kStarNamesAssetPath);
      namesJson =
          utf8.decode(names.buffer.asUint8List(), allowMalformed: true);
    } catch (e) {
      namesError = '星名目录读取失败：$e';
    }
    return SkyAssets.fromBytes(
      starsBytes: stars.buffer.asUint8List(),
      asterismsJson:
          utf8.decode(asterisms.buffer.asUint8List(), allowMalformed: true),
      license: licenseText,
      starNamesJson: namesJson,
      starNamesError: namesError,
    );
  }
}
