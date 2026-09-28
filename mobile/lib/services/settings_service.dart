import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ============================================================================
// 设置持久化
// ----------------------------------------------------------------------------
// 分层：
//   * KvStore       —— 普通键值存储（SharedPreferences），存主机/端口/模型/坐标；
//   * SecureStore   —— 系统安全存储（Keystore/Keychain），只放 API key；
//   * SettingsService —— ChangeNotifier，setter 即落盘并 notifyListeners。
// 测试用 InMemoryKvStore / InMemorySecureStore 注入，不触碰真实存储。
// ============================================================================

/// 普通键值存储缝（同步读、异步写）。
abstract interface class KvStore {
  String? getString(String key);

  Future<void> setString(String key, String value);

  double? getDouble(String key);

  Future<void> setDouble(String key, double value);

  int? getInt(String key);

  Future<void> setInt(String key, int value);

  Future<void> remove(String key);
}

/// 安全存储缝（读/写/删全部异步）。
abstract interface class SecureStore {
  Future<String?> get(String key);

  Future<void> set(String key, String value);

  Future<void> delete(String key);
}

/// SharedPreferences 上的 KvStore 实现。
class SharedPreferencesKvStore implements KvStore {
  SharedPreferencesKvStore(this._prefs);

  final SharedPreferences _prefs;

  @override
  String? getString(String key) => _prefs.getString(key);

  @override
  Future<void> setString(String key, String value) async {
    await _prefs.setString(key, value);
  }

  @override
  double? getDouble(String key) => _prefs.getDouble(key);

  @override
  Future<void> setDouble(String key, double value) async {
    await _prefs.setDouble(key, value);
  }

  @override
  int? getInt(String key) => _prefs.getInt(key);

  @override
  Future<void> setInt(String key, int value) async {
    await _prefs.setInt(key, value);
  }

  @override
  Future<void> remove(String key) async {
    await _prefs.remove(key);
  }
}

/// flutter_secure_storage 上的 SecureStore 实现。
class FlutterSecureStorageStore implements SecureStore {
  FlutterSecureStorageStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  @override
  Future<String?> get(String key) => _storage.read(key: key);

  @override
  Future<void> set(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

// ---------------------------------------------------------------- 键名与默认值
const String kDefaultApiModel = 'Qwen/Qwen2.5-7B-Instruct';
const String kDefaultThemeName = '默认';

const String _kRtlHost = 'rtlHost';
const String _kRtlPort = 'rtlPort';
const String _kApiKey = 'apiKey';
const String _kApiModel = 'apiModel';
const String _kStationLat = 'stationLat';
const String _kStationLon = 'stationLon';
const String _kStationAlt = 'stationAlt';

/// 应用设置：读写即持久化，UI 通过 ChangeNotifier 订阅。
class SettingsService extends ChangeNotifier {
  SettingsService({required KvStore kv, required SecureStore secure})
      : _kv = kv,
        _secure = secure;

  final KvStore _kv;
  final SecureStore _secure;

  String _rtlHost = '';
  int _rtlPort = 1234;
  String _apiKey = '';
  String _apiModel = kDefaultApiModel;
  double? _stationLat;
  double? _stationLon;
  double? _stationAlt;

  /// rtl_tcp 主机（已 trim）。
  String get rtlHost => _rtlHost;
  set rtlHost(String value) {
    _rtlHost = value.trim();
    unawaited(_kv.setString(_kRtlHost, _rtlHost));
    notifyListeners();
  }

  /// rtl_tcp 端口，合法范围 1–65535，越界抛 [FormatException]。
  int get rtlPort => _rtlPort;
  set rtlPort(int value) {
    if (value < 1 || value > 65535) {
      throw FormatException('端口必须在 1–65535 之间，收到: $value');
    }
    _rtlPort = value;
    unawaited(_kv.setInt(_kRtlPort, value));
    notifyListeners();
  }

  /// AI API key：只写安全存储，绝不进普通 KV。
  String get apiKey => _apiKey;
  set apiKey(String value) {
    _apiKey = value.trim();
    if (_apiKey.isEmpty) {
      unawaited(_secure.delete(_kApiKey));
    } else {
      unawaited(_secure.set(_kApiKey, _apiKey));
    }
    notifyListeners();
  }

  /// AI 模型名（云端推理模型 ID）。
  String get apiModel => _apiModel;
  set apiModel(String value) {
    _apiModel = value.trim();
    unawaited(_kv.setString(_kApiModel, _apiModel));
    notifyListeners();
  }

  /// 本站纬度（度），null 表示未设置。
  double? get stationLat => _stationLat;
  set stationLat(double? value) {
    _stationLat = value;
    if (value == null) {
      unawaited(_kv.remove(_kStationLat));
    } else {
      unawaited(_kv.setDouble(_kStationLat, value));
    }
    notifyListeners();
  }

  /// 本站经度（度），null 表示未设置。
  double? get stationLon => _stationLon;
  set stationLon(double? value) {
    _stationLon = value;
    if (value == null) {
      unawaited(_kv.remove(_kStationLon));
    } else {
      unawaited(_kv.setDouble(_kStationLon, value));
    }
    notifyListeners();
  }

  /// 本站海拔（米），null 表示未设置。
  double? get stationAlt => _stationAlt;
  set stationAlt(double? value) {
    _stationAlt = value;
    if (value == null) {
      unawaited(_kv.remove(_kStationAlt));
    } else {
      unawaited(_kv.setDouble(_kStationAlt, value));
    }
    notifyListeners();
  }

  /// 外观主题名；当前唯一可选即「默认」（深色）。
  String get themeName => kDefaultThemeName;

  /// 三坐标是否齐全（用于决定是否把手动站点交给天空页）。
  bool get hasManualStation =>
      _stationLat != null && _stationLon != null && _stationAlt != null;

  /// 启动时从存储中读回全部设置。
  Future<void> load() async {
    _rtlHost = _kv.getString(_kRtlHost) ?? '';
    _rtlPort = _kv.getInt(_kRtlPort) ?? 1234;
    _apiModel = _kv.getString(_kApiModel) ?? kDefaultApiModel;
    _stationLat = _kv.getDouble(_kStationLat);
    _stationLon = _kv.getDouble(_kStationLon);
    _stationAlt = _kv.getDouble(_kStationAlt);
    _apiKey = await _secure.get(_kApiKey) ?? '';
    notifyListeners();
  }
}
