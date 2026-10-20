// W2d 真链路闭环：断开诚实性 + predict_passes 确定性 + 工具总数审计。
//
// 红线（_PHASE3_SPEC §3 / A4 §4-P0）：
//   * 断开时 4 个动作工具不得回 ok:true，且不得真正调用 RadioApi；
//   * predict_passes 用固定 TLE/固定时间注入，禁真网、禁编造过境；
//   * buildRadioTools().length 必须等于登记工具数，未来漏注册即失败；
//   * tool-count-sync：目录已接入集合必须等于 buildRadioTools 名 ∩ 桌面目录名。
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/ai_tools.dart';
import 'package:mbdsdr_mobile/app/tool_catalog.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/bookmark.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/sat_passes_provider.dart';

/// 记录写调用的内存 FakeRadioApi。
class RecordingRadio implements RadioApi {
  RecordingRadio({this.status = ConnectionStatus.disconnected});

  int setFrequencyCalls = 0;
  int setModeCalls = 0;
  int setGainCalls = 0;
  int setAutoGainCalls = 0;
  int setSampleRateCalls = 0;
  int startRecordingCalls = 0;
  int stopRecordingCalls = 0;

  /// 可翻转的录制态（默认未在录）。
  @override
  bool recording = false;

  @override
  ConnectionStatus status;

  @override
  String? errorMessage;

  @override
  int freqHz = 100000000;

  @override
  DemodMode mode = DemodMode.nfm;

  @override
  double gainDb = 20;

  @override
  bool autoGain = true;

  @override
  double sampleRateHz = 2.048e6;

  @override
  double volume = 1;

  @override
  bool muted = false;

  // ---- 静噪门真值（get_status 细化 / get_squelch_status 用）----
  @override
  bool squelchEnabled = true;

  @override
  double squelchThresholdDb = -60;

  @override
  bool squelchOpen = true;

  @override
  double squelchLevelDb = -45.5;

  @override
  bool squelchAuto = false;

  // ---- 范围扫描真值（get_status 细化 / get_scan_status 用）----
  @override
  bool scanning = false;

  @override
  int? scanHz;

  @override
  double scanProgress = 0;

  @override
  bool scanPaused = false;

  @override
  Future<void> setFrequencyHz(int hz) async {
    setFrequencyCalls++;
    freqHz = hz;
  }

  @override
  void setMode(DemodMode m) {
    setModeCalls++;
    mode = m;
  }

  @override
  Future<void> setGainDb(double db) async {
    setGainCalls++;
    gainDb = db;
  }

  @override
  Future<void> setAutoGain(bool on) async {
    setAutoGainCalls++;
    autoGain = on;
  }

  @override
  Future<void> setSampleRateHz(double hz) async {
    setSampleRateCalls++;
    sampleRateHz = hz;
  }

  @override
  Future<void> startRecording() async {
    startRecordingCalls++;
    recording = true;
  }

  @override
  Future<RecordingMeta?> stopRecording() async {
    stopRecordingCalls++;
    final was = recording;
    recording = false;
    if (!was) return null;
    return RecordingMeta(
      startedAtEpochMs: 1,
      frequencyHz: freqHz,
      mode: mode.name,
      wavFileName: 'rec_test.wav',
      durationMs: 1234,
    );
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} 本测试不需要');
}

// 国际空间站近地 TLE（与 passes_test / sgp4_test 同一颗）。
const String _issL1 =
    '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
const String _issL2 =
    '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';

void main() {
  group('P0 断开诚实性：未连接时 4 个动作工具不得报成功', () {
    test('disconnected：动作工具回 ok:false 且底层零调用', () async {
      final radio = RecordingRadio(status: ConnectionStatus.disconnected);
      final tools = buildRadioTools(radio); // manualMode=false

      Future<Map<String, dynamic>> call(
          String name, Map<String, dynamic> args) async {
        final t = tools.firstWhere((e) => e.name == name);
        return jsonDecode(await t.execute(args)) as Map<String, dynamic>;
      }

      final f = await call('set_frequency', <String, dynamic>{'frequency_mhz': 145.0});
      final m = await call('set_mode', <String, dynamic>{'mode': 'nfm'});
      final g = await call('set_gain', <String, dynamic>{'gain_db': 20.0});
      final s =
          await call('set_sample_rate', <String, dynamic>{'sample_rate_hz': 2.048e6});
      // start_recording 同样要过连接 gate（未连接不得假装开始录）。
      final r = await call('start_recording', <String, dynamic>{});

      for (final r in [f, m, g, s, r]) {
        expect(r['ok'], isFalse, reason: '断开时不得回 ok:true');
        expect(r['error'], contains('接收机未连接'), reason: '诚实报错');
      }
      expect(radio.setFrequencyCalls, 0);
      expect(radio.setModeCalls, 0);
      expect(radio.setGainCalls, 0);
      expect(radio.setSampleRateCalls, 0);
      expect(radio.startRecordingCalls, 0);
    });

    test('reconnecting 同样视为未连接（不假装成功）', () async {
      final radio = RecordingRadio(status: ConnectionStatus.reconnecting);
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'set_frequency');
      final r = jsonDecode(await t.execute(
          <String, dynamic>{'frequency_mhz': 145.0})) as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('接收机未连接'));
      expect(radio.setFrequencyCalls, 0);
    });

    test('connected：动作工具真正下发（回归，P0 不破坏正常链路）', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected);
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'set_frequency');
      final r = jsonDecode(await t.execute(
          <String, dynamic>{'frequency_mhz': 145.0})) as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(radio.setFrequencyCalls, 1);
      expect(radio.freqHz, 145000000);
    });
  });

  group('predict_passes（固定 TLE / 固定时间，禁真网）', () {
    // 带名称行，便于按 "ISS" 匹配（fromLines 会退化为 "NORAD 25544"）。
    final iss = Tle.parseThreeLine('ISS (ZARYA)', _issL1, _issL2);
    // 与 passes_test 同一站，保证窗口内确有过境。
    const station = Station(lat: 40.0, lon: -100.0);
    // 固定时钟：TLE 历元 +2h，确定性。
    DateTime fixedNow() => iss.epoch.add(const Duration(hours: 2)).toUtc();

    SatPassesService seededService() =>
        SatPassesService(now: fixedNow)..seedTles = [iss];

    test('命中过境：返回 passes 列表且字段与 C++ 侧对齐', () async {
      final radio = RecordingRadio(); // 只读，不依赖连接
      final tools = buildRadioTools(radio,
          passesService: seededService(), station: station);
      final t = tools.firstWhere((e) => e.name == 'predict_passes');
      final r = jsonDecode(await t.execute(<String, dynamic>{
        'satellite_name': 'ISS',
        'hours': 48,
      })) as Map<String, dynamic>;

      expect(r['ok'], isTrue);
      final passes = (r['passes'] as List<dynamic>).cast<Map<String, dynamic>>();
      expect(passes, isNotEmpty, reason: 'ISS 每天过境十余次');
      for (final p in passes) {
        // C++ 侧对齐字段。
        expect(p.containsKey('startUtc'), isTrue);
        expect(p.containsKey('endUtc'), isTrue);
        expect(p.containsKey('maxElevationDeg'), isTrue);
        expect(p.containsKey('azimuthDeg'), isTrue);
        expect(DateTime.parse(p['startUtc'] as String)
            .isBefore(DateTime.parse(p['endUtc'] as String)), isTrue);
        final el = p['maxElevationDeg'] as num;
        expect(el, inInclusiveRange(0.0, 90.0));
        final az = p['azimuthDeg'] as num;
        expect(az, inInclusiveRange(0.0, 360.0));
      }
    });

    test('无同名卫星 TLE：诚实空态（不编造）', () async {
      final radio = RecordingRadio();
      final tools = buildRadioTools(radio,
          passesService: seededService(), station: station);
      final t = tools.firstWhere((e) => e.name == 'predict_passes');
      final r = jsonDecode(await t.execute(<String, dynamic>{
        'satellite_name': 'NOAA 19',
      })) as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('未找到卫星'));
    });

    test('未配置 TLE 数据源：诚实空态', () async {
      final radio = RecordingRadio();
      // 不传 passesService。
      final tools = buildRadioTools(radio, station: station);
      final t = tools.firstWhere((e) => e.name == 'predict_passes');
      final r = jsonDecode(await t.execute(<String, dynamic>{
        'satellite_name': 'ISS',
      })) as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('未配置'));
    });

    test('未配置测站坐标：诚实空态', () async {
      final radio = RecordingRadio();
      // 传 service 但不传 station。
      final tools = buildRadioTools(radio, passesService: seededService());
      final t = tools.firstWhere((e) => e.name == 'predict_passes');
      final r = jsonDecode(await t.execute(<String, dynamic>{
        'satellite_name': 'ISS',
      })) as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('测站坐标'));
    });

    test('缺 satellite_name：参数错误', () async {
      final radio = RecordingRadio();
      final tools = buildRadioTools(radio,
          passesService: seededService(), station: station);
      final t = tools.firstWhere((e) => e.name == 'predict_passes');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('satellite_name'));
    });
  });

  group('get_status 字段对齐：五态压不压平', () {
    // 与桌面端 sourceTelemetry(connected) + sourceDropped/sourceError/
    // reconnectRequested 的多信号披露对齐：connected 是就绪布尔，status 保留
    // 完整状态机名，error_message 携带真实原因。新增字段只增不改旧契约。
    Future<Map<String, dynamic>> callGetStatus(ConnectionStatus status,
        {String? errorMessage}) async {
      final radio = RecordingRadio(status: status)..errorMessage = errorMessage;
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'get_status');
      return jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
    }

    test('connected：就绪布尔 true 且 status=connected', () async {
      final r = await callGetStatus(ConnectionStatus.connected);
      expect(r['ok'], isTrue);
      expect(r['connected'], isTrue);
      expect(r['status'], 'connected');
    });

    test('reconnecting：就绪布尔 false，但 status 明确为 reconnecting（不是 idle）',
        () async {
      final r = await callGetStatus(ConnectionStatus.reconnecting,
          errorMessage: '传输中断；2s 后第 1 次自动重连');
      expect(r['connected'], isFalse);
      expect(r['status'], 'reconnecting');
      expect(r['error_message'], contains('自动重连'));
    });

    test('disconnected：就绪布尔 false，status=disconnected', () async {
      final r = await callGetStatus(ConnectionStatus.disconnected);
      expect(r['connected'], isFalse);
      expect(r['status'], 'disconnected');
    });

    test('error：就绪布尔 false，status=error 且带 error_message', () async {
      final r = await callGetStatus(ConnectionStatus.error,
          errorMessage: '连接失败: Connection refused');
      expect(r['connected'], isFalse);
      expect(r['status'], 'error');
      expect(r['error_message'], contains('Connection refused'));
    });

    test('旧字段不回退：frequency/mode/gain/sample_rate 仍在', () async {
      final r = await callGetStatus(ConnectionStatus.connected);
      expect(r['frequency_hz'], 100000000);
      expect(r['mode'], 'nfm');
      expect(r['auto_gain'], isTrue);
      expect(r['sample_rate_hz'], 2.048e6);
    });
  });

  group('工具总数审计：注册数 = Schema 数，漏注册即失败', () {
    test('buildRadioTools 恰好登记 14 个工具且名字齐全', () {
      final radio = RecordingRadio();
      final tools = buildRadioTools(radio);
      expect(tools.length, 14,
          reason: 'set_frequency/set_mode/set_gain/set_sample_rate/'
              'start_recording/stop_recording/set_squelch/get_squelch_status/'
              'get_status/predict_passes + 只读补全 list_bookmarks/'
              'list_recordings/get_recording_state/get_scan_status 共 14 个；'
              '新增工具须同步更新本断言');
      final names = tools.map((t) => t.name).toSet();
      expect(names, <String>{
        'set_frequency',
        'set_mode',
        'set_gain',
        'set_sample_rate',
        'start_recording',
        'stop_recording',
        'set_squelch',
        'get_squelch_status',
        'get_status',
        'predict_passes',
        'list_bookmarks',
        'list_recordings',
        'get_recording_state',
        'get_scan_status',
      });
    });

    test('tool-count-sync：目录已接入集合 == buildRadioTools 名 ∩ 桌面目录名', () {
      final radio = RecordingRadio();
      final Set<String> toolNames =
          buildRadioTools(radio).map((t) => t.name).toSet();
      final Set<String> desktopNames =
          kDesktopToolCatalog.map((e) => e.name).toSet();
      final Set<String> overlap = toolNames.intersection(desktopNames);
      expect(kMobileImplementedToolNames, overlap,
          reason: '目录「已接入」标注与真实注册工具必须同真值：'
              '多标=冒充能力，少标=藏能力');
    });
  });

  group('录音工具 start/stop_recording（对齐桌面 C++ 同名工具）', () {
    Future<Map<String, dynamic>> call(
        RecordingRadio radio, String name, Map<String, dynamic> args) async {
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == name);
      return jsonDecode(await t.execute(args)) as Map<String, dynamic>;
    }

    test('connected 未在录：start_recording 真正下发并置 recording=true', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected);
      final r = await call(radio, 'start_recording', <String, dynamic>{});
      expect(r['ok'], isTrue);
      expect(r['recording'], isTrue);
      expect(r['frequency_hz'], 100000000);
      expect(radio.startRecordingCalls, 1);
      expect(radio.recording, isTrue);
    });

    test('已在录：start_recording 不重复开文件，回 already_recording', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected)
        ..recording = true;
      final r = await call(radio, 'start_recording', <String, dynamic>{});
      expect(r['ok'], isTrue);
      expect(r['already_recording'], isTrue);
      expect(radio.startRecordingCalls, 0, reason: '不重复调用底层');
    });

    test('未在录：stop_recording 诚实回"当前未在录制"，不造文件', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected)
        ..recording = false;
      final r = await call(radio, 'stop_recording', <String, dynamic>{});
      expect(r['ok'], isTrue);
      expect(r['recording'], isFalse);
      expect(r['message'], contains('未在录制'));
      expect(radio.stopRecordingCalls, 1);
    });

    test('在录：stop_recording 落盘并回 meta 字段（wav/时长）', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected)
        ..recording = true;
      final r = await call(radio, 'stop_recording', <String, dynamic>{});
      expect(r['ok'], isTrue);
      expect(r['recording'], isFalse);
      expect(r['wav_file'], 'rec_test.wav');
      expect(r['duration_ms'], 1234);
      expect(radio.recording, isFalse);
    });

    test('手动模式：start/stop_recording 同被 gate（与桌面 writeTools 一致）',
        () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected);
      final tools = buildRadioTools(radio, manualMode: true);
      final t = tools.firstWhere((e) => e.name == 'start_recording');
      final r = jsonDecode(
          await t.execute(<String, dynamic>{})) as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['gated'], isTrue);
      expect(radio.startRecordingCalls, 0, reason: '手动模式不真正下发');
    });
  });

  group('get_status 细化字段：录制/播放/静噪门/扫描均为实时真值', () {
    test('细化字段逐项等于 RadioApi 当前真值（不摆拍）', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected)
        ..recording = true
        ..volume = 0.5
        ..muted = true
        ..squelchEnabled = true
        ..squelchAuto = true
        ..squelchThresholdDb = -55
        ..squelchOpen = false
        ..squelchLevelDb = -70.25
        ..scanning = true
        ..scanHz = 145000000
        ..scanProgress = 0.5
        ..scanPaused = true;
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'get_status');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;

      expect(r['ok'], isTrue);
      expect(r['recording'], isTrue);
      expect(r['volume'], 0.5);
      expect(r['muted'], isTrue);
      final squelch = r['squelch']! as Map<String, dynamic>;
      expect(squelch['enabled'], isTrue);
      expect(squelch['auto'], isTrue);
      expect(squelch['threshold_db'], -55);
      expect(squelch['open'], isFalse);
      expect(squelch['level_db'], -70.25);
      final scan = r['scan']! as Map<String, dynamic>;
      expect(scan['scanning'], isTrue);
      expect(scan['scan_hz'], 145000000);
      expect(scan['scan_progress'], 0.5);
      expect(scan['paused'], isTrue);
    });

    test('空闲真值：未扫描 scan_hz=null、未录制 recording=false', () async {
      final radio = RecordingRadio(status: ConnectionStatus.disconnected);
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'get_status');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['recording'], isFalse);
      final scan = r['scan']! as Map<String, dynamic>;
      expect(scan['scanning'], isFalse);
      expect(scan['scan_hz'], isNull);
    });
  });

  group('只读查询工具：真数据源返回真值，无数据源诚实报错/空态', () {
    test('list_bookmarks：返回真实书签逐字段（不编造台名）', () async {
      final radio = RecordingRadio();
      final tools = buildRadioTools(
        radio,
        bookmarksProvider: () => const <Bookmark>[
          Bookmark(name: '中继', frequencyHz: 145000000, mode: 'nfm', bandwidthHz: 12500),
          Bookmark(name: '', frequencyHz: 439000000, mode: 'wfm'),
        ],
      );
      final t = tools.firstWhere((e) => e.name == 'list_bookmarks');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(r['count'], 2);
      final List<dynamic> list = r['bookmarks']! as List<dynamic>;
      expect(list.length, 2);
      final Map<String, dynamic> b0 = list[0]! as Map<String, dynamic>;
      expect(b0['name'], '中继');
      expect(b0['frequency_hz'], 145000000);
      expect(b0['mode'], 'nfm');
      expect(b0['bandwidth_hz'], 12500);
      // 未命名书签如实为空串，不占位假名。
      expect((list[1]! as Map<String, dynamic>)['name'], '');
    });

    test('list_bookmarks：空源 → 空数组诚实空态；未注入 → 诚实报错（不冒充空）',
        () async {
      final radio = RecordingRadio();

      final AiTool emptyTool = buildRadioTools(
        radio,
        bookmarksProvider: () => const <Bookmark>[],
      ).firstWhere((e) => e.name == 'list_bookmarks');
      final Map<String, dynamic> emptyR =
          jsonDecode(await emptyTool.execute(<String, dynamic>{}))
              as Map<String, dynamic>;
      expect(emptyR['ok'], isTrue);
      expect(emptyR['count'], 0);
      expect(emptyR['bookmarks'], isEmpty);

      final AiTool noSource =
          buildRadioTools(radio).firstWhere((e) => e.name == 'list_bookmarks');
      final Map<String, dynamic> noR =
          jsonDecode(await noSource.execute(<String, dynamic>{}))
              as Map<String, dynamic>;
      expect(noR['ok'], isFalse);
      expect(noR['error'], contains('数据源未注入'));
    });

    test('list_recordings：真实索引逐字段，旧索引缺字段如实 null', () async {
      final radio = RecordingRadio();
      final tools = buildRadioTools(
        radio,
        recordingsProvider: () => <RecordingMeta>[
          const RecordingMeta(
            startedAtEpochMs: 1700000000000,
            frequencyHz: 145000000,
            mode: 'nfm',
            wavFileName: 'rec_a.wav',
            durationMs: 1234,
            deviceSource: RecordingSource.connected,
          ),
          // 旧索引条目：无时长/文件名/来源。
          const RecordingMeta(
            startedAtEpochMs: 1600000000000,
            frequencyHz: 439000000,
            mode: 'wfm',
          ),
        ],
      );
      final t = tools.firstWhere((e) => e.name == 'list_recordings');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(r['count'], 2);
      final List<dynamic> list = r['recordings']! as List<dynamic>;
      final Map<String, dynamic> r0 = list[0]! as Map<String, dynamic>;
      expect(r0['started_at_ms'], 1700000000000);
      expect(r0['frequency_hz'], 145000000);
      expect(r0['wav_file'], 'rec_a.wav');
      expect(r0['duration_ms'], 1234);
      final Map<String, dynamic> r1 = list[1]! as Map<String, dynamic>;
      expect(r1['duration_ms'], isNull);
      expect(r1['wav_file'], isNull);
      expect(r1['source'], isNull);
    });

    test('list_recordings：未注入数据源 → 诚实报错，不编造文件名', () async {
      final radio = RecordingRadio();
      final t = buildRadioTools(radio).firstWhere((e) => e.name == 'list_recordings');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['ok'], isFalse);
      expect(r['error'], contains('数据源未注入'));
    });

    test('get_recording_state：recording 真值 + 索引条数；未注入只给 note', () async {
      final radio = RecordingRadio(status: ConnectionStatus.connected)..recording = true;
      final AiTool withSource = buildRadioTools(
        radio,
        recordingsProvider: () => <RecordingMeta>[
          const RecordingMeta(
            startedAtEpochMs: 1,
            frequencyHz: 1,
            mode: 'nfm',
          ),
        ],
      ).firstWhere((e) => e.name == 'get_recording_state');
      final Map<String, dynamic> r =
          jsonDecode(await withSource.execute(<String, dynamic>{}))
              as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(r['recording'], isTrue);
      expect(r['connected'], isTrue);
      expect(r['recordings_indexed'], 1);

      final AiTool noSource = buildRadioTools(radio)
          .firstWhere((e) => e.name == 'get_recording_state');
      final Map<String, dynamic> r2 =
          jsonDecode(await noSource.execute(<String, dynamic>{}))
              as Map<String, dynamic>;
      expect(r2['ok'], isTrue);
      expect(r2['recording'], isTrue, reason: '录制真值不依赖索引数据源');
      expect(r2.containsKey('recordings_indexed'), isFalse);
      expect(r2['note'], contains('未注入'));
    });

    test('get_scan_status：扫描真值（未扫描 scan_hz 为 null，不臆造频点）', () async {
      final radio = RecordingRadio()
        ..scanning = true
        ..scanHz = 145500000
        ..scanProgress = 0.25
        ..scanPaused = true;
      final tools = buildRadioTools(radio);
      final t = tools.firstWhere((e) => e.name == 'get_scan_status');
      final r = jsonDecode(await t.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r['ok'], isTrue);
      expect(r['scanning'], isTrue);
      expect(r['scan_hz'], 145500000);
      expect(r['scan_progress'], 0.25);
      expect(r['paused'], isTrue);

      final idle = RecordingRadio();
      final t2 = buildRadioTools(idle).firstWhere((e) => e.name == 'get_scan_status');
      final r2 = jsonDecode(await t2.execute(<String, dynamic>{}))
          as Map<String, dynamic>;
      expect(r2['scanning'], isFalse);
      expect(r2['scan_hz'], isNull);
    });

    test('只读工具在断开时照常可读（读取不构成动作，不设连接门）', () async {
      final radio = RecordingRadio(status: ConnectionStatus.disconnected);
      final tools = buildRadioTools(
        radio,
        bookmarksProvider: () => const <Bookmark>[],
        recordingsProvider: () => const <RecordingMeta>[],
      );
      for (final String name in <String>[
        'list_bookmarks',
        'list_recordings',
        'get_recording_state',
        'get_scan_status',
      ]) {
        final t = tools.firstWhere((e) => e.name == name);
        final r = jsonDecode(await t.execute(<String, dynamic>{}))
            as Map<String, dynamic>;
        expect(r['ok'], isTrue, reason: '$name 只读不应被连接门拦截');
      }
    });
  });
}
