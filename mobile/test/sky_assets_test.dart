/// 指天视图的数据包：真实资产可解析、坏资产必须报错。
///
/// 这里读的是 `assets/sky/` 下的**真文件**（与 APK 里同一份），不是测试替身：
/// 星表格式错了、许可文件漏打包，都会在这里红。
library;

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/sky_assets.dart';

void main() {
  test('真实资产能解析出星表 / 星官目录 / 许可', () {
    final SkyAssets assets = SkyAssets.fromBytes(
      starsBytes: File(kStarsAssetPath).readAsBytesSync(),
      asterismsJson: File(kAsterismsAssetPath).readAsStringSync(),
      license: File(kSkyLicenseAssetPath).readAsStringSync(),
    );
    expect(assets.stars.length, greaterThan(1000), reason: '星表条数太少，可能是半份');
    expect(assets.catalog.asterisms, isNotEmpty);
    expect(assets.license.trim(), isNotEmpty);
    expect(assets.isEmpty, isFalse);
  });

  test('坏星表必须抛 SkyAssetException，不许退回空表', () {
    final String catalog = File(kAsterismsAssetPath).readAsStringSync();
    expect(
      () => SkyAssets.fromBytes(
        starsBytes: Uint8List.fromList(<int>[1, 2, 3]),
        asterismsJson: catalog,
        license: 'x',
      ),
      throwsA(isA<SkyAssetException>()),
    );
  });

  test('坏星官目录也必须带上是哪份资产', () {
    final Uint8List stars = File(kStarsAssetPath).readAsBytesSync();
    try {
      SkyAssets.fromBytes(
        starsBytes: stars,
        asterismsJson: '{"regions": 1}',
        license: 'x',
      );
      fail('坏目录没有抛异常');
    } on SkyAssetException catch (e) {
      expect(e.asset, kAsterismsAssetPath);
      expect(e.reason, isNotEmpty);
    }
  });

  testWidgets('BundleSkyAssetSource 能读到打包资产（pubspec 已声明）', (WidgetTester tester) async {
    final SkyAssets assets = await const BundleSkyAssetSource().load();
    expect(assets.stars, isNotEmpty);
    expect(assets.catalog.asterisms, isNotEmpty);
  });
}
