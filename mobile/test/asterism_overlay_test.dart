/// 星官叠加层（`lib/astro/asterism_overlay.dart`）的行为测试。
///
/// 覆盖点
/// ------
/// * `stars.bin` 合成字节：魔数 / 计数 / 逐字段解码；坏字节必须抛，不静默
///   返回半份星表（格式口径与 `scripts/build_mobile_sky_assets.py` 逐字段对齐）；
/// * `asterisms.json`：区域、许可、折线必须是 int、单星星官；
/// * `SkyOverlayEngine.compute`：视图对准星官 → 线段端点落在画布内且成员全在
///   视场；视场装不下 / 转开 → 线段被裁到边界或整条不在视场，`membersInView` 下降。
///
/// 期望值全部由本文件用同一套公开函数现场推导（坐标链 `altAzFromJ2000`、
/// 投影 `projectAzAlt`），不写死像素常数，也不扫描源码文本。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/asterism_overlay.dart';
import 'package:mbdsdr_mobile/astro/sky_projection.dart';
import 'package:mbdsdr_mobile/astro/sky_transform.dart';

const double _jd = 2461355.25;
const SkySite _site = SkySite(latDeg: 45.0, lonDeg: 120.0);

/// 三颗合成恒星（J2000）：前两颗相距约 1°，第三颗与前两颗相距约 30°。
const List<SkyStar> _stars = <SkyStar>[
  SkyStar(raDeg: 100.0, decDeg: 20.0, vmag: 1.0, bv: 0.25, hip: 101),
  SkyStar(raDeg: 100.5, decDeg: 20.0, vmag: 2.0, bv: 0.75, hip: 102),
  SkyStar(raDeg: 130.0, decDeg: 20.0, vmag: 3.0, bv: -0.1, hip: 103),
];

/// 按 `scripts/build_mobile_sky_assets.py::_serialize_stars` 的格式拼字节：
/// 8 字节魔数 + uint32 计数（小端）+ 每条 `<ffffI`（20 字节，小端）。
Uint8List _encodeStars(List<SkyStar> stars, {int? declaredCount}) {
  final out = ByteData(12 + stars.length * kStarsRecordBytes);
  final bytes = out.buffer.asUint8List();
  bytes.setRange(0, 8, kStarsMagic.codeUnits);
  out.setUint32(8, declaredCount ?? stars.length, Endian.little);
  for (var i = 0; i < stars.length; i++) {
    final off = 12 + i * kStarsRecordBytes;
    out.setFloat32(off, stars[i].raDeg, Endian.little);
    out.setFloat32(off + 4, stars[i].decDeg, Endian.little);
    out.setFloat32(off + 8, stars[i].vmag, Endian.little);
    out.setFloat32(off + 12, stars[i].bv, Endian.little);
    out.setUint32(off + 16, stars[i].hip, Endian.little);
  }
  return bytes;
}

AsterismCatalog _catalog(List<int> hipLine) => AsterismCatalog(
      asterisms: <Asterism>[
        Asterism(
          name: '测试官',
          regionName: '紫微垣',
          polylines: <List<int>>[hipLine],
        ),
      ],
      regionNames: const <String, String>{'ziwei': '紫微垣'},
      singleStarNames: const <String>[],
      license: 'CC BY-SA 4.0（测试）',
    );

SkyOverlayEngine _engine(List<int> hipLine) => SkyOverlayEngine(
      stars: _stars,
      catalog: _catalog(hipLine),
    );

SkyView _viewAt(double azDeg, double altDeg, double fovDeg) => SkyView(
      centerAzDeg: azDeg,
      centerAltDeg: altDeg,
      fovDeg: fovDeg,
      width: 1080,
      height: 1920,
    );

AzAlt _altAzOf(int hip) {
  final star = _stars.firstWhere((s) => s.hip == hip);
  return altAzFromJ2000(
    raDeg: star.raDeg,
    decDeg: star.decDeg,
    latDeg: _site.latDeg,
    lonDeg: _site.lonDeg,
    jdUt1: _jd,
  );
}

void _expectInsideCanvas(OverlaySegment seg, SkyView view) {
  for (final p in <ScreenPoint>[seg.a, seg.b]) {
    expect(p.x, greaterThanOrEqualTo(-1e-6));
    expect(p.x, lessThanOrEqualTo(view.width + 1e-6));
    expect(p.y, greaterThanOrEqualTo(-1e-6));
    expect(p.y, lessThanOrEqualTo(view.height + 1e-6));
  }
}

bool _onEdge(ScreenPoint p, SkyView view) {
  const eps = 1e-6;
  return (p.x - 0).abs() < eps ||
      (p.x - view.width).abs() < eps ||
      (p.y - 0).abs() < eps ||
      (p.y - view.height).abs() < eps;
}

void main() {
  group('stars.bin 解析（合成字节）', () {
    test('逐字段解码：星数、HIP、ra/dec/vmag/bv 与写入值一致', () {
      final parsed = parseStarsBin(_encodeStars(_stars));

      expect(parsed, hasLength(_stars.length));
      for (var i = 0; i < _stars.length; i++) {
        final want = _stars[i];
        final got = parsed[i];
        expect(got.hip, want.hip);
        // float32 只能还原到单精度：数值按 1e-6 比对，不假装是双精度。
        expect(got.raDeg, closeTo(want.raDeg, 1e-6));
        expect(got.decDeg, closeTo(want.decDeg, 1e-6));
        expect(got.vmag, closeTo(want.vmag, 1e-6));
        expect(got.bv, closeTo(want.bv, 1e-6));
        expect(got.hasColor, isTrue);
      }
    });

    test('空星表是合法输入：0 条记录，不抛', () {
      expect(parseStarsBin(_encodeStars(const <SkyStar>[])), isEmpty);
    });

    test('魔数不符必抛，且报出实际魔数与期望值', () {
      final bytes = _encodeStars(_stars);
      bytes[0] = 'X'.codeUnitAt(0);

      expect(
        () => parseStarsBin(bytes),
        throwsA(isA<FormatException>().having(
          (e) => e.message,
          'message',
          allOf(contains('魔数'), contains(kStarsMagic)),
        )),
      );
    });

    test('文件头不足 12 字节必抛（不静默返回空表）', () {
      expect(
        () => parseStarsBin(Uint8List(11)),
        throwsA(isA<FormatException>().having(
          (e) => e.message,
          'message',
          contains('太短'),
        )),
      );
    });

    test('记录长度与计数不符必抛（截断/多条都要抓住）', () {
      final bytes = _encodeStars(_stars);
      final truncated = Uint8List.sublistView(bytes, 0, bytes.length - 1);
      expect(
        () => parseStarsBin(truncated),
        throwsA(isA<FormatException>().having(
          (e) => e.message,
          'message',
          contains('长度不符'),
        )),
      );

      // 头部声称 2 条，实际只有 1 条记录：同样必须抛。
      final oneRecord = _encodeStars(_stars.take(1).toList(), declaredCount: 2);
      expect(
        () => parseStarsBin(oneRecord),
        throwsA(isA<FormatException>().having(
          (e) => e.message,
          'message',
          contains('长度不符'),
        )),
      );
    });
  });

  group('asterisms.json 解析', () {
    final jsonText = jsonEncode(<String, dynamic>{
      'meta': <String, dynamic>{
        'schema': 'mbdsdr.mobile.xingguan.v1',
        'data_license': 'CC BY-SA 4.0',
      },
      'regions': <dynamic>[
        <String, dynamic>{
          'key': 'ziwei',
          'name': '紫微垣',
          'asterisms': <dynamic>[
            <String, dynamic>{
              'name': '北斗',
              'lines': <dynamic>[
                <int>[1, 2, 3],
              ],
            },
            <String, dynamic>{
              'name': '空官',
              'lines': <dynamic>[],
            },
          ],
          'single_star_asterisms': <dynamic>['北极'],
        },
        <String, dynamic>{
          'key': 'jiao',
          'name': '角宿',
          'asterisms': <dynamic>[
            <String, dynamic>{
              'name': '角',
              'lines': <dynamic>[
                <int>[4, 5],
                <int>[6, 7],
              ],
            },
          ],
        },
      ],
    });

    test('区域数、区域名、许可、单星星官都进目录', () {
      final catalog = parseAsterismsJson(jsonText);

      expect(catalog.license, 'CC BY-SA 4.0');
      expect(catalog.regionNames, <String, String>{'ziwei': '紫微垣', 'jiao': '角宿'});
      expect(catalog.singleStarNames, <String>['北极']);
      // 「空官」没有折线，画不出线，按实现跳过。
      expect(catalog.asterisms, hasLength(2));
      expect(
        catalog.asterisms.map((a) => a.name),
        <String>['北斗', '角'],
      );
    });

    test('折线元素是 int（不是字符串/浮点），星官归到所属星区', () {
      final catalog = parseAsterismsJson(jsonText);

      for (final asterism in catalog.asterisms) {
        for (final polyline in asterism.polylines) {
          expect(polyline, everyElement(isA<int>()));
        }
      }

      final beidou = catalog.asterisms.firstWhere((a) => a.name == '北斗');
      expect(beidou.regionName, '紫微垣');
      expect(beidou.polylines, <List<int>>[
        <int>[1, 2, 3],
      ]);
      expect(beidou.starCount, 3);

      final jiao = catalog.asterisms.firstWhere((a) => a.name == '角');
      expect(jiao.regionName, '角宿');
      expect(jiao.starCount, 4);
    });
  });

  group('SkyOverlayEngine.compute', () {
    test('视图对准星官：线段端点落在画布内，成员全部在视场', () {
      final aa1 = _altAzOf(101);
      final view = _viewAt(aa1.azDeg, aa1.altDeg, 20.0);
      final result = _engine(const <int>[101, 102]).compute(
        site: _site,
        jdUt1: _jd,
        view: view,
      );

      expect(result.jdUt1, _jd);
      expect(result.asterisms, hasLength(1));
      final presence = result.asterisms.single;
      expect(presence.name, '测试官');
      expect(presence.regionName, '紫微垣');
      expect(presence.membersTotal, 2);
      expect(presence.membersInView, 2);
      expect(presence.truncated, isFalse);
      expect(presence.coverage, 1.0);
      expect(presence.segments, hasLength(1));
      expect(result.segmentCount, 1);

      final seg = presence.segments.single;
      expect(seg.clipped, isFalse);
      _expectInsideCanvas(seg, view);

      // 线段两端就是两颗星各自的投影点（未被裁动）。
      final s101 = projectAzAlt(aa1.azDeg, aa1.altDeg, view)!;
      final aa2 = _altAzOf(102);
      final s102 = projectAzAlt(aa2.azDeg, aa2.altDeg, view)!;
      expect(seg.a.x, closeTo(s101.x, 1e-6));
      expect(seg.a.y, closeTo(s101.y, 1e-6));
      expect(seg.b.x, closeTo(s102.x, 1e-6));
      expect(seg.b.y, closeTo(s102.y, 1e-6));

      // 星点：两颗在画布内，HIP 与星表一一对应。
      expect(result.starScreenPoints, hasLength(2));
      expect(result.starHips, <int>[101, 102]);
      for (final p in result.starScreenPoints) {
        expect(isOnCanvas(p, view.width, view.height), isTrue);
      }
    });

    test('视场装不下：线段被裁到画布边界，membersInView 下降并标记截断', () {
      final aa1 = _altAzOf(101);
      final view = _viewAt(aa1.azDeg, aa1.altDeg, 10.0);
      final result = _engine(const <int>[101, 103]).compute(
        site: _site,
        jdUt1: _jd,
        view: view,
      );

      expect(result.asterisms, hasLength(1));
      final presence = result.asterisms.single;
      expect(presence.membersTotal, 2);
      expect(presence.membersInView, 1);
      expect(presence.truncated, isTrue);
      expect(presence.coverage, 0.5);
      expect(presence.segments, hasLength(1));

      final seg = presence.segments.single;
      expect(seg.clipped, isTrue);
      _expectInsideCanvas(seg, view);

      // 靠近视中心的一端是 101 的真实投影；另一端被压在画布边界上。
      final s101 = projectAzAlt(aa1.azDeg, aa1.altDeg, view)!;
      expect(seg.a.x, closeTo(s101.x, 1e-6));
      expect(seg.a.y, closeTo(s101.y, 1e-6));
      // 103 的方位角更小（更靠西），投影落在视中心左侧，所以裁到左边界。
      expect(_onEdge(seg.b, view), isTrue);
      expect(seg.b.x, lessThan(seg.a.x));
    });

    test('视图转开：线段与星点都不在视场，星官不出现在结果里', () {
      final aa1 = _altAzOf(101);
      final view = _viewAt(
        normalizeDeg(aa1.azDeg + 180.0),
        -aa1.altDeg,
        60.0,
      );
      final result = _engine(const <int>[101, 102]).compute(
        site: _site,
        jdUt1: _jd,
        view: view,
      );

      expect(result.hasAnything, isFalse);
      expect(result.asterisms, isEmpty);
      expect(result.segmentCount, 0);
      expect(result.starScreenPoints, isEmpty);
      expect(result.starHips, isEmpty);
    });

    test('星表里查不到的 HIP 不编坐标：该点不参与连线', () {
      final aa1 = _altAzOf(101);
      final view = _viewAt(aa1.azDeg, aa1.altDeg, 20.0);
      // 999 不在星表里：按实现取「永远在地平下」的哨兵值，整段跳过。
      final result = _engine(const <int>[101, 999]).compute(
        site: _site,
        jdUt1: _jd,
        view: view,
      );

      expect(result.asterisms, isEmpty);
      expect(result.segmentCount, 0);
    });
  });
}
