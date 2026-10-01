// 书签点击跳频的 widget 测试：泵出频谱控制面板，点一个书签 chip，
// 断言真实调用 controller.setFrequencyHz 并在模式可用时 setMode。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/models/bookmark.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';

/// 记录式 fake：只记录 setFrequencyHz / setMode 的调用结果。
class _TapRadio extends ChangeNotifier implements RadioApi {
  @override
  ConnectionStatus status = ConnectionStatus.disconnected;

  @override
  String? errorMessage;

  @override
  int freqHz = 144000000;

  @override
  DemodMode mode = DemodMode.nfm;

  @override
  double gainDb = 20;

  @override
  bool autoGain = true;

  @override
  double sampleRateHz = 2.048e6;

  @override
  double volume = 1.0;

  @override
  bool muted = false;

  @override
  bool squelchEnabled = false;

  @override
  double squelchThresholdDb = -50;

  @override
  bool squelchOpen = false;

  @override
  double squelchLevelDb = -120;

  @override
  Future<void> connect(String host, int port) async {}

  @override
  Future<void> disconnect() async {}

  @override
  Future<void> setFrequencyHz(int hz) async {
    freqHz = hz;
  }

  @override
  void setMode(DemodMode m) {
    mode = m;
  }

  @override
  Future<void> setGainDb(double db) async {}

  @override
  Future<void> setAutoGain(bool on) async {}

  @override
  Future<void> setSampleRateHz(double hz) async {}

  @override
  void setVolume(double v) {}

  @override
  void setMuted(bool m) {}

  @override
  void setSquelchEnabled(bool on) {}

  @override
  void setSquelchThresholdDb(double db) {}

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
}

void main() {
  testWidgets('点书签 chip 真实跳频并应用模式', (tester) async {
    final radio = _TapRadio();
    const bookmarks = <Bookmark>[
      Bookmark(name: '测试台', frequencyHz: 146520000, mode: 'wfm', bandwidthHz: 200000),
    ];

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: Scaffold(
          backgroundColor: AppTokens.bgMain,
          body: SpectrumPage(
            controller: radio,
            rtlHost: '127.0.0.1',
            rtlPort: 1234,
            bookmarks: bookmarks,
            onAddBookmark: (_, _, _, _) {},
            onRemoveBookmark: (_) {},
          ),
        ),
      ),
    );
    await tester.pump();

    // chip 存在且显示名称+频率。
    expect(find.textContaining('146.5200'), findsOneWidget);

    // 控制面板在滚动视图里，先滚到 chip 再点。
    await tester.ensureVisible(find.byType(InputChip).first);
    await tester.pumpAndSettle();
    await tester.tap(find.byType(InputChip).first);
    await tester.pump();

    expect(radio.freqHz, 146520000);
    expect(radio.mode, DemodMode.wfm);
  });

  testWidgets('无名书签 chip 用频率占位显示', (tester) async {
    final radio = _TapRadio();
    const bookmarks = <Bookmark>[
      Bookmark(name: '', frequencyHz: 145000000),
    ];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SpectrumPage(
            controller: radio,
            rtlHost: '127.0.0.1',
            rtlPort: 1234,
            bookmarks: bookmarks,
            onAddBookmark: (_, _, _, _) {},
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('145.0000 MHz'), findsOneWidget);
  });
}
