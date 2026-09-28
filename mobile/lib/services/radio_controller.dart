// 收音机控制层：把 rtl_tcp 客户端、FFT 频谱、NFM/WFM 解调串起来。
//
// RadioApi 是抽象接口，供外壳/测试 fake 接线；RadioController 是真实实现，
//  extends ChangeNotifier 以便 UI 监听状态。
//
// 音频流说明：本版只产出解调后的音频 Float32List，**不接扬声器**——
// 播放（设备音频路由、采样率匹配、增益）为后续工作，这里不伪造听感。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../dsp/demod.dart';
import '../dsp/fft_processor.dart';
import '../dsp/iq.dart';
import '../models/radio_state.dart';
import 'rtl_tcp_client.dart';

/// 收音机对外接口（供外壳模块与测试 fake 实现）。
abstract interface class RadioApi {
  /// 当前连接状态。
  ConnectionStatus get status;

  /// 最近一次错误描述。
  String? get errorMessage;

  /// 当前调谐频率（Hz）。
  int get freqHz;

  /// 当前解调模式。
  DemodMode get mode;

  /// 当前手动增益（dB）。
  double get gainDb;

  /// 是否自动增益。
  bool get autoGain;

  /// 当前采样率（Hz）。
  double get sampleRateHz;

  /// 连接到 rtl_tcp 服务端。
  Future<void> connect(String host, int port);

  /// 断开连接。
  Future<void> disconnect();

  /// 设置频率（Hz）。
  Future<void> setFrequencyHz(int hz);

  /// 切换解调模式。
  void setMode(DemodMode mode);

  /// 设置手动增益（dB）。
  Future<void> setGainDb(double db);

  /// 开关自动增益。
  Future<void> setAutoGain(bool on);

  /// 设置采样率（Hz）。
  Future<void> setSampleRateHz(double hz);

  /// 实时频谱帧流。
  Stream<SpectrumFrame> get spectrumStream;

  /// 解调后的单声道音频流（Float32，[-1,1] 附近）。本版不播放。
  Stream<Float32List> get audioStream;
}

/// 真实收音机控制器。
class RadioController extends ChangeNotifier implements RadioApi {
  RtlTcpClient? _client;
  StreamSubscription<IqBlock>? _iqSub;

  ConnectionStatus _status = ConnectionStatus.disconnected;
  String? _errorMessage;

  int _freqHz = 144000000; // 144.0 MHz 业余段
  DemodMode _mode = DemodMode.nfm;
  double _gainDb = 20;
  bool _autoGain = true;
  double _sampleRateHz = 2.048e6;

  late final FftProcessor _fft = FftProcessor(fftSize: 2048);
  FmDemod? _demod;

  // 有界最近 IQ 缓冲（满则丢旧），供 30fps 频谱取最新 N 点。
  final List<double> _recentI = <double>[];
  final List<double> _recentQ = <double>[];
  late final int _bufferCap = _fft.fftSize * 8;

  Timer? _spectrumTimer;
  final _spectrumCtrl = StreamController<SpectrumFrame>.broadcast();
  final _audioCtrl = StreamController<Float32List>.broadcast();

  RadioController();

  @override
  ConnectionStatus get status => _status;
  @override
  String? get errorMessage => _errorMessage;
  @override
  int get freqHz => _freqHz;
  @override
  DemodMode get mode => _mode;
  @override
  double get gainDb => _gainDb;
  @override
  bool get autoGain => _autoGain;
  @override
  double get sampleRateHz => _sampleRateHz;
  @override
  Stream<SpectrumFrame> get spectrumStream => _spectrumCtrl.stream;
  @override
  Stream<Float32List> get audioStream => _audioCtrl.stream;

  void _setStatus(ConnectionStatus s, [String? err]) {
    _status = s;
    _errorMessage = err;
    notifyListeners();
  }

  @override
  Future<void> connect(String host, int port) async {
    if (_status == ConnectionStatus.connected ||
        _status == ConnectionStatus.connecting) {
      return;
    }
    _setStatus(ConnectionStatus.connecting);
    final client = RtlTcpClient();
    _client = client;

    _iqSub = client.iqStream.listen(
      _onIqBlock,
      onError: (Object e) => _setStatus(ConnectionStatus.error, '$e'),
    );

    await client.connect(host, port);
    if (client.status != ConnectionStatus.connected) {
      _setStatus(ConnectionStatus.error, client.lastError ?? '连接失败');
      return;
    }

    // 连接成功后按约定顺序下发：采样率 → 增益模式 → 增益 → 频率。
    await client.setSampleRateHz(_sampleRateHz.round());
    await client.setGainMode(automatic: _autoGain);
    if (!_autoGain) {
      await client.setGainDb(_gainDb);
    }
    await client.setFrequencyHz(_freqHz);

    _demod = _buildDemod();
    _startSpectrumTimer();
    _setStatus(ConnectionStatus.connected);
  }

  FmDemod _buildDemod() => switch (_mode) {
    DemodMode.nfm => NfmDemod(inputRateHz: _sampleRateHz),
    DemodMode.wfm => WfmDemod(inputRateHz: _sampleRateHz),
  };

  void _startSpectrumTimer() {
    _spectrumTimer?.cancel();
    _spectrumTimer = Timer.periodic(const Duration(milliseconds: 33), (_) {
      if (_recentI.length < _fft.fftSize) return;
      final n = _fft.fftSize;
      final start = _recentI.length - n;
      final i = Float64List.fromList(_recentI.sublist(start));
      final q = Float64List.fromList(_recentQ.sublist(start));
      final frame = _fft.compute(
        i,
        q,
        centerFreqHz: _freqHz.toDouble(),
        sampleRateHz: _sampleRateHz,
      );
      if (!_spectrumCtrl.isClosed) _spectrumCtrl.add(frame);
    });
  }

  void _onIqBlock(IqBlock block) {
    // 追加到有界缓冲，满则丢旧。
    _recentI.addAll(block.i);
    _recentQ.addAll(block.q);
    final overflow = _recentI.length - _bufferCap;
    if (overflow > 0) {
      _recentI.removeRange(0, overflow);
      _recentQ.removeRange(0, overflow);
    }

    // 连续送入当前模式解调器（音频本版不播放，仅产出）。
    final demod = _demod;
    if (demod != null) {
      final c = Float64x2List(block.length);
      for (var n = 0; n < block.length; n++) {
        c[n] = Float64x2(block.i[n], block.q[n]);
      }
      final audio = demod.process(c);
      if (audio.isNotEmpty && !_audioCtrl.isClosed) {
        _audioCtrl.add(audio);
      }
    }
  }

  @override
  Future<void> disconnect() async {
    _spectrumTimer?.cancel();
    _spectrumTimer = null;
    await _iqSub?.cancel();
    _iqSub = null;
    await _client?.disconnect();
    _client = null;
    _recentI.clear();
    _recentQ.clear();
    _demod = null;
    _setStatus(ConnectionStatus.disconnected);
  }

  @override
  Future<void> setFrequencyHz(int hz) async {
    _freqHz = hz;
    notifyListeners();
    await _client?.setFrequencyHz(hz);
  }

  @override
  void setMode(DemodMode mode) {
    _mode = mode;
    if (_status == ConnectionStatus.connected) {
      _demod = _buildDemod();
    }
    notifyListeners();
  }

  @override
  Future<void> setGainDb(double db) async {
    _gainDb = db;
    notifyListeners();
    if (!_autoGain) {
      await _client?.setGainDb(db);
    }
  }

  @override
  Future<void> setAutoGain(bool on) async {
    _autoGain = on;
    notifyListeners();
    await _client?.setGainMode(automatic: on);
  }

  @override
  Future<void> setSampleRateHz(double hz) async {
    _sampleRateHz = hz;
    notifyListeners();
    await _client?.setSampleRateHz(hz.round());
    if (_status == ConnectionStatus.connected) {
      _demod = _buildDemod();
    }
  }

  @override
  void dispose() {
    _spectrumTimer?.cancel();
    unawaited(_client?.disconnect());
    unawaited(_iqSub?.cancel());
    unawaited(_spectrumCtrl.close());
    unawaited(_audioCtrl.close());
    super.dispose();
  }
}
