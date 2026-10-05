// G4 扫频面板 UI 复核：开始入口 / 进行中进度+停止按钮。
//
// 判定（诚实）：开始（_promptScan→startScan）/ 停止（stopScan）/ 命中列表
// （onSignalActivity source:'scan'→活动日志「扫描」标签）/ 书签跳频
// （bookmark_tap_test）四件本就具备。本文件补一条 UI 断言：扫描进行中确实渲染
// 进度条 + 「停止」按钮，且点停止真实调用 controller.stopScan。
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

class _ScanRadio extends ChangeNotifier implements RadioApi {
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
  Future<void> setFrequencyHz(int hz) async {}
  @override
  void setMode(DemodMode mode) {}
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
  @override
  bool get recording => false;
  @override
  Future<void> startRecording() async {}
  @override
  Future<RecordingMeta?> stopRecording() async => null;

  // 扫描状态可切换，记录 stopScan/pause/resume 调用。
  @override
  bool scanning = true;
  @override
  int? scanHz = 145050000;
  @override
  double scanProgress = 0.4;
  int stopCalls = 0;
  @override
  bool scanPaused = false;
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
  void stopScan() => stopCalls++;
  @override
  void pauseScan() {
    scanPaused = true;
    notifyListeners();
  }
  @override
  void resumeScan() {
    scanPaused = false;
    notifyListeners();
  }
}

void main() {
  testWidgets('扫描进行中：渲染进度条 + 「停止」按钮，点停止真实调用 stopScan',
      (tester) async {
    final radio = _ScanRadio();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    ));
    await tester.pump();

    // 进行中：进度条 + 当前 MHz 读数 + 「停止」。
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.textContaining('扫描中'), findsOneWidget);
    expect(find.text('停止'), findsOneWidget);

    await tester.ensureVisible(find.text('停止'));
    await tester.pump();
    await tester.tap(find.text('停止'));
    await tester.pump();
    expect(radio.stopCalls, 1, reason: '点停止必须真实下发 stopScan');
  });

  testWidgets('未在扫描：显示「范围扫描」开始入口（不显示进度/停止）', (tester) async {
    final radio = _ScanRadio()..scanning = false;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    ));
    await tester.pump();
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(find.text('停止'), findsNothing);
    expect(find.text('范围扫描'), findsOneWidget);
    // 未在扫描时点开始图标 → 弹范围扫描对话框（起始/结束 MHz）。
    await tester.ensureVisible(find.byIcon(Icons.tune));
    await tester.pump();
    await tester.tap(find.byIcon(Icons.tune));
    await tester.pumpAndSettle();
    expect(find.text('范围扫描'), findsWidgets); // 标题与行文案均出现
    expect(find.text('开始'), findsOneWidget); // 对话框的开始按钮
  });

  testWidgets('扫描中：「暂停/恢复」按钮随 scanPaused 切换，文案与状态同步',
      (tester) async {
    final radio = _ScanRadio();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    ));
    await tester.pump();

    // 初始未暂停：显示「暂停」按钮，无「恢复」。
    expect(find.text('暂停'), findsOneWidget);
    expect(find.text('恢复'), findsNothing);
    expect(find.textContaining('扫描中'), findsOneWidget);

    await tester.ensureVisible(find.text('暂停'));
    await tester.pump();
    await tester.tap(find.text('暂停'));
    await tester.pump();
    expect(radio.scanPaused, isTrue, reason: '点暂停必须真实下发 pauseScan');
    expect(find.text('恢复'), findsOneWidget);
    expect(find.text('暂停'), findsNothing);
    expect(find.textContaining('已暂停'), findsOneWidget);

    await tester.ensureVisible(find.text('恢复'));
    await tester.pump();
    await tester.tap(find.text('恢复'));
    await tester.pump();
    expect(radio.scanPaused, isFalse, reason: '点恢复必须真实下发 resumeScan');
    expect(find.text('暂停'), findsOneWidget);
    expect(find.text('恢复'), findsNothing);
  });
}
