import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/settings_service.dart';

/// 测试用内存 KV 存储。
class InMemoryKvStore implements KvStore {
  final Map<String, Object?> data = <String, Object?>{};

  @override
  String? getString(String key) => data[key] as String?;

  @override
  Future<void> setString(String key, String value) async {
    data[key] = value;
  }

  @override
  double? getDouble(String key) => data[key] as double?;

  @override
  Future<void> setDouble(String key, double value) async {
    data[key] = value;
  }

  @override
  int? getInt(String key) => data[key] as int?;

  @override
  Future<void> setInt(String key, int value) async {
    data[key] = value;
  }

  @override
  bool? getBool(String key) => data[key] as bool?;

  @override
  Future<void> setBool(String key, bool value) async {
    data[key] = value;
  }

  @override
  Future<void> remove(String key) async {
    data.remove(key);
  }
}

/// 测试用内存安全存储。
class InMemorySecureStore implements SecureStore {
  final Map<String, String> data = <String, String>{};

  @override
  Future<String?> get(String key) async => data[key];

  @override
  Future<void> set(String key, String value) async {
    data[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    data.remove(key);
  }
}

void main() {
  test('空存储下 load() 读回全部默认值', () async {
    final SettingsService s = SettingsService(
      kv: InMemoryKvStore(),
      secure: InMemorySecureStore(),
    );
    await s.load();
    expect(s.rtlHost, '');
    expect(s.rtlPort, 1234);
    expect(s.apiKey, '');
    expect(s.apiModel, 'Qwen/Qwen2.5-7B-Instruct');
    expect(s.stationLat, isNull);
    expect(s.stationLon, isNull);
    expect(s.stationAlt, isNull);
    expect(s.themeName, '默认');
    expect(s.hasManualStation, isFalse);
    expect(s.lastFreqHz, 144000000);
    expect(s.demodMode, 'nfm');
    expect(s.demodModeEnum, DemodMode.nfm);
    expect(s.volume, 1.0);
    expect(s.muted, isFalse);
  });

  test('各字段保存后可重新 load 往返', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    final InMemorySecureStore secure = InMemorySecureStore();
    final SettingsService s = SettingsService(kv: kv, secure: secure);
    await s.load();

    s.rtlHost = '  192.168.1.10  ';
    s.rtlPort = 8080;
    s.apiModel = 'deepseek-chat';
    s.apiKey = 'sk-abcdef123456';
    s.stationLat = 43.88;
    s.stationLon = 125.32;
    s.stationAlt = 230.5;

    // host 落盘前已 trim
    expect(kv.data['rtlHost'], '192.168.1.10');
    expect(kv.data['rtlPort'], 8080);
    expect(kv.data['apiModel'], 'deepseek-chat');
    expect(kv.data['stationLat'], 43.88);
    expect(kv.data['stationLon'], 125.32);
    expect(kv.data['stationAlt'], 230.5);

    final SettingsService s2 = SettingsService(kv: kv, secure: secure);
    await s2.load();
    expect(s2.rtlHost, '192.168.1.10');
    expect(s2.rtlPort, 8080);
    expect(s2.apiModel, 'deepseek-chat');
    expect(s2.apiKey, 'sk-abcdef123456');
    expect(s2.stationLat, 43.88);
    expect(s2.stationLon, 125.32);
    expect(s2.stationAlt, 230.5);
    expect(s2.hasManualStation, isTrue);
  });

  test('apiKey 只进安全存储，普通 KV 中不出现该键', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    final InMemorySecureStore secure = InMemorySecureStore();
    final SettingsService s = SettingsService(kv: kv, secure: secure);
    await s.load();

    s.apiKey = 'sk-secret-987654321';
    expect(secure.data['apiKey'], 'sk-secret-987654321');
    expect(kv.data.containsKey('apiKey'), isFalse);

    // 清空时安全存储也一并删除
    s.apiKey = '';
    expect(secure.data.containsKey('apiKey'), isFalse);
    expect(kv.data.containsKey('apiKey'), isFalse);
  });

  test('非法端口抛 FormatException，合法端口正常', () {
    final SettingsService s = SettingsService(
      kv: InMemoryKvStore(),
      secure: InMemorySecureStore(),
    );
    expect(() => s.rtlPort = 0, throwsFormatException);
    expect(() => s.rtlPort = 65536, throwsFormatException);
    expect(() => s.rtlPort = 70000, throwsFormatException);
    s.rtlPort = 1;
    expect(s.rtlPort, 1);
    s.rtlPort = 65535;
    expect(s.rtlPort, 65535);
  });

  test('清空坐标后存储中对应键被移除', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    final SettingsService s = SettingsService(
      kv: kv,
      secure: InMemorySecureStore(),
    );
    await s.load();
    s.stationLat = 1.0;
    s.stationLon = 2.0;
    expect(kv.data.containsKey('stationLat'), isTrue);
    s.stationLat = null;
    expect(kv.data.containsKey('stationLat'), isFalse);
    expect(s.stationLat, isNull);
    expect(s.hasManualStation, isFalse);
  });

  test('音频/解调/频率四字段 round-trip：写入后新实例 load 读回一致', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    final InMemorySecureStore secure = InMemorySecureStore();
    final SettingsService s = SettingsService(kv: kv, secure: secure);
    await s.load();

    s.lastFreqHz = 1090000000;
    s.demodMode = 'wfm';
    s.volume = 0.6;
    s.muted = true;

    // 直接落盘到普通 KV（不进安全存储）。
    expect(kv.data['lastFreqHz'], 1090000000);
    expect(kv.data['demodMode'], 'wfm');
    expect(kv.data['volume'], 0.6);
    expect(kv.data['muted'], true);
    expect(secure.data.containsKey('lastFreqHz'), isFalse);
    expect(secure.data.containsKey('demodMode'), isFalse);
    expect(secure.data.containsKey('volume'), isFalse);
    expect(secure.data.containsKey('muted'), isFalse);

    final SettingsService s2 = SettingsService(kv: kv, secure: secure);
    await s2.load();
    expect(s2.lastFreqHz, 1090000000);
    expect(s2.demodMode, 'wfm');
    expect(s2.demodModeEnum, DemodMode.wfm);
    expect(s2.volume, closeTo(0.6, 1e-9));
    expect(s2.muted, isTrue);
  });

  test('volume setter 与 load 都把值 clamp 到 [0,1]', () {
    final SettingsService s = SettingsService(
      kv: InMemoryKvStore(),
      secure: InMemorySecureStore(),
    );
    s.volume = 1.7;
    expect(s.volume, 1.0);
    s.volume = -0.3;
    expect(s.volume, 0.0);
    s.volume = 0.25;
    expect(s.volume, 0.25);
  });

  test('load 时 KV 中越界的 volume 被 clamp 回 [0,1]', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    kv.data['volume'] = 2.5;
    final SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
    await s.load();
    expect(s.volume, 1.0);

    kv.data['volume'] = -1.0;
    final SettingsService s2 = SettingsService(kv: kv, secure: InMemorySecureStore());
    await s2.load();
    expect(s2.volume, 0.0);
  });

  test('未知 demodMode 字符串回退为 nfm，wfm 正常识别', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    kv.data['demodMode'] = 'am'; // 未知写法
    SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
    await s.load();
    expect(s.demodMode, 'nfm');
    expect(s.demodModeEnum, DemodMode.nfm);

    kv.data['demodMode'] = 'WFM'; // 大小写容忍
    s = SettingsService(kv: kv, secure: InMemorySecureStore());
    await s.load();
    expect(s.demodMode, 'wfm');
    expect(s.demodModeEnum, DemodMode.wfm);
  });

  test('demodMode setter 对未知写法归一为 nfm 而不是落盘脏值', () {
    final SettingsService s = SettingsService(
      kv: InMemoryKvStore(),
      secure: InMemorySecureStore(),
    );
    s.demodMode = 'ssb';
    expect(s.demodMode, 'nfm');
    expect(s.demodModeEnum, DemodMode.nfm);
    s.demodMode = ' wfm ';
    expect(s.demodMode, 'wfm');
  });

  test('lastFreqHz 缺失或非正数时回退默认 144 MHz', () async {
    final InMemoryKvStore kv = InMemoryKvStore();
    kv.data['lastFreqHz'] = -1;
    final SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
    await s.load();
    expect(s.lastFreqHz, 144000000);
  });

  group('频点书签（名称/频率/模式/带宽）', () {
    test('默认空列表，不预存任何台', () async {
      final SettingsService s = SettingsService(
        kv: InMemoryKvStore(),
        secure: InMemorySecureStore(),
      );
      await s.load();
      expect(s.bookmarks, isEmpty);
      expect(s.bookmarksHz, isEmpty);
    });

    test('addBookmark 完整结构 round-trip：名称/频率/模式/带宽都读回', () async {
      final InMemoryKvStore kv = InMemoryKvStore();
      final SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
      await s.load();

      s.addBookmark(
        name: '测试台',
        frequencyHz: 146520000,
        mode: 'wfm',
        bandwidthHz: 200000,
      );
      expect(s.bookmarks, hasLength(1));
      final b = s.bookmarks.single;
      expect(b.name, '测试台');
      expect(b.frequencyHz, 146520000);
      expect(b.mode, 'wfm');
      expect(b.bandwidthHz, 200000);
      expect(b.modeEnum, DemodMode.wfm);

      // 落盘是对象数组（不是纯数字）。
      final raw = kv.data['bookmarksHz'] as String;
      expect(raw, contains('"name"'));
      expect(raw, contains('"frequencyHz"'));

      final SettingsService s2 = SettingsService(kv: kv, secure: InMemorySecureStore());
      await s2.load();
      expect(s2.bookmarks.single.name, '测试台');
      expect(s2.bookmarks.single.bandwidthHz, 200000);
    });

    test('同频率去重，removeBookmark 按频率删除', () async {
      final SettingsService s = SettingsService(
        kv: InMemoryKvStore(),
        secure: InMemorySecureStore(),
      );
      await s.load();
      expect(s.addBookmark(name: 'A', frequencyHz: 144000000), isTrue);
      // 同频率再次加入被忽略（不覆盖）。
      expect(s.addBookmark(name: 'B', frequencyHz: 144000000), isFalse);
      expect(s.bookmarks, hasLength(1));
      expect(s.bookmarks.single.name, 'A');

      s.removeBookmark(144000000);
      expect(s.bookmarks, isEmpty);
    });

    test('旧纯数字频率数组被只读迁移为无名书签', () async {
      final InMemoryKvStore kv = InMemoryKvStore();
      // 旧版只存频率数字数组。
      kv.data['bookmarksHz'] = '[144000000, 145000000]';
      final SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
      await s.load();
      expect(s.bookmarks, hasLength(2));
      expect(s.bookmarks[0].frequencyHz, 144000000);
      expect(s.bookmarks[1].frequencyHz, 145000000);
      // 迁移后名称/模式/带宽为空。
      expect(s.bookmarks.every((b) => b.name.isEmpty), isTrue);
      expect(s.bookmarks.every((b) => b.mode.isEmpty), isTrue);
      expect(s.bookmarks.every((b) => b.bandwidthHz == 0), isTrue);
      // bookmarksHz 便捷视图仍给纯频率。
      expect(s.bookmarksHz, <int>[144000000, 145000000]);
    });

    test('新对象格式解析 + 非法项静默丢弃', () async {
      final InMemoryKvStore kv = InMemoryKvStore();
      kv.data['bookmarksHz'] = jsonEncode(<Object?>[
        <String, Object?>{
          'name': 'X',
          'frequencyHz': 98500000,
          'mode': 'nfm',
          'bandwidthHz': 12500,
        },
        'not-an-object',
        <String, Object?>{'name': 'bad', 'frequencyHz': -5},
      ]);
      final SettingsService s = SettingsService(kv: kv, secure: InMemorySecureStore());
      await s.load();
      // 非法两项被丢弃，只剩合法的一个。
      expect(s.bookmarks, hasLength(1));
      expect(s.bookmarks.single.frequencyHz, 98500000);
      expect(s.bookmarks.single.modeEnum, DemodMode.nfm);
    });

    test('兼容旧 addBookmarkHz / removeBookmarkHz', () async {
      final SettingsService s = SettingsService(
        kv: InMemoryKvStore(),
        secure: InMemorySecureStore(),
      );
      await s.load();
      s.addBookmarkHz(146000000);
      expect(s.bookmarks.single.frequencyHz, 146000000);
      expect(s.bookmarks.single.name, isEmpty);
      s.removeBookmarkHz(146000000);
      expect(s.bookmarks, isEmpty);
    });
  });
}
