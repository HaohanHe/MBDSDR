// AGC（自动增益）：真实命令下发 / 回读 / 未连接时禁用。
//
// 用记录型 FakeRtlTcpClient 注入：setAutoGain 必须同时下发
//   * setGainMode(0x03)：调谐器增益自动/手动；
//   * setAgcMode(0x08)：RTL2832 芯片数字 AGC。
// 未连接时频谱控制面板的 AGC 开关诚实禁用（onChanged==null）。
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/dsp/fft_processor.dart';
import 'package:mbdsdr_mobile/dsp/iq.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/pages/spectrum_page.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/rtl_tcp_client.dart';

/// 记录命令的假客户端：不建真实 Socket，仅记录 setGainMode / setAgcMode 调用。
class RecFakeClient extends RtlTcpClient {
  final List<bool> gainModeCalls = <bool>[];
  final List<bool> agcCalls = <bool>[];

  // ignore: close_sinks
  final _iq = StreamController<IqBlock>.broadcast();
  ConnectionStatus _status = ConnectionStatus.disconnected;

  @override
  Stream<IqBlock> get iqStream => _iq.stream;
  @override
  ConnectionStatus get status => _status;
  @override
  String? get lastError;

  @override
  Future<void> connect(String host, int port) async {
    _status = ConnectionStatus.connected;
  }

  @override
  Future<void> disconnect() async {
    _status = ConnectionStatus.disconnected;
  }

  @override
  Future<void> setGainMode({required bool automatic}) async {
    gainModeCalls.add(automatic);
  }

  @override
  Future<void> setAgcMode({required bool on}) async {
    agcCalls.add(on);
  }

  @override
  Future<void> setGainDb(double db) async {}
  @override
  Future<void> setSampleRateHz(int hz) async {}
  @override
  Future<void> setFrequencyHz(int hz) async {}
}

void _controllerTests() {
  test('setAutoGain 同时下发调谐器增益模式与芯片 AGC，回读 autoGain', () async {
    final client = RecFakeClient();
    final ctl = RadioController(clientFactory: () => client);
    await ctl.connect('host', 1234);
    expect(ctl.status, ConnectionStatus.connected);

    // 初始默认 autoGain=true：连接握手应已下发一次开。
    expect(client.gainModeCalls, contains(true));
    expect(client.agcCalls, contains(true));

    // 手动关 AGC：两个命令都应下发 false。
    await ctl.setAutoGain(false);
    expect(ctl.autoGain, isFalse);
    expect(client.gainModeCalls.last, isFalse);
    expect(client.agcCalls.last, isFalse);

    // 再打开：两个命令都应下发 true。
    await ctl.setAutoGain(true);
    expect(client.gainModeCalls.last, isTrue);
    expect(client.agcCalls.last, isTrue);

    ctl.dispose();
  });
}

/// 最小可监听 fake RadioApi：只用于渲染控制面板并断言 AGC 开关禁用态。
class _PanelRadio extends ChangeNotifier implements RadioApi {
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
  void setVolume(double v) {}
  @override
  void setMuted(bool m) {}
  @override
  void setSquelchEnabled(bool on) {}
  @override
  void setSquelchThresholdDb(double db) {}
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
  Future<void> setAutoGain(bool on) async => autoGain = on;
  @override
  Future<void> setSampleRateHz(double hz) async {}
  @override
  Stream<SpectrumFrame> get spectrumStream => const Stream.empty();
  @override
  Stream<Float32List> get audioStream => const Stream.empty();
}

Widget _wrap(_PanelRadio r) => MaterialApp(
      home: Scaffold(
        body: SpectrumPage(
          controller: r,
          rtlHost: '127.0.0.1',
          rtlPort: 1234,
        ),
      ),
    );

void main() {
  _controllerTests();

  // 「自动增益」标签所在 Row 内的 Switch（二者是兄弟，不是祖先/后代）。
  Finder agcSwitch() => find.descendant(
        of: find
            .ancestor(of: find.text('自动增益'), matching: find.byType(Row))
            .first,
        matching: find.byType(Switch),
      );

  testWidgets('未连接：自动增益开关禁用（onChanged==null）', (tester) async {
    final r = _PanelRadio()..status = ConnectionStatus.disconnected;
    await tester.pumpWidget(_wrap(r));
    await tester.pump();

    final sw = tester.widget<Switch>(agcSwitch());
    expect(sw.onChanged, isNull);
  });

  testWidgets('已连接：自动增益开关可点，回读 autoGain 翻转', (tester) async {
    final r = _PanelRadio()..status = ConnectionStatus.connected;
    await tester.pumpWidget(_wrap(r));
    await tester.pump();

    final sw = tester.widget<Switch>(agcSwitch());
    expect(sw.onChanged, isNotNull);
    expect(sw.value, isTrue);

    await tester.tap(agcSwitch());
    await tester.pump();
    expect(r.autoGain, isFalse);
  });
}
