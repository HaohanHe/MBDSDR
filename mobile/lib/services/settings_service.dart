import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/activity_log.dart';
import '../models/bookmark.dart';
import '../models/radio_state.dart';
import '../models/recording.dart';

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

/// 桌面 ControlHub HTTP 主机/端口（远程只读解码面板用）。
///
/// 与 rtl_tcp（裸 IQ TCP，本机接收链）相互独立：ControlHub 是桌面引擎把
/// POCSAG/M17/VOR 解码结果经 HTTP 暴露的只读通道。host 留空 = 未配置，远程
/// 解码面板退化为「去设置」空态，不假连。
///
/// Phase27：默认值与桌面 HttpControlServer 对齐——桌面端点仅绑定 127.0.0.1
/// 回环、默认端口 50732（tokens::kControlHttpDefaultPort）。移动端作为「本机
/// 桌面查看器」时出厂即指向本机回环，开箱可达；远程查看由用户改主机地址。
const String _kControlHubHost = 'controlHubHost';
const String _kControlHubPort = 'controlHubPort';

/// 桌面 ControlHub HTTP 默认主机（与桌面端回环绑定一致）。
const String kDefaultControlHubHost = '127.0.0.1';

/// 桌面 ControlHub HTTP 默认端口（对齐 tokens::kControlHttpDefaultPort = 50732）。
const int kDefaultControlHubPort = 50732;
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

/// 频点书签列表，JSON 数组字符串落盘。
///
/// 结构升级：旧版只存频率数字数组（如 `[144000000,145000000]`）；新版存对象数组
/// `[{name,frequencyHz,mode,bandwidthHz}, ...]`。load() 时对旧的纯数字数组做只读迁移：
/// 每个数字升级为 `Bookmark(name:'', frequencyHz:该值, mode:'', bandwidthHz:0)`。
/// 永不内置台名/位置，默认空列表。
const String _kBookmarksHz = 'bookmarksHz';

/// 录音元数据列表，JSON 数组字符串落盘（结构见 models/recording.dart）。
///
/// 诚实说明：移动端当前无真实文件录制路径，生产代码不会写入任何条目；
/// 这里只是就绪的持久化槽位，列表在真机上恒为空态。
const String _kRecordings = 'recordingsMeta';

/// 信号活动日志，JSON 数组字符串落盘（结构见 models/activity_log.dart）。
///
/// 与书签严格区分：书签=用户手工收藏；活动日志=系统自动追加的真实观察记录
/// （值守静噪门开门/扫描命中）。newest-first，超过 [kActivityLogMaxEntries]
/// 丢弃最旧，避免长会话撑爆存储。默认空，不内置任何假信号。
const String _kActivityLog = 'activityLog';

/// 活动日志最大保留条数（对齐桌面 ActivityLog::maxEntries 上限语义）。
const int kActivityLogMaxEntries = 200;

/// 频谱固定频率标记（Hz）列表，JSON 数字数组落盘。
///
/// 用户在频谱图上手动钉住的参考频点；默认空，不预存任何频点。
/// 仅存频率数字（无名称），画布以琥珀虚线竖线呈现。
const String _kFixedMarksHz = 'fixedMarksHz';

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
  String _controlHubHost = kDefaultControlHubHost;
  int _controlHubPort = kDefaultControlHubPort;
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

  /// 桌面 ControlHub HTTP 主机（已 trim）。空串 = 未配置远程解码查看。
  String get controlHubHost => _controlHubHost;
  set controlHubHost(String value) {
    _controlHubHost = value.trim();
    unawaited(_kv.setString(_kControlHubHost, _controlHubHost));
    notifyListeners();
  }

  /// 桌面 ControlHub HTTP 端口，合法范围 1–65535，越界抛 [FormatException]。
  int get controlHubPort => _controlHubPort;
  set controlHubPort(int value) {
    if (value < 1 || value > 65535) {
      throw FormatException('端口必须在 1–65535 之间，收到: $value');
    }
    _controlHubPort = value;
    unawaited(_kv.setInt(_kControlHubPort, _controlHubPort));
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

  // ------------------------------------------------ 频点书签（名称/频率/模式/带宽）
  List<Bookmark> _bookmarks = <Bookmark>[];

  /// 书签列表（按加入顺序，不可变视图）。默认空，不预存任何台。
  List<Bookmark> get bookmarks => List<Bookmark>.unmodifiable(_bookmarks);

  /// 便捷视图：仅频率列表（Hz），供需要纯频率的旧调用方/扫描使用。
  List<int> get bookmarksHz =>
      _bookmarks.map((b) => b.frequencyHz).toList(growable: false);

  void _persistBookmarks() {
    unawaited(_kv.setString(
      _kBookmarksHz,
      jsonEncode(_bookmarks.map((b) => b.toJson()).toList()),
    ));
  }

  /// 加入书签；同频率已存在则忽略（返回 false，不覆盖已有名称/模式）。
  /// 返回最终是否真正新增。frequencyHz<=0 一律拒绝。
  bool addBookmark({
    required String name,
    required int frequencyHz,
    String mode = '',
    int bandwidthHz = 0,
  }) {
    if (frequencyHz <= 0) return false;
    if (_bookmarks.any((b) => b.frequencyHz == frequencyHz)) return false;
    _bookmarks = List<Bookmark>.of(_bookmarks)
      ..add(Bookmark(
        name: name.trim(),
        frequencyHz: frequencyHz,
        mode: mode.trim().toLowerCase(),
        bandwidthHz: bandwidthHz,
      ));
    _persistBookmarks();
    notifyListeners();
    return true;
  }

  /// 兼容旧调用方：只按频率加入（无名/无模式/无带宽）。
  bool addBookmarkHz(int hz) =>
      addBookmark(name: '', frequencyHz: hz);

  /// 按频率移除书签。
  void removeBookmark(int frequencyHz) {
    if (!_bookmarks.any((b) => b.frequencyHz == frequencyHz)) return;
    _bookmarks = List<Bookmark>.of(_bookmarks)
      ..removeWhere((b) => b.frequencyHz == frequencyHz);
    _persistBookmarks();
    notifyListeners();
  }

  /// 兼容旧调用方。
  void removeBookmarkHz(int hz) => removeBookmark(hz);

  // ------------------------------------------------ 录音元数据索引
  List<RecordingMeta> _recordings = <RecordingMeta>[];

  /// 录音元数据列表（按开始时间倒序，不可变视图）。
  ///
  /// 真机上恒为空——移动端尚无真实文件录制；此列表为就绪的持久化索引。
  List<RecordingMeta> get recordings =>
      List<RecordingMeta>.unmodifiable(_recordings);

  void _persistRecordings() {
    unawaited(_kv.setString(
      _kRecordings,
      jsonEncode(_recordings.map((r) => r.toJson()).toList()),
    ));
  }

  /// 追加一条录音元数据（真实录制落地时由录制路径调用）。
  void addRecording(RecordingMeta meta) {
    _recordings = List<RecordingMeta>.of(_recordings)..add(meta);
    // 新录制在前。
    _recordings.sort((a, b) => b.startedAtEpochMs - a.startedAtEpochMs);
    _persistRecordings();
    notifyListeners();
  }

  /// 清空录音元数据索引。
  void clearRecordings() {
    if (_recordings.isEmpty) return;
    _recordings = <RecordingMeta>[];
    _persistRecordings();
    notifyListeners();
  }

  /// 删除单条录音元数据（按时刻+频率定位；未命中静默返回）。
  void removeRecording(RecordingMeta meta) {
    final before = _recordings.length;
    _recordings = List<RecordingMeta>.of(_recordings)
      ..removeWhere((r) =>
          r.startedAtEpochMs == meta.startedAtEpochMs &&
          r.frequencyHz == meta.frequencyHz);
    if (_recordings.length == before) return;
    _persistRecordings();
    notifyListeners();
  }

  // ------------------------------------------------ 信号活动日志（自动观察，与书签分离）
  List<ActivityEntry> _activities = <ActivityEntry>[];

  /// 信号活动日志（按观察时刻倒序，不可变视图）。默认空，不内置假信号。
  List<ActivityEntry> get activities =>
      List<ActivityEntry>.unmodifiable(_activities);

  void _persistActivities() {
    unawaited(_kv.setString(
      _kActivityLog,
      jsonEncode(_activities.map((a) => a.toJson()).toList()),
    ));
  }

  /// 追加一条真实观察记录（newest-first，超过上限丢最旧）。
  /// frequencyHz<=0 一律拒绝（不记录假频点）。返回是否真正新增。
  bool addActivity({
    required int frequencyHz,
    required double levelDbfs,
    String mode = '',
    String source = 'squelch',
    int? epochMsUtc,
  }) {
    if (frequencyHz <= 0) return false;
    final entry = ActivityEntry(
      epochMsUtc: epochMsUtc ?? DateTime.now().millisecondsSinceEpoch,
      frequencyHz: frequencyHz,
      levelDbfs: levelDbfs,
      mode: mode.trim().toLowerCase(),
      source: source.trim().isEmpty ? 'squelch' : source.trim(),
    );
    _activities = List<ActivityEntry>.of(_activities)..insert(0, entry);
    if (_activities.length > kActivityLogMaxEntries) {
      _activities = _activities
          .sublist(0, kActivityLogMaxEntries)
          .toList(growable: true);
    }
    _persistActivities();
    notifyListeners();
    return true;
  }

  /// 清空活动日志。
  void clearActivities() {
    if (_activities.isEmpty) return;
    _activities = <ActivityEntry>[];
    _persistActivities();
    notifyListeners();
  }

  // ------------------------------------------------ 频谱固定频率标记（Hz）
  List<double> _fixedMarks = <double>[];

  /// 固定频率标记列表（Hz，按加入顺序，不可变视图）。默认空，不预存任何频点。
  List<double> get fixedMarksHz => List<double>.unmodifiable(_fixedMarks);

  void _persistFixedMarks() {
    unawaited(_kv.setString(_kFixedMarksHz, jsonEncode(_fixedMarks)));
  }

  /// 钉住一个固定频率标记（Hz）；频率 <=0 拒绝，已存在则忽略（返回 false）。
  bool addFixedMarkHz(double hz) {
    if (hz <= 0) return false;
    if (_fixedMarks.any((f) => (f - hz).abs() < 1.0)) return false;
    _fixedMarks = List<double>.of(_fixedMarks)..add(hz);
    _persistFixedMarks();
    notifyListeners();
    return true;
  }

  /// 按频率移除固定标记（容差 1 Hz）。
  void removeFixedMarkHz(double hz) {
    final before = _fixedMarks.length;
    _fixedMarks = List<double>.of(_fixedMarks)
      ..removeWhere((f) => (f - hz).abs() < 1.0);
    if (_fixedMarks.length == before) return;
    _persistFixedMarks();
    notifyListeners();
  }

  /// 清空全部固定标记。
  void clearFixedMarks() {
    if (_fixedMarks.isEmpty) return;
    _fixedMarks = <double>[];
    _persistFixedMarks();
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
    _controlHubHost = _kv.getString(_kControlHubHost) ?? kDefaultControlHubHost;
    // 端口缺失/越界回退默认 50732（对齐桌面），绝不抛。
    final int? chPort = _kv.getInt(_kControlHubPort);
    _controlHubPort = (chPort == null || chPort < 1 || chPort > 65535)
        ? kDefaultControlHubPort
        : chPort;
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

    // 频点书签：兼容旧「纯频率数字数组」并迁移为完整 Bookmark 对象。
    // 解析失败/非法项静默丢弃，回退空列表，绝不抛异常。
    final String? rawBookmarks = _kv.getString(_kBookmarksHz);
    if (rawBookmarks != null && rawBookmarks.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawBookmarks);
        if (decoded is List) {
          _bookmarks = decoded.map((item) {
            // 旧格式：数字 → 迁移为无名书签。
            if (item is num) {
              final hz = item.toInt();
              return hz > 0
                  ? Bookmark(name: '', frequencyHz: hz)
                  : null;
            }
            // 新格式：对象。
            return Bookmark.fromJson(item);
          }).whereType<Bookmark>().toList();
        }
      } on FormatException {
        _bookmarks = <Bookmark>[];
      }
    }

    // 录音元数据：解析失败/空则回退空列表（真机本就为空）。
    final String? rawRecordings = _kv.getString(_kRecordings);
    if (rawRecordings != null && rawRecordings.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawRecordings);
        if (decoded is List) {
          _recordings = decoded
              .map(RecordingMeta.fromJson)
              .whereType<RecordingMeta>()
              .toList();
          _recordings
              .sort((a, b) => b.startedAtEpochMs - a.startedAtEpochMs);
        }
      } on FormatException {
        _recordings = <RecordingMeta>[];
      }
    }

    // 信号活动日志：JSON 对象数组；解析失败/非法项静默丢弃，回退空列表。
    // newest-first：load 后按观察时刻倒序兜底排序。
    final String? rawActivities = _kv.getString(_kActivityLog);
    if (rawActivities != null && rawActivities.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawActivities);
        if (decoded is List) {
          _activities = decoded
              .map(ActivityEntry.fromJson)
              .whereType<ActivityEntry>()
              .toList();
          _activities.sort((a, b) => b.epochMsUtc - a.epochMsUtc);
        }
      } on FormatException {
        _activities = <ActivityEntry>[];
      }
    }

    // 固定频率标记：JSON 数字数组；解析失败/非法项静默丢弃，回退空列表。
    final String? rawMarks = _kv.getString(_kFixedMarksHz);    if (rawMarks != null && rawMarks.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawMarks);
        if (decoded is List) {
          _fixedMarks = decoded
              .whereType<num>()
              .map((n) => n.toDouble())
              .where((f) => f > 0)
              .toList();
        }
      } on FormatException {
        _fixedMarks = <double>[];
      }
    }

    notifyListeners();
  }
}
