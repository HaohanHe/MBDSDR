import 'package:flutter_test/flutter_test.dart';
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
}
