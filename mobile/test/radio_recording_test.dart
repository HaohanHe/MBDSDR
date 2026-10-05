// 生产录制接线端到端：假 IQ → 真实解调 → startRecording 落 16-bit WAV + sidecar →
// stopRecording 经 onRecordingFinalized 进索引；门控诚实（未连接/未配目录抛 StateError）；
// 断连自动收尾；回放经 mbdsdr/audio 通道（mock）。全部离线、云内可跑。
library;

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/file_player.dart';
import 'package:mbdsdr_mobile/audio/file_recording_sink.dart';
import 'package:mbdsdr_mobile/audio/null_pcm_sink.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/dsp/iq.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/recording_store.dart';
import 'package:mbdsdr_mobile/services/rtl_tcp_client.dart';

/// 脚本化假 rtl_tcp 客户端（与 radio_controller_test 同款）：不建真实 Socket。
class _FakeClient extends RtlTcpClient {
  final _iq = StreamController<IqBlock>.broadcast();
  ConnectionStatus _status = ConnectionStatus.disconnected;

  @override
  Stream<IqBlock> get iqStream => _iq.stream;
  @override
  ConnectionStatus get status => _status;
  @override
  String? get lastError;
  @override
  Future<void> connect(String host, int port) async =>
      _status = ConnectionStatus.connected;
  @override
  Future<void> disconnect() async => _status = ConnectionStatus.disconnected;
  @override
  Future<void> setSampleRateHz(int hz) async {}
  @override
  Future<void> setGainMode({required bool automatic}) async {}
  @override
  Future<void> setAgcMode({required bool on}) async {}
  @override
  Future<void> setGainDb(double db) async {}
  @override
  Future<void> setFrequencyHz(int hz) async {}

  void emit(IqBlock b) => _iq.add(b);
}

IqBlock _iq(int n) {
  final i = Float32List(n);
  final q = Float32List(n);
  for (var k = 0; k < n; k++) {
    i[k] = (k % 2 == 0) ? 0.3 : -0.3;
  }
  return IqBlock(i: i, q: q, timestamp: DateTime.now());
}

RadioController _ctl(Directory dir, _FakeClient client) => RadioController(
      sink: NoOpSink(),
      clientFactory: () => client,
      recordingsDirProvider: () => RecordingStore(dir: dir).recordingsDir(),
    );

void main() {
  buttonTests();

  group('录制端到端（假 IQ → 真 WAV → sidecar → 索引回调）', () {
    test('连接→startRecording→喂IQ→stopRecording 落盘并回调 meta', () async {
      final dir = await Directory.systemTemp.createTemp('rec-e2e');
      final client = _FakeClient();
      final ctl = _ctl(dir, client);
      RecordingMeta? finalized;
      ctl.onRecordingFinalized = (m) => finalized = m;

      // 未连接：startRecording 诚实抛 StateError（按钮同时被禁用）。
      await expectLater(ctl.startRecording(), throwsStateError);

      await ctl.connect('host', 1);
      expect(ctl.status, ConnectionStatus.connected);
      expect(ctl.recording, isFalse);

      await ctl.startRecording();
      expect(ctl.recording, isTrue);

      client.emit(_iq(8192));
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final meta = await ctl.stopRecording();
      expect(ctl.recording, isFalse);
      expect(meta, isNotNull);
      expect(finalized, same(meta)); // 进索引
      expect(meta!.deviceSource, RecordingSource.connected);
      expect(meta.wavFileName, endsWith('.wav'));
      expect(meta.frequencyHz, ctl.freqHz);

      // 真 WAV 落盘且含 PCM 数据（>44 字节头）。
      final wav = File('${dir.path}/${meta.wavFileName}');
      expect(wav.existsSync(), isTrue);
      final bytes = await wav.readAsBytes();
      expect(bytes.length, greaterThan(44));

      // sidecar JSON 落盘，deviceSource=connected。
      final jsonName = meta.wavFileName!.replaceAll('.wav', '.json');
      expect(File('${dir.path}/$jsonName').existsSync(), isTrue);

      ctl.dispose();
      await dir.delete(recursive: true);
    });

    test('已连接但未配录音目录 → StateError（不假录）', () async {
      final ctl = RadioController(sink: NoOpSink(), clientFactory: () => _FakeClient());
      await ctl.connect('h', 1);
      await expectLater(ctl.startRecording(), throwsStateError);
      ctl.dispose();
    });

    test('重复 startRecording 不建第二个文件；未在录时 stop 返回 null', () async {
      final dir = await Directory.systemTemp.createTemp('rec-dbl');
      final store = RecordingStore(dir: dir);
      final client = _FakeClient();
      final ctl = RadioController(
        sink: NoOpSink(),
        clientFactory: () => client,
        recordingsDirProvider: () => store.recordingsDir(),
      );
      await ctl.connect('h', 1);
      await ctl.startRecording();
      await ctl.startRecording(); // 忽略
      client.emit(_iq(4096));
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await ctl.stopRecording();
      // 未在录再 stop → null。
      expect(await ctl.stopRecording(), isNull);

      expect((await store.list()).length, 1);
      ctl.dispose();
      await dir.delete(recursive: true);
    });

    test('录制中断连：自动收尾落盘并回调，不留半写文件', () async {
      final dir = await Directory.systemTemp.createTemp('rec-disc');
      final client = _FakeClient();
      final ctl = _ctl(dir, client);
      RecordingMeta? finalized;
      ctl.onRecordingFinalized = (m) => finalized = m;

      await ctl.connect('h', 1);
      await ctl.startRecording();
      client.emit(_iq(4096));
      await Future<void>.delayed(const Duration(milliseconds: 20));

      await ctl.disconnect(); // 断连应自动 stopRecording
      expect(ctl.recording, isFalse);
      expect(finalized, isNotNull);
      expect((await RecordingStore(dir: dir).list()).length, 1);

      ctl.dispose();
      await dir.delete(recursive: true);
    });
  });

  group('回放通道集成（store 解析 wav → mbdsdr/audio）', () {
    test('onPlay 复现：dir + wavFileName → startFile 打到 mbdsdr/audio', () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      const channel = MethodChannel('mbdsdr/audio');
      final calls = <MethodCall>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
        calls.add(call);
        return null;
      });

      final dir = await Directory.systemTemp.createTemp('rec-play');
      final store = RecordingStore(dir: dir);
      // 真实落一段 wav。
      final sink = FileRecordingSink(
        dir: dir,
        metaFactory: () => const RecordingMeta(
          startedAtEpochMs: 1, frequencyHz: 145800000, mode: 'wfm',
          sampleRateHz: 48000,
        ),
      );
      await sink.start();
      sink.write(Float32List.fromList([0.5]));
      await sink.dispose();
      final meta = sink.result!;

      // 复现 home_shell onPlay：录音目录 + wavFileName 拼绝对路径。
      final player = FilePlayer(channel: channel);
      final d = await store.recordingsDir();
      await player.startFile(
        path: '${d.path}/${meta.wavFileName}',
        sampleRate: meta.sampleRateHz ?? 48000,
      );
      expect(calls.first.method, 'startFile');
      final args = calls.first.arguments as Map;
      expect(args['path'], endsWith(meta.wavFileName!));
      expect(args['sampleRate'], 48000);

      await player.stopFile();
      await player.dispose();
      await dir.delete(recursive: true);
    });
  });
}

/// 完整渲染 fake：控制面板需要的字段全给，recording 可翻转以测按钮态。
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
  void setVolume(double v) {}
  @override
  void setMuted(bool m) {}
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
  void setMode(DemodMode m) {}
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

  // 可翻转的录制态。
  @override
  bool recording = false;
  @override
  Future<void> startRecording() async {
    recording = true;
    notifyListeners();
  }

  @override
  Future<RecordingMeta?> stopRecording() async {
    recording = false;
    notifyListeners();
    return null;
  }

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
}

Widget _wrap(_PanelRadio r) => MaterialApp(
      home: Scaffold(
        body: SpectrumPage(controller: r, rtlHost: '127.0.0.1', rtlPort: 1),
      ),
    );

void buttonTests() {
  testWidgets('未连接：录音按钮禁用（onPressed==null）并说明原因', (tester) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final r = _PanelRadio()..status = ConnectionStatus.disconnected;
    await tester.pumpWidget(_wrap(r));
    await tester.pump();

    final btn = find.widgetWithIcon(IconButton, Icons.fiber_manual_record_outlined);
    expect(btn, findsOneWidget);
    expect(tester.widget<IconButton>(btn).onPressed, isNull);
  });

  testWidgets('已连接：录音按钮可点，点击后进入录制中态', (tester) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final r = _PanelRadio()..status = ConnectionStatus.connected;
    await tester.pumpWidget(_wrap(r));
    await tester.pump();

    final btn = find.widgetWithIcon(IconButton, Icons.fiber_manual_record_outlined);
    expect(btn, findsOneWidget);
    expect(tester.widget<IconButton>(btn).onPressed, isNotNull);

    await tester.tap(btn);
    await tester.pump();
    expect(r.recording, isTrue);
    // 录制中图标变为实心红点。
    expect(find.byIcon(Icons.fiber_manual_record), findsOneWidget);
  });
}
