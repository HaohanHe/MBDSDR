// 移动端 AI 工具描述质量门禁（模型体验轴，DSH 四维标准 · 移动端镜像）
//
// 设计来源：与 Python 侧 mbdsdr_ai/tests/test_tool_description_quality.py
// 同构——模型对工具的全部认知来自 description + parameters，描述缺维 =
// 移动端 AI 盲飞。桌面 Python 侧 147 工具已有门禁；本文件把同一标准
// 钉到移动端 buildRadioTools() 的 10 个工具上（两扇门，一个标准）。
//
// 钉死：
//   * 地板：无空/过短描述（≥100 字符）、参数 schema 形状合法；
//   * 状态变更型（set_*/start_recording/stop_recording）四维齐全；
//   * 只读型（get_*/predict_passes）至少两维；
//   * 关键诚实契约逐条钉死（AGC 耦合、模式边界、不编造、WAV≠IQ）；
//   * 全工具面四维覆盖率棘轮阈值（只升不降）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/ai_tools.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/ai_client.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';

/// 描述门禁专用最小 Fake：不执行任何工具，只取 description/parameters。
class _DescFake implements RadioApi {
  @override
  ConnectionStatus status = ConnectionStatus.connected;
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
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('描述门禁不执行工具');
}

const int _minDescChars = 100; // 全工具面描述最短阈值（移动端描述天然紧凑）

// 四维语义族关键词（与 Python 门禁同一口径，措辞宽松匹配）。
const List<String> _pre = <String>[
  '前置条件',
  '需已连接',
  '必须先已连接',
  '必须已连接',
  '输入契约',
  '必须且只能',
];
const List<String> _rec = <String>[
  '失败恢复',
  '重试',
  '返回范围错误',
  '返回明确错误',
  '返回合法值',
  '诚实回错',
  '不要重发',
  '不要反复',
];
const List<String> _side = <String>[
  '副作用',
  '持续写盘',
  '改变实际',
  '会影响',
  '落盘',
  '决定音频播出',
];
const List<String> _bound = <String>[
  '能力边界',
  '仅覆盖',
  '仅支持',
  '只能取',
  '不编造',
  '不伪造',
  '未实现',
  '只读',
];

bool _has(String desc, List<String> kws) => kws.any(desc.contains);

List<AiTool> _tools() => buildRadioTools(_DescFake());

void main() {
  // ---------------------------------------------------------------- 地板

  test('全工具面描述 ≥ $_minDescChars 字符，无空描述', () {
    final Map<String, int> short = <String, int>{};
    for (final t in _tools()) {
      if (t.description.trim().length < _minDescChars) {
        short[t.name] = t.description.trim().length;
      }
    }
    expect(short, isEmpty, reason: '描述不足 $_minDescChars 字的工具: $short');
  });

  test('工具面规模锁定为 10 个（漏注册/误增即红）', () {
    expect(_tools().length, 10);
  });

  test('参数 schema 形状合法（type=object + properties 为 Map）', () {
    for (final t in _tools()) {
      expect(t.parameters['type'], 'object', reason: '${t.name} type 非 object');
      final Object? props = t.parameters['properties'];
      expect(props is Map<String, dynamic>, true,
          reason: '${t.name} properties 非 Map');
    }
  });

  // ---------------------------------------------------------------- 四维

  // 状态变更型：四维齐全（改射频/写盘/改门控）。
  const List<String> mutators = <String>[
    'set_frequency',
    'set_mode',
    'set_gain',
    'set_sample_rate',
    'set_squelch',
    'start_recording',
    'stop_recording',
  ];

  for (final name in mutators) {
    test('状态变更型 $name 四维齐全', () {
      final AiTool t = _tools().firstWhere((AiTool x) => x.name == name);
      final List<String> missing = <String>[
        if (!_has(t.description, _pre)) '前置条件',
        if (!_has(t.description, _rec)) '失败恢复',
        if (!_has(t.description, _side)) '副作用',
        if (!_has(t.description, _bound)) '能力边界',
      ];
      expect(missing, isEmpty, reason: '$name 缺维度（当前: ${t.description}）');
    });
  }

  // 只读型：至少两维。
  const List<String> readers = <String>[
    'get_squelch_status',
    'get_status',
    'predict_passes',
  ];

  for (final name in readers) {
    test('只读型 $name 至少两维', () {
      final AiTool t = _tools().firstWhere((AiTool x) => x.name == name);
      final int dims = <int>[
        if (_has(t.description, _pre)) 1,
        if (_has(t.description, _rec)) 1,
        if (_has(t.description, _side)) 1,
        if (_has(t.description, _bound)) 1,
      ].length;
      expect(dims >= 2, true, reason: '$name 仅 $dims 维（需≥2）');
    });
  }

  // ---------------------------------------------------------------- 诚实契约

  test('诚实契约钉死：AGC 耦合 / 模式边界 / 不编造 / WAV≠IQ', () {
    final Map<String, AiTool> byName = <String, AiTool>{
      for (final AiTool t in _tools()) t.name: t,
    };
    // set_gain：手动增益仅在 AGC 关闭时生效（实现真相，删改即红）。
    expect(byName['set_gain']!.description.contains('AGC 关闭'),
        true,
        reason: 'set_gain 必须说明 gain_db 仅在 AGC 关闭时生效');
    // set_mode：移动端仅 nfm/wfm（不冒充桌面端模式集）。
    expect(byName['set_mode']!.description.contains('移动端仅支持'), true,
        reason: 'set_mode 必须标注移动端模式边界');
    // predict_passes：诚实空态不编造。
    expect(byName['predict_passes']!.description.contains('不编造'), true);
    // start_recording：录解调音频 WAV 而非 IQ（与桌面产物差异）。
    expect(byName['start_recording']!.description.contains('不录原始 IQ'), true);
  });

  // ---------------------------------------------------------------- 棘轮

  test('全工具面四维覆盖率棘轮（只升不降）', () {
    final List<AiTool> tools = _tools();
    int pre = 0, rec = 0, side = 0, bound = 0, anyd = 0;
    for (final AiTool t in tools) {
      final bool p = _has(t.description, _pre);
      final bool r = _has(t.description, _rec);
      final bool s = _has(t.description, _side);
      final bool b = _has(t.description, _bound);
      if (p) pre++;
      if (r) rec++;
      if (s) side++;
      if (b) bound++;
      if (p || r || s || b) anyd++;
    }
    // 阈值取当前实测约 80%（10 工具：pre 10 / rec 7 / side 6 / bound 7 /
    // anyd 10）。下一轮目标全维 100%，届时上调。
    expect(anyd / tools.length, greaterThanOrEqualTo(0.9));
    expect(pre / tools.length, greaterThanOrEqualTo(0.8));
    expect(rec / tools.length, greaterThanOrEqualTo(0.6));
    expect(side / tools.length, greaterThanOrEqualTo(0.5));
    expect(bound / tools.length, greaterThanOrEqualTo(0.6));
  });
}
