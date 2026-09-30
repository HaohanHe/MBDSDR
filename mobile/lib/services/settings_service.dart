import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/radio_state.dart';

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

  bool? getBool(String key);

  Future<void> setBool(String key, bool value);

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
  bool? getBool(String key) => _prefs.getBool(key);

  @override
  Future<void> setBool(String key, bool value) async {
    await _prefs.setBool(key, value);
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
const String _kLastFreqHz = 'lastFreqHz';
const String _kDemodMode = 'demodMode';
const String _kVolume = 'volume';
const String _kMuted = 'muted';

/// AI 助手运行模式：是否处于「手动模式」。
///   * false（默认）= AI 接管：AI 的调谐/模式/增益等动作真正执行；
///   * true        = 手动：AI 仍可对话，但工具动作仅记录不执行。
const String _kAiManualMode = 'aiManualMode';

/// 收藏频率列表（Hz），JSON 数组字符串落盘。只存真实频率，不存台名/位置。
const String _kBookmarksHz = 'bookmarksHz';

/// 上次调谐频率（Hz）默认值：144 MHz（2 m 业余段）。
const int kDefaultLastFreqHz = 144000000;

/// 解调模式持久化字符串合法取值。
const String _kDemodNfm = 'nfm';
const String _kDemodWfm = 'wfm';

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
  int _lastFreqHz = kDefaultLastFreqHz;
  String _demodMode = _kDemodNfm;
  double _volume = 1.0;
  bool _muted = false;
  bool _aiManualMode = false;

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

  /// 上次调谐频率（Hz）。
  int get lastFreqHz => _lastFreqHz;
  set lastFreqHz(int value) {
    _lastFreqHz = value;
    unawaited(_kv.setInt(_kLastFreqHz, value));
    notifyListeners();
  }

  /// 解调模式持久化字符串（'nfm' / 'wfm'）。
  ///
  /// 未知写法在落盘前归一为 'nfm'，不抛异常。
  String get demodMode => _demodMode;
  set demodMode(String value) {
    final String v = value.trim().toLowerCase();
    _demodMode = v == _kDemodWfm ? _kDemodWfm : _kDemodNfm;
    unawaited(_kv.setString(_kDemodMode, _demodMode));
    notifyListeners();
  }

  /// 解调模式枚举便捷视图（始终合法）。
  DemodMode get demodModeEnum =>
      _demodMode == _kDemodWfm ? DemodMode.wfm : DemodMode.nfm;

  /// 音量，范围 [0,1]，越界在 setter 内 clamp。
  double get volume => _volume;
  set volume(double value) {
    _volume = value.clamp(0.0, 1.0);
    unawaited(_kv.setDouble(_kVolume, _volume));
    notifyListeners();
  }

  /// 是否静音。
  bool get muted => _muted;
  set muted(bool value) {
    _muted = value;
    unawaited(_kv.setBool(_kMuted, value));
    notifyListeners();
  }

  /// AI 助手是否处于「手动模式」。
  ///
  /// false = AI 接管（工具动作真正执行）；true = 手动（工具仅记录不执行，
  /// 在对话流里以「手动模式：未执行」标注）。读写即持久化。
  bool get aiManualMode => _aiManualMode;
  set aiManualMode(bool value) {
    _aiManualMode = value;
    unawaited(_kv.setBool(_kAiManualMode, value));
    notifyListeners();
  }

  // ------------------------------------------------ 收藏频率（真实频率）
  List<int> _bookmarksHz = <int>[];

  /// 收藏的频率列表（Hz），按加入顺序。仅频率数值，无台名/位置。
  List<int> get bookmarksHz => List<int>.unmodifiable(_bookmarksHz);

  void _persistBookmarks() {
    unawaited(_kv.setString(_kBookmarksHz, jsonEncode(_bookmarksHz)));
  }

  /// 加入收藏；已存在则忽略。返回最终是否包含该频率。
  bool addBookmarkHz(int hz) {
    if (hz <= 0) return false;
    if (_bookmarksHz.contains(hz)) return true;
    _bookmarksHz = List<int>.of(_bookmarksHz)..add(hz);
    _persistBookmarks();
    notifyListeners();
    return true;
  }

  /// 移除收藏。
  void removeBookmarkHz(int hz) {
    if (!_bookmarksHz.contains(hz)) return;
    _bookmarksHz = List<int>.of(_bookmarksHz)..remove(hz);
    _persistBookmarks();
    notifyListeners();
  }

  /// 三坐标是否齐全（用于决定是否把手动站点交给天空页）。
  bool get hasManualStation =>
      _stationLat != null && _stationLon != null && _stationAlt != null;

  /// 启动时从存储中读回全部设置。
  ///
  /// 非法/越界/未知写法一律回退默认值，绝不抛异常导致启动崩溃。
  Future<void> load() async {
    _rtlHost = _kv.getString(_kRtlHost) ?? '';
    _rtlPort = _kv.getInt(_kRtlPort) ?? 1234;
    _apiModel = _kv.getString(_kApiModel) ?? kDefaultApiModel;
    _stationLat = _kv.getDouble(_kStationLat);
    _stationLon = _kv.getDouble(_kStationLon);
    _stationAlt = _kv.getDouble(_kStationAlt);
    _apiKey = await _secure.get(_kApiKey) ?? '';

    // 频率：缺失或非正数时回退默认。
    final int? freq = _kv.getInt(_kLastFreqHz);
    _lastFreqHz = (freq == null || freq <= 0) ? kDefaultLastFreqHz : freq;

    // 解调模式：仅接受 nfm/wfm，其余（含拼写错误）回退 nfm。
    final String? mode = _kv.getString(_kDemodMode)?.trim().toLowerCase();
    _demodMode = (mode == _kDemodWfm) ? _kDemodWfm : _kDemodNfm;

    // 音量：缺失回退 1.0，越界 clamp 到 [0,1]。
    final double? vol = _kv.getDouble(_kVolume);
    _volume = (vol ?? 1.0).clamp(0.0, 1.0);

    _muted = _kv.getBool(_kMuted) ?? false;

    // AI 模式：缺失回退 false（AI 接管）。
    _aiManualMode = _kv.getBool(_kAiManualMode) ?? false;

    // 收藏频率：JSON 数组解析失败/非法值一律回退空列表，不抛异常。
    final String? rawBookmarks = _kv.getString(_kBookmarksHz);
    if (rawBookmarks != null && rawBookmarks.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawBookmarks);
        if (decoded is List) {
          _bookmarksHz = decoded
              .whereType<num>()
              .map((n) => n.toInt())
              .where((h) => h > 0)
              .toList();
        }
      } on FormatException {
        _bookmarksHz = <int>[];
      }
    }

    notifyListeners();
  }
}
