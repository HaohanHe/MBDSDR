// G3 只读测频差游标 A/B：对齐桌面 spectrum_widget:123 的游标协议。
//
// 判定（诚实）：桌面端有 placeCursorA/B + 可拖动测 Δf；移动画布是纯 CustomPainter。
// 本块补**只读双游标叠加**：点「游标A/B」落在当前视窗中心，两线同框画 Δf 读数；
// 不调谐、不改 DSP、不持久化。测试覆盖：
//   1) formatCursorDiff 纯函数分档；
//   2) 画布带 A/B 游标渲染不抛异常；
//   3) 页面点「游标A/B」真实记录 setFrequencyHz 调用次数恒为 0（只读、不改频率）。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/widgets/spectrum_display.dart';

SpectrumFrame _frame() {
  const n = 512;
  final db = Float64List(n);
  for (var i = 0; i < n; i++) {
    db[i] = -80 + 20 * (i / n);
  }
  return SpectrumFrame(
    db: db,
    centerFreqHz: 145e6,
    sampleRateHz: 2.048e6,
    fftSize: n,
  );
}

/// 连接态 fake：记录真实调谐次数，用于证明游标是只读叠加（不调 VFO）。
class _CountingRadio extends ChangeNotifier implements RadioApi {
  final StreamController<SpectrumFrame> _ctrl =
      StreamController<SpectrumFrame>.broadcast();
  int tuneCalls = 0;

  void addFrame() => _ctrl.add(_frame());

  @override
  ConnectionStatus status = ConnectionStatus.connected;
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
  void setVolume(double v) {}
  @override
  void setMuted(bool m) {}
  @override
  bool squelchEnabled = false;
  @override
  double squelchThresholdDb = -50;
  @override
  bool squelchOpen = false;
  @override
  double squelchLevelDb = -120;
  @override
  void setSquelchEnabled(bool on) {}
  @override
  void setSquelchThresholdDb(double db) {}
  @override
  bool get squelchAuto => false;
  @override
  void setSquelchAuto(bool on) {}
  @override
  Future<void> connect(String host, int port) async {}
  @override
  Future<void> disconnect() async {}
  @override
  Future<void> setFrequencyHz(int hz) async => tuneCalls++; // 记录！
  @override
  void setMode(DemodMode mode) {}
  @override
  Future<void> setGainDb(double db) async {}
  @override
  Future<void> setAutoGain(bool on) async {}
  @override
  Future<void> setSampleRateHz(double hz) async {}
  @override
  Stream<SpectrumFrame> get spectrumStream => _ctrl.stream;
  @override
  Stream<Float32List> get audioStream => const Stream.empty();
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
          int dwellMs = 300,
          ScanDirection direction = ScanDirection.up,
          int hitHoldMs = 0}) async {}
  @override
  void stopScan() {}
  @override
  bool get scanPaused => false;
  @override
  void pauseScan() {}
  @override
  void resumeScan() {}
  @override
  void dispose() {
    _ctrl.close();
    super.dispose();
  }
}

void main() {
  test('formatCursorDiff 自动分档 Hz / kHz / MHz', () {
    expect(formatCursorDiff(0), '0 Hz');
    expect(formatCursorDiff(2500), '2.50 kHz');
    expect(formatCursorDiff(-12500), '12.50 kHz');
    expect(formatCursorDiff(1.5e6), '1.500 MHz');
    expect(formatCursorDiff(-300), '300 Hz');
  });

  testWidgets('SpectrumDisplay 带 A/B 游标渲染不抛异常（只读叠加）', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SpectrumDisplay(
            frame: _frame(),
            channelBandwidthHz: 12500,
            // 两条游标都落在当前扫宽内：Δf = 100 kHz。
            cursorAHz: 145.05e6,
            cursorBHz: 144.95e6,
          ),
        ),
      ),
    );
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(find.byType(SpectrumDisplay), findsOneWidget);
  });

  testWidgets('页面点「游标A/B」只读：不触发任何真实调谐（setFrequencyHz 恒 0 次）',
      (tester) async {
    final radio = _CountingRadio();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    ));
    radio.addFrame();
    await tester.pump();

    // 控制面板出现三个游标控件：A / B / 清除。
    expect(find.textContaining('测频差'), findsOneWidget);
    expect(find.text('A'), findsWidgets);
    expect(find.text('B'), findsWidgets);

    expect(radio.tuneCalls, 0);
    // 依次放 A、放 B、清除——全程只读叠加，绝不调 VFO。
    await tester.tap(find.text('A').first);
    await tester.pump();
    await tester.tap(find.text('B').first);
    await tester.pump();
    expect(radio.tuneCalls, 0, reason: '游标是只读测量线，放置/清除都不应调谐');
    expect(radio.freqHz, 145000000); // 频率纹丝不动

    await tester.ensureVisible(find.byIcon(Icons.close));
    await tester.pump();
    await tester.tap(find.byIcon(Icons.close));
    await tester.pump();
    expect(radio.tuneCalls, 0);
    expect(tester.takeException(), isNull);
  });
}
