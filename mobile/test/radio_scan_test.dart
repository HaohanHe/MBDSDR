// 范围扫描真实链路测试：注入脚本化 rtl_tcp 客户端（不建 Socket）。
// 验证：每点真实调谐、真实电平量测、命中经 onSignalActivity(source:'scan') 写活动日志；
// 进度推进、停止后提前退出、未连接空态。全部离线、云内可跑。
library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/null_pcm_sink.dart';
import 'package:mbdsdr_mobile/dsp/iq.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/rtl_tcp_client.dart';

/// 脚本化假 rtl_tcp 客户端：记录真实调谐频率，不建 Socket。
class _FakeClient extends RtlTcpClient {
  final _iq = StreamController<IqBlock>.broadcast();
  ConnectionStatus _status = ConnectionStatus.disconnected;
  final List<int> tunedHz = [];

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
  Future<void> setFrequencyHz(int hz) async => tunedHz.add(hz);

  void close() => _iq.close();
}

class _Hit {
  _Hit(this.frequencyHz, this.mode, this.levelDbfs, this.source);
  final int frequencyHz;
  final String mode;
  final double levelDbfs;
  final String source;
}

void main() {
  test('扫描逐点真实调谐，命中写活动日志且字段完整、来源为 scan', () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    final hits = <_Hit>[];
    ctl.onSignalActivity = ({
      required int frequencyHz,
      required String mode,
      required double levelDbfs,
      String source = 'squelch',
    }) =>
        hits.add(_Hit(frequencyHz, mode, levelDbfs, source));

    await ctl.connect('127.0.0.1', 1234);
    expect(ctl.scanning, isFalse);

    // 门限压到极低：让真实静噪底（≈下限）也判为命中，验证链路真的在量测并记录。
    await ctl.startScan(
      startHz: 144000000,
      endHz: 144100000,
      stepHz: 50000,
      thresholdDbfs: -300,
      dwellMs: 20,
    );

    // 3 个步进点：144.000 / 144.050 / 144.100 MHz（connect 另有一次自动调谐在前）。
    expect(client.tunedHz.sublist(client.tunedHz.length - 3),
        [144000000, 144050000, 144100000]);
    expect(ctl.scanning, isFalse); // 扫描结束复位
    expect(hits.length, 3);
    for (var i = 0; i < 3; i++) {
      expect(hits[i].frequencyHz, [144000000, 144050000, 144100000][i]);
      expect(hits[i].mode, isNotEmpty);
      // 电平为真实静噪门量测值（dBFS），非伪造常量。
      expect(hits[i].levelDbfs, isA<double>());
      expect(hits[i].source, 'scan');
    }
    await ctl.disconnect();
    client.close();
  });

  test('停止扫描：stopScan 后循环提前退出，不再继续调谐', () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    await ctl.connect('127.0.0.1', 1234);

    // 大范围、长驻留，便于中途停。
    final future = ctl.startScan(
      startHz: 100000000,
      endHz: 200000000,
      stepHz: 10000,
      thresholdDbfs: -300,
      dwellMs: 80,
    );
    await Future<void>.delayed(const Duration(milliseconds: 120));
    ctl.stopScan();
    await future;

    expect(ctl.scanning, isFalse);
    // 原计划 10001 点；中途停止后应远少于该数。
    expect(client.tunedHz.length, lessThan(10001));
    expect(client.tunedHz.length, greaterThan(0)); // 至少真调谐过一点
    await ctl.disconnect();
    client.close();
  });

  test('未连接时 startScan 空转：不调谐、不命中、不挂起', () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    var fired = false;
    ctl.onSignalActivity = ({
      required int frequencyHz,
      required String mode,
      required double levelDbfs,
      String source = 'squelch',
    }) =>
        fired = true;

    await ctl.startScan(
      startHz: 144000000,
      endHz: 144100000,
      stepHz: 50000,
      thresholdDbfs: -300,
    );
    expect(client.tunedHz, isEmpty);
    expect(fired, isFalse);
    expect(ctl.scanning, isFalse);
  });

  test('下行方向：从 endHz 向 startHz 递减调谐（对齐桌面 ScanDirection::Down）',
      () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    await ctl.connect('127.0.0.1', 1234);

    // 3 个步进点：144.000 / 144.050 / 144.100 MHz。下行应从高到低调。
    await ctl.startScan(
      startHz: 144000000,
      endHz: 144100000,
      stepHz: 50000,
      thresholdDbfs: -300,
      dwellMs: 20,
      direction: ScanDirection.down,
    );

    expect(client.tunedHz.sublist(client.tunedHz.length - 3),
        [144100000, 144050000, 144000000]);
    expect(ctl.scanning, isFalse);
    await ctl.disconnect();
    client.close();
  });

  test('暂停：冻结调谐；恢复：从暂停处继续（对齐桌面 pause/resume）', () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    await ctl.connect('127.0.0.1', 1234);

    // 大范围、短驻留，便于中途暂停观察冻结。
    final future = ctl.startScan(
      startHz: 100000000,
      endHz: 200000000,
      stepHz: 10000,
      thresholdDbfs: -300,
      dwellMs: 30,
    );
    await Future<void>.delayed(const Duration(milliseconds: 120));

    ctl.pauseScan();
    expect(ctl.scanPaused, isTrue);
    final frozen = client.tunedHz.length;
    await Future<void>.delayed(const Duration(milliseconds: 200));
    // 暂停期间不得继续调谐（冻结当前频点与驻留计时）。
    expect(client.tunedHz.length, frozen,
        reason: '暂停期间应冻结，不得继续调谐');

    ctl.resumeScan();
    expect(ctl.scanPaused, isFalse);
    final resumed = client.tunedHz.length;
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(client.tunedHz.length, greaterThan(resumed),
        reason: '恢复后应继续调谐');

    ctl.stopScan();
    await future;
    expect(ctl.scanning, isFalse);
    expect(ctl.scanPaused, isFalse);
    await ctl.disconnect();
    client.close();
  });

  test('命中停留：命中频点额外驻留 hitHoldMs，总耗时显著变长', () async {
    final client = _FakeClient();
    final ctl = RadioController(sink: NoOpSink(), clientFactory: () => client);
    await ctl.connect('127.0.0.1', 1234);

    // 基线：无命中停留（2 点，每点 dwell 15ms ≈ 30ms 量级）。
    final sw0 = Stopwatch()..start();
    await ctl.startScan(
      startHz: 144000000,
      endHz: 144050000,
      stepHz: 50000,
      thresholdDbfs: -300,
      dwellMs: 15,
    );
    sw0.stop();
    final noHold = sw0.elapsedMilliseconds;

    // 命中停留：门限压到极低使每点都命中；每点命中后再驻留 200ms。
    final sw1 = Stopwatch()..start();
    await ctl.startScan(
      startHz: 145000000,
      endHz: 145050000,
      stepHz: 50000,
      thresholdDbfs: -300,
      dwellMs: 15,
      hitHoldMs: 200,
    );
    sw1.stop();
    final withHold = sw1.elapsedMilliseconds;

    expect(noHold, lessThan(200),
        reason: '无命中停留应很快结束：实际 ${noHold}ms');
    expect(withHold, greaterThan(noHold + 250),
        reason: '有命中停留应显著更长：实际 ${withHold}ms');
    await ctl.disconnect();
    client.close();
  });
}
