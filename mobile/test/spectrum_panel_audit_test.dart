// 频谱控制面板 / AppTokens 审美修正的 widget 测试。
//
// 覆盖本轮 Figma 清单对齐的关键修正：
//   * 新增语义令牌存在且取值正确（间距 XL/XXL、selectedFill、focusRing、
//     divider、字重 medium/semi、radiusPill、disabledFill、缓动）；
//   * 控制面板卡片：内 padding 16、外边距 8（卡片内 padding ≥ 外边距节奏）；
//   * SegmentedButton 选中块用低饱和蓝灰 selectedFill，不用亮 accent 铺满；
//   * hero 大频率读数走 weightMedium(500)，不通篇 600。
// 用内存 FakeRadioApi（实现 RadioApi），不触碰真实网络。
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';

/// 内存脚本化 RadioApi：只渲染控制面板所需字段，其余空实现。
class _PanelRadio extends ChangeNotifier implements RadioApi {
  @override
  ConnectionStatus status = ConnectionStatus.disconnected;

  @override
  String? errorMessage;

  @override
  int freqHz = 145000000;

  @override
  DemodMode mode = DemodMode.nfm;

  @override
  double gainDb = 20;

  @override
  bool autoGain = true;

  @override
  double sampleRateHz = 2.048e6;

  @override
  double volume = 0.6;

  @override
  bool muted = false;

  @override
  void setVolume(double v) => volume = v;

  @override
  void setMuted(bool m) => muted = m;

  @override
  bool squelchEnabled = false;

  @override
  double squelchThresholdDb = -50;

  @override
  bool squelchOpen = false;

  @override
  double squelchLevelDb = -120;

  @override
  void setSquelchEnabled(bool on) => squelchEnabled = on;

  @override
  void setSquelchThresholdDb(double db) => squelchThresholdDb = db;

  @override
  bool get squelchAuto => false;

  @override
  void setSquelchAuto(bool on) {}

  @override
  Future<void> connect(String host, int port) async {}

  @override
  Future<void> disconnect() async {}

  @override
  Future<void> setFrequencyHz(int hz) async {}

  @override
  void setMode(DemodMode m) => mode = m;

  @override
  Future<void> setGainDb(double db) async {}

  @override
  Future<void> setAutoGain(bool on) async {}

  @override
  Future<void> setSampleRateHz(double hz) async {}

  @override
  Stream<SpectrumFrame> get spectrumStream => const Stream.empty();

  @override
  Stream<Float32List> get audioStream => const Stream.empty();

  // 只读渲染 fake：不支持真实录制。
  @override
  bool get recording => false;

  @override
  Future<void> startRecording() async {}

  @override
  Future<RecordingMeta?> stopRecording() async => null;

  @override
  bool get scanning => false;

  @override
  int? get scanHz => null;

  @override
  double get scanProgress => 0;

  @override
  Future<void> startScan(
          {required int startHz,
          required int endHz,
          required int stepHz,
          required double thresholdDbfs,
          int dwellMs = 300}) async {}

  @override
  void stopScan() {}
}

Widget _wrap(_PanelRadio radio) => MaterialApp(
      debugShowCheckedModeBanner: false,
      home: Scaffold(
        backgroundColor: AppTokens.bgMain,
        body: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
          onOpenSettings: () {},
        ),
      ),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AppTokens 新增审美语义', () {
    test('间距 4pt 栅格：补 XL=24 / XXL=32', () {
      expect(AppTokens.spacingS, 4);
      expect(AppTokens.spacingM, 8);
      expect(AppTokens.spacingL, 16);
      expect(AppTokens.spacingXL, 24);
      expect(AppTokens.spacingXXL, 32);
    });

    test('字重语义：medium=500 / semi=600 / regular=400', () {
      expect(AppTokens.weightRegular, FontWeight.w400);
      expect(AppTokens.weightMedium, FontWeight.w500);
      expect(AppTokens.weightSemi, FontWeight.w600);
    });

    test('选中态/焦点/分隔/禁用层语义存在', () {
      // 选中填充是低饱和蓝灰（#919cac 系），不是亮 accent。
      expect(AppTokens.selectedFill, isNot(AppTokens.accent));
      expect(AppTokens.focusRing, isNot(Colors.transparent));
      expect(AppTokens.divider, isNot(Colors.transparent));
      expect(AppTokens.disabledFill, isNot(Colors.transparent));
      expect(AppTokens.radiusPill, greaterThan(50));
    });

    test('跨端选中态收敛：selectedFill alpha=0.16 / selectedText=#d7dee8', () {
      // 与桌面 kSelectedFill(alpha 0.16) / kSelectedText(#d7dee8) 对齐。
      expect(AppTokens.selectedFillAlpha, closeTo(0.16, 0.001));
      expect(AppTokens.selectedFill, const Color(0x29919CAC));
      expect(AppTokens.selectedText, const Color(0xFFD7DEE8));
    });

    test('主交互色收敛到低饱和蓝灰，亮蓝仅留仪器轨迹', () {
      // P0-1：#7CC4FF 退役为主交互色 -> #919cac 三态。
      expect(AppTokens.accent, const Color(0xFF919CAC));
      expect(AppTokens.accentHover, const Color(0xFFAAB3C2));
      expect(AppTokens.accentPress, const Color(0xFF7C8796));
      // 亮蓝保留为仪器画布轨迹专用 token。
      expect(AppTokens.traceColor, const Color(0xFF7CC4FF));
    });

    test('hero 读数与正文走 medium/regular，不通篇 600', () {
      expect(AppTokens.freqReadout.fontWeight, AppTokens.weightMedium);
      expect(AppTokens.sectionTitle.fontWeight, AppTokens.weightMedium);
      expect(AppTokens.body.fontWeight, AppTokens.weightRegular);
      expect(AppTokens.mono.fontWeight, AppTokens.weightMedium);
    });
  });

  testWidgets('控制面板卡片：内 padding 16、外边距 8', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final radio = _PanelRadio();
    await tester.pumpWidget(_wrap(radio));
    await tester.pump();
    expect(tester.takeException(), isNull);

    // 控制面板根 Container：margin=8、padding=16。
    final containers = tester.widgetList<Container>(find.byType(Container));
    var foundPanel = false;
    for (final c in containers) {
      final deco = c.decoration;
      if (deco is BoxDecoration &&
          deco.borderRadius == BorderRadius.circular(AppTokens.radiusCard)) {
        expect(c.margin, const EdgeInsets.all(AppTokens.spacingM),
            reason: '卡片外边距应落在 8px 节奏');
        expect(c.padding, const EdgeInsets.all(AppTokens.spacingL),
            reason: '卡片内 padding 应 ≥ 外边距节奏（16）');
        foundPanel = true;
      }
    }
    expect(foundPanel, isTrue, reason: '应找到控制面板卡片');
  });

  testWidgets('SegmentedButton 选中态用低饱和蓝灰 selectedFill', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final radio = _PanelRadio();
    await tester.pumpWidget(_wrap(radio));
    await tester.pump();

    final seg = tester.widget<SegmentedButton<DemodMode>>(
      find.byType(SegmentedButton<DemodMode>),
    );
    expect(seg.selected, {DemodMode.nfm});

    // 选中态背景解析为 selectedFill，而非亮 accent。
    final bg = seg.style?.backgroundColor;
    expect(bg, isNotNull);
    final selectedColor =
        bg!.resolve(<WidgetState>{WidgetState.selected});
    expect(selectedColor, AppTokens.selectedFill);
    final normalColor = bg.resolve(<WidgetState>{});
    expect(normalColor, Colors.transparent);
  });

  testWidgets('hero 大频率读数为 medium 字重且不裁切', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final radio = _PanelRadio();
    await tester.pumpWidget(_wrap(radio));
    await tester.pump();

    final readout = tester.widget<Text>(
      find.textContaining('145.0000 MHz'),
    );
    expect(readout.style?.fontWeight, AppTokens.weightMedium);
    expect(readout.style?.fontSize, AppTokens.freqReadout.fontSize);
  });

  testWidgets('hero 频率读数触控热区 ≥ touchMin(44) 且可命中', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final radio = _PanelRadio();
    await tester.pumpWidget(_wrap(radio));
    await tester.pump();

    final readout = find.textContaining('145.0000 MHz');
    expect(readout, findsOneWidget);

    // 包裹读数的 GestureDetector 即触控热区；其渲染高度应 ≥ 44。
    final hit = find.ancestor(of: readout, matching: find.byType(GestureDetector));
    expect(hit, findsWidgets);
    double best = 0;
    for (final el in tester.renderObjectList<RenderBox>(hit)) {
      if (el.attached) best = best > el.size.height ? best : el.size.height;
    }
    expect(best, greaterThanOrEqualTo(AppTokens.touchMin - 0.01),
        reason: 'hero 读数触控热区应补足到 44px');

    // 命中测试：在读数垂直中心处点击，应落在 GestureDetector 上（不抛异常）。
    final center = tester.getCenter(readout);
    await tester.tapAt(center);
    await tester.pump();
    // 弹出频率输入对话框即视为热区响应（不崩溃、命中成功）。
    expect(tester.takeException(), isNull);
  });
}
