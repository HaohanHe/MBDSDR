// SpectrumDisplay / SpectrumPage 桌面级质感打磨的 widget 测试。
//
// 本文件内构造【合成但形状真实】的 SpectrumFrame 用于离屏截图——
// 这是测试专用数据，不污染任何产品代码（产品路径只吃真实 rtl_tcp IQ 流）。
import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/widgets/spectrum_display.dart';

// ---------------------------------------------------------------------------
// 测试专用合成数据：噪声底 + 中央载波 + 漂移峰 + 固定侧峰。
// 明确标注：仅用于离屏渲染截图，不进入产品代码。
// ---------------------------------------------------------------------------
double _gauss(double x, double centerHz, double sigmaHz) =>
    math.exp(-(x - centerHz) * (x - centerHz) / (2 * sigmaHz * sigmaHz));

SpectrumFrame _synthFrame(int t) {
  const n = 1024;
  const center = 145e6;
  const fs = 2.048e6;
  final db = Float64List(n);
  final drift = math.sin(t / 6.0) * 350000; // 漂移峰 → 瀑布拉出斜条纹
  for (var i = 0; i < n; i++) {
    final fOff = (i - n / 2) / n * fs; // 相对中心频率的 Hz 偏移
    var v = -86.0 + 4.0 * math.sin(i * 0.15 + t * 0.7); // 噪声底
    v += 58.0 * _gauss(fOff, 0, 6000); // 中央强载波
    v += 42.0 * _gauss(fOff, drift, 22000); // 漂移峰
    v += 30.0 * _gauss(fOff, -420000, 14000); // 左侧固定峰
    db[i] = v.clamp(-100.0, 0.0);
  }
  return SpectrumFrame(
    db: db,
    centerFreqHz: center,
    sampleRateHz: fs,
    fftSize: n,
  );
}

Widget _wrapPage(_FakeConnectedRadio radio) {
  return MaterialApp(
    debugShowCheckedModeBanner: false,
    home: Scaffold(
      backgroundColor: AppTokens.bgMain,
      body: RepaintBoundary(
        key: const Key('spectrum-root'),
        child: SpectrumPage(
          controller: radio,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
          onOpenSettings: () {},
        ),
      ),
    ),
  );
}

/// 整段（喂帧 + 截图 + 落盘 + 排空解码回调）放进 runAsync，
/// 因为瀑布的 decodeImageFromPixels / toImage 都走引擎真实异步回调。
Future<void> _pumpAndCapture(
  WidgetTester tester,
  Size size,
  int from,
  int to,
  String path,
) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  final radio = _FakeConnectedRadio();
  await tester.pumpWidget(_wrapPage(radio));
  await tester.pump();
  await tester.runAsync(() async {
    for (var t = from; t < to; t++) {
      radio.addFrame();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 16));
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
    // 解码回调已异步 setState(_image)，再 pump 一次让其重绘进图层。
    await tester.pump();
    expect(find.byType(SpectrumDisplay), findsOneWidget);
    expect(tester.takeException(), isNull);
    final boundary = tester
        .renderObject(find.byKey(const Key('spectrum-root')))
        as RenderRepaintBoundary;
    final image = await boundary.toImage(pixelRatio: 2.0);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    File(path).writeAsBytesSync(bytes!.buffer.asUint8List());
    // 卸载 widget 并等待挂起的解码回调跑完，避免收尾时 channel 报错。
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    await Future<void>.delayed(const Duration(milliseconds: 300));
  });
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('竖屏手机：渲染不溢出不抛异常，并出 PNG', (tester) async {
    await _pumpAndCapture(tester, const Size(390, 844), 0, 200,
        'scratch/spectrum_portrait.png');
  });

  testWidgets('横屏：渲染不溢出不抛异常，并出 PNG', (tester) async {
    await _pumpAndCapture(tester, const Size(844, 390), 30, 260,
        'scratch/spectrum_landscape.png');
  });

  testWidgets('SpectrumDisplay: 空 frame 不崩溃、不画假数据', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SpectrumDisplay(
            frame: null,
            channelBandwidthHz: 12500,
            onTapFrequency: (_) {},
          ),
        ),
      ),
    );
    expect(tester.takeException(), isNull);
    // null frame → shrink，不绘制轨迹。
    expect(find.byType(SpectrumDisplay), findsOneWidget);
  });

  testWidgets('SpectrumPage: 未连接时显示诚实空态（复用 EmptyState）',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final radio = _FakeRadio();
    await tester.pumpWidget(
      MaterialApp(
        home: SpectrumPage(
          controller: radio,
          rtlHost: '',
          rtlPort: 1234,
          onOpenSettings: () {},
        ),
      ),
    );
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(find.text('未连接 rtl_tcp'), findsOneWidget);
    // 空态与控制面板都引导去设置（无 host 时）。
    expect(find.text('设置 rtl_tcp 地址'), findsWidgets);
  });
}

/// 最小可控 fake：仅用于空态渲染，不产生任何假频谱流。
class _FakeRadio extends ChangeNotifier implements RadioApi {
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
  void setVolume(double v) => volume = v;

  @override
  void setMuted(bool m) => muted = m;

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
}

/// 已连接 fake：持续吐合成频谱帧（测试专用，不进产品代码）。
class _FakeConnectedRadio extends ChangeNotifier implements RadioApi {
  final StreamController<SpectrumFrame> _ctrl =
      StreamController<SpectrumFrame>.broadcast();
  int _t = 0;

  void addFrame() => _ctrl.add(_synthFrame(_t++));

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
  void setVolume(double v) => volume = v;

  @override
  void setMuted(bool m) => muted = m;

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
  Stream<SpectrumFrame> get spectrumStream => _ctrl.stream;

  @override
  Stream<Float32List> get audioStream => const Stream.empty();

  @override
  void dispose() {
    _ctrl.close();
    super.dispose();
  }
}
