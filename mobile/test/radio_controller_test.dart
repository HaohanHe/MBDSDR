// RadioController：音频 sink 接线、音量/静音转发、会话恢复、自动重连状态机。
//
// 用一个可脚本化的 [FakeRtlTcpClient]（继承真实客户端、覆写网络方法）注入控制器，
// 不触碰真实 Socket；重连退避用 FakeAsync 虚拟时间推进。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/recording_pcm_sink.dart';
import 'package:mbdsdr_mobile/dsp/iq.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/rtl_tcp_client.dart';

/// 脚本化假客户端：覆写网络相关方法，用内存 StreamController 分发 IQ。
class FakeRtlTcpClient extends RtlTcpClient {
  FakeRtlTcpClient({bool Function()? onConnect}) : _onConnect = onConnect;

  final bool Function()? _onConnect;
  final StreamController<IqBlock> _iq =
      StreamController<IqBlock>.broadcast();

  ConnectionStatus _status = ConnectionStatus.disconnected;
  String? _lastError;

  /// 成功 connect 的次数（含自动重连）。
  int connectCalls = 0;

  @override
  Stream<IqBlock> get iqStream => _iq.stream;

  @override
  ConnectionStatus get status => _status;

  @override
  String? get lastError => _lastError;

  @override
  Future<void> connect(String host, int port) async {
    connectCalls++;
    final ok = _onConnect?.call() ?? true;
    if (ok) {
      _status = ConnectionStatus.connected;
    } else {
      _status = ConnectionStatus.error;
      _lastError = 'refused';
    }
  }

  @override
  Future<void> setSampleRateHz(int hz) async {}

  @override
  Future<void> setGainMode({required bool automatic}) async {}

  @override
  Future<void> setGainDb(double db) async {}

  @override
  Future<void> setFrequencyHz(int hz) async {}

  @override
  Future<void> disconnect() async {
    _status = ConnectionStatus.disconnected;
  }

  /// 模拟服务端推来一段 IQ。
  void emit(IqBlock block) => _iq.add(block);

  /// 模拟传输中断：向 IQ 流发一个 error。
  void fail(Object e) => _iq.addError(e);
}

/// 造一段简单 IQ（I 交替、Q=0），喂给解调器产出非空音频。
IqBlock makeIqBlock(int n) {
  final i = Float32List(n);
  final q = Float32List(n);
  for (var k = 0; k < n; k++) {
    i[k] = (k % 2 == 0) ? 0.3 : -0.3;
  }
  return IqBlock(i: i, q: q, timestamp: DateTime.now());
}

void main() {
  group('音频 sink 接线', () {
    test('connect 成功后 sink.start(48000)，IQ → 解调 → sink 收到帧', () async {
      final sink = RecordingPcmSink();
      final client = FakeRtlTcpClient();
      final ctl = RadioController(sink: sink, clientFactory: () => client);

      await ctl.connect('host', 1234);
      expect(ctl.status, ConnectionStatus.connected);
      expect(sink.sampleRateHz, 48000);
      expect(sink.started, isTrue);

      client.emit(makeIqBlock(8192));
      // 让「IQ→解调→audioStream→sink」的微任务链跑完。
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(sink.recorded, isNotEmpty);
      ctl.dispose();
      expect(sink.started, isFalse);
    });
  });

  group('音量 / 静音转发', () {
    test('setVolume/setMuted 同时更新控制器字段并转发 sink', () {
      final sink = RecordingPcmSink();
      final ctl = RadioController(sink: sink, clientFactory: () => FakeRtlTcpClient());

      ctl.setVolume(0.5);
      expect(ctl.volume, closeTo(0.5, 1e-9));
      expect(sink.volume, closeTo(0.5, 1e-9));

      ctl.setMuted(true);
      expect(ctl.muted, isTrue);
      expect(sink.muted, isTrue);

      // 越界 clamp 到 [0,1]。
      ctl.setVolume(2.0);
      expect(ctl.volume, 1.0);
      expect(sink.volume, 1.0);

      ctl.dispose();
    });
  });

  group('applySession 会话恢复', () {
    test('connect 前恢复 freq/mode/volume/muted', () async {
      final sink = RecordingPcmSink();
      final ctl = RadioController(sink: sink, clientFactory: () => FakeRtlTcpClient());

      await ctl.applySession(
        freqHz: 98_500_000,
        mode: DemodMode.wfm,
        volume: 0.3,
        muted: true,
      );

      expect(ctl.freqHz, 98_500_000);
      expect(ctl.mode, DemodMode.wfm);
      expect(ctl.volume, closeTo(0.3, 1e-9));
      expect(ctl.muted, isTrue);
      // 音量/静音也已转发 sink。
      expect(sink.volume, closeTo(0.3, 1e-9));
      expect(sink.muted, isTrue);

      ctl.dispose();
    });
  });

  group('自动重连状态机', () {
    test('connected → 断流 → reconnecting → 退避后自动回 connected', () {
      FakeAsync().run((async) {
        final sink = RecordingPcmSink();
        final client = FakeRtlTcpClient();
        final ctl = RadioController(sink: sink, clientFactory: () => client);
        final statuses = <ConnectionStatus>[];
        ctl.addListener(() => statuses.add(ctl.status));

        // 首次连接成功。
        ctl.connect('host', 1234);
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.connected);
        expect(client.connectCalls, 1);

        // 喂一段 IQ，解调产出音频并喂入 sink。
        client.emit(makeIqBlock(4096));
        async.elapse(Duration.zero);
        expect(sink.recorded, isNotEmpty);

        // 传输中断 → 进入重连。
        client.fail(StateError('eof'));
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.reconnecting);
        expect(ctl.errorMessage, contains('eof'));

        // 退避 1s 未到：保持重连，不发起新 connect。
        async.elapse(const Duration(milliseconds: 900));
        expect(ctl.status, ConnectionStatus.reconnecting);
        expect(client.connectCalls, 1);

        // 跨过 1s 退避：自动重连并回到 connected。
        async.elapse(const Duration(milliseconds: 200));
        expect(ctl.status, ConnectionStatus.connected);
        expect(client.connectCalls, 2);

        expect(
          statuses,
          containsAllInOrder(<ConnectionStatus>[
            ConnectionStatus.connected,
            ConnectionStatus.reconnecting,
            ConnectionStatus.connected,
          ]),
        );

        ctl.dispose();
        async.elapse(Duration.zero);
      });
    });

    test('指数退避：1s→2s→4s，封顶 15s', () {
      FakeAsync().run((async) {
        // 让每次 connect 都失败，观测退避间隔。
        final client = FakeRtlTcpClient(onConnect: () => false);
        final ctl = RadioController(sink: RecordingPcmSink(), clientFactory: () => client);

        ctl.connect('host', 1234); // 首次失败 → 第 1 次退避 1s。
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.reconnecting);
        expect(client.connectCalls, 1); // 仅首次尝试。

        // 1s 后第 2 次 connect（仍失败）→ 退避 2s。
        async.elapse(const Duration(seconds: 1));
        expect(client.connectCalls, 2);
        expect(ctl.status, ConnectionStatus.reconnecting);

        // 2s 未到（再走 1s 仍不够 2s）：不发起第 3 次。
        async.elapse(const Duration(seconds: 1));
        expect(client.connectCalls, 2);

        // 走完 2s：第 3 次 connect → 退避 4s。
        async.elapse(const Duration(seconds: 1));
        expect(client.connectCalls, 3);

        // 4s 内不发起第 4 次。
        async.elapse(const Duration(seconds: 3));
        expect(client.connectCalls, 3);

        ctl.dispose();
        async.elapse(Duration.zero);
      });
    });

    test('主动 disconnect() 后不再自动重连', () {
      FakeAsync().run((async) {
        final client = FakeRtlTcpClient();
        final ctl = RadioController(sink: RecordingPcmSink(), clientFactory: () => client);

        ctl.connect('host', 1234);
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.connected);
        expect(client.connectCalls, 1);

        // 断流 → 已挂好 1s 重连定时器。
        client.fail(StateError('eof'));
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.reconnecting);

        // 用户主动断开。
        ctl.disconnect();
        async.elapse(Duration.zero);
        expect(ctl.status, ConnectionStatus.disconnected);

        // 时间大幅流逝，不再重连。
        async.elapse(const Duration(seconds: 30));
        expect(ctl.status, ConnectionStatus.disconnected);
        expect(client.connectCalls, 1);

        ctl.dispose();
        async.elapse(Duration.zero);
      });
    });
  });
}
