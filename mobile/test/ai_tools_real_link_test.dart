// W2d 真链路闭环：断开诚实性 + predict_passes 确定性 + 工具总数审计。
//
// 红线（_PHASE3_SPEC §3 / A4 §4-P0）：
//   * 断开时 4 个动作工具不得回 ok:true，且不得真正调用 RadioApi；
//   * predict_passes 用固定 TLE/固定时间注入，禁真网、禁编造过境；
//   * buildRadioTools().length 必须等于登记工具数，未来漏注册即失败。
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/ai_tools.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
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

      for (final r in [f, m, g, s]) {
        expect(r['ok'], isFalse, reason: '断开时不得回 ok:true');
        expect(r['error'], contains('接收机未连接'), reason: '诚实报错');
      }
      expect(radio.setFrequencyCalls, 0);
      expect(radio.setModeCalls, 0);
      expect(radio.setGainCalls, 0);
      expect(radio.setSampleRateCalls, 0);
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
    test('buildRadioTools 恰好登记 6 个工具且名字齐全', () {
      final radio = RecordingRadio();
      final tools = buildRadioTools(radio);
      expect(tools.length, 6,
          reason: 'set_frequency/set_mode/set_gain/set_sample_rate/'
              'get_status/predict_passes 共 6 个；新增工具须同步更新本断言');
      final names = tools.map((t) => t.name).toSet();
      expect(names, <String>{
        'set_frequency',
        'set_mode',
        'set_gain',
        'set_sample_rate',
        'get_status',
        'predict_passes',
      });
    });
  });
}
