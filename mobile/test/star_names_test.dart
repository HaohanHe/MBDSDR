/// 星名目录与准星中心识别文案（纯函数）。
///
/// 覆盖点
/// ------
/// * 解析 `star_names.json`：中文名/西名/别名/星官/星区/星座；
/// * 顶层或条目结构错误必须抛 FormatException（名字对不上号比没名字更危险）；
/// * HIP 非数字/≤0 的条目跳过，不拖垮整份目录；
/// * 中心文案：命中中文名+西名、未收录名字只报 HIP、附近无星、星名不可用；
/// * 角距保留 1 位小数——它本身就是「指得准不准」的读数。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/star_names.dart';

void main() {
  group('星名目录解析', () {
    test('解析中文名/西名/别名/星官/星区/星座', () {
      final Map<int, StarNameEntry> names = parseStarNamesJson(
        '{"91262":{"zh":"织女一","alt":["织女"],"en":"Vega",'
        '"asterisms":["织女"],"regions":["牛宿"],"con":"Lyr"}}',
      );
      final StarNameEntry e = names[91262]!;
      expect(e.zh, '织女一');
      expect(e.en, 'Vega');
      expect(e.alt, <String>['织女']);
      expect(e.asterisms, <String>['织女']);
      expect(e.regions, <String>['牛宿']);
      expect(e.con, 'Lyr');
      expect(e.hasName, isTrue);
      expect(e.primary, '织女一');
      expect(e.secondary, 'Vega');
    });

    test('顶层不是对象 → FormatException', () {
      expect(() => parseStarNamesJson('[]'), throwsFormatException);
      expect(() => parseStarNamesJson('"x"'), throwsFormatException);
    });

    test('条目不是对象 → FormatException（指名 HIP）', () {
      expect(
        () => parseStarNamesJson('{"1":2}'),
        throwsA(predicate((Object e) =>
            e is FormatException && e.message.contains('HIP 1'))),
      );
    });

    test('字段类型错误 → FormatException，不静默丢', () {
      expect(
        () => parseStarNamesJson('{"1":{"zh":1}}'),
        throwsFormatException,
      );
      expect(
        () => parseStarNamesJson('{"1":{"asterisms":"织女"}}'),
        throwsFormatException,
      );
      expect(
        () => parseStarNamesJson('{"1":{"alt":[1]}}'),
        throwsFormatException,
      );
    });

    test('HIP 非数字 / ≤0 的条目跳过，其余照常', () {
      final Map<int, StarNameEntry> names = parseStarNamesJson(
        '{"0":{"zh":"零点"},"abc":{"zh":"非号"},"-3":{"zh":"负数"},'
        '"1":{"zh":"甲"}}',
      );
      expect(names.keys, <int>[1]);
      expect(names[1]!.zh, '甲');
    });

    test('中文缺失时 primary 退回西名；两者都缺 hasName=false', () {
      final Map<int, StarNameEntry> names = parseStarNamesJson(
        '{"1":{"en":"Sirius"},"2":{}}',
      );
      expect(names[1]!.primary, 'Sirius');
      expect(names[1]!.secondary, isNull, reason: '主名就是西名时不再重复括号');
      expect(names[2]!.hasName, isFalse);
    });

    test('secondary 只在西名与主名不同时出现', () {
      const StarNameEntry zhSameAsEn =
          StarNameEntry(zh: 'Vega', en: 'Vega');
      expect(zhSameAsEn.secondary, isNull);
      const StarNameEntry zhOnly = StarNameEntry(zh: '织女一');
      expect(zhOnly.secondary, isNull);
    });
  });

  group('准星中心文案', () {
    test('命中中文名 + 西名：名字（西名）· 距中心 N.N°', () {
      final String text = centerStarText(
        namesAvailable: true,
        identification: const CenterStarIdentification(
          hip: 91262,
          vmag: 0.03,
          separationDeg: 0.42,
          name: StarNameEntry(zh: '织女一', en: 'Vega'),
        ),
      );
      expect(text, '织女一（Vega） · 距中心 0.4°');
    });

    test('未收录名字：只报 HIP/星等/角距，绝不编名', () {
      final String text = centerStarText(
        namesAvailable: true,
        identification: const CenterStarIdentification(
          hip: 12345,
          vmag: 4.6,
          separationDeg: 1.26,
        ),
      );
      expect(text, 'HIP 12345 · 距中心 1.3° · 4.6等');
      expect(text.contains('星'), isFalse);
    });

    test('中心附近无星：明说「附近无亮星」', () {
      expect(
        centerStarText(namesAvailable: true, identification: null),
        '中心附近无亮星',
      );
    });

    test('星名不可用：先说明不可用，不把无名字说成没有星', () {
      expect(
        centerStarText(namesAvailable: false, identification: null),
        '星名数据不可用，无法识别中心星',
      );
    });

    test('角距 2.4° 不四舍五入成 0°', () {
      final String text = centerStarText(
        namesAvailable: true,
        identification: const CenterStarIdentification(
          hip: 1,
          vmag: 1,
          separationDeg: 2.44,
          name: StarNameEntry(zh: '测试星'),
        ),
      );
      expect(text, '测试星 · 距中心 2.4°');
    });
  });
}
