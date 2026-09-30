// 收音机控制层：把 rtl_tcp 客户端、FFT 频谱、NFM/WFM 解调、PCM 输出串起来。
//
// RadioApi 是抽象接口，供外壳/测试 fake 接线；RadioController 是真实实现，
//  extends ChangeNotifier 以便 UI 监听状态。
//
// 音频流说明：解调后的 Float32List 既通过 [audioStream] 暴露给 UI，也会被本
// 控制器订阅并逐帧喂入注入的 [PcmSink]（默认 [NoOpSink]，真机接 PlatformPcmSink）。
//
// 连接健壮性：传输中断（IQ 流 error / 对端关闭）不直接挂死，而是进入
// [ConnectionStatus.reconnecting]，按指数退避（1s→2s→4s→8s→封顶 15s）自动重连；
// 用户主动 disconnect() 会停止退避定时器并回到 disconnected，不再自动重试。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../app/tokens.dart';
import '../audio/null_pcm_sink.dart';
import '../audio/pcm_sink.dart';
import '../dsp/demod.dart';
import '../dsp/fft_processor.dart';
import '../dsp/iq.dart';
import '../dsp/squelch.dart';
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

  /// 当前音量（0..1）。
  double get volume;

  /// 当前是否静音。
  bool get muted;

  /// 连接到 rtl_tcp 服务端。
  Future<void> connect(String host, int port);

  /// 断开连接（停止自动重连）。
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

  /// 设置音量（0..1）：存字段、转发给 sink、通知 UI。
  void setVolume(double v);

  /// 设置静音：存字段、转发给 sink、通知 UI。
  void setMuted(bool m);

  /// 是否启用静噪门控（基于解调后音频真实 RMS 电平）。
  bool get squelchEnabled;

  /// 静噪门限（dBFS）。
  double get squelchThresholdDb;

  /// 当前静噪门是否开门（true=送声，false=被门控静音）。
  bool get squelchOpen;

  /// 当前平滑后的真实解调电平（dBFS），无信号时为下限。
  double get squelchLevelDb;

  /// 开关静噪门控。
  void setSquelchEnabled(bool on);

  /// 设置静噪门限（dBFS），内部 clamp 到 AppTokens 区间。
  void setSquelchThresholdDb(double db);

  /// 实时频谱帧流。
  Stream<SpectrumFrame> get spectrumStream;

  /// 解调后的单声道音频流（Float32，[-1,1] 附近）；本控制器会同时喂给 sink。
  Stream<Float32List> get audioStream;
}

/// 真实收音机控制器。
class RadioController extends ChangeNotifier implements RadioApi {
  RadioController({PcmSink? sink, RtlTcpClient Function()? clientFactory})
      : _sink = sink ?? NoOpSink(),
        _clientFactory = clientFactory ?? RtlTcpClient.new {
    // 订阅自己的解调音频流，逐帧过静噪门后喂入 PCM sink。
    _audioSub = audioStream.listen(_onAudioFrame);
  }

  // ---------------------------------------------------- 依赖注入
  final PcmSink _sink;
  final RtlTcpClient Function() _clientFactory;

  /// 解调音频送给输出设备的名义采样率（Hz）。WFM 恰好 ~48k；NFM 约 51.2k，
  /// 重采样为已知限制。sink.start 统一用 48000 与原生 AudioTrack 对齐。
  static const int audioSampleRateHz = 48000;

  // ---------------------------------------------------- 连接状态
  RtlTcpClient? _client;
  StreamSubscription<IqBlock>? _iqSub;
  late final StreamSubscription<Float32List> _audioSub;

  ConnectionStatus _status = ConnectionStatus.disconnected;
  String? _errorMessage;

  // 最近一次 connect 参数，供自动重连复用。
  String? _lastHost;
  int? _lastPort;

  /// 用户是否主动断开（true 时抑制一切自动重连）。
  bool _userDisconnected = false;

  /// 已连续失败的重连次数（用于指数退避）。
  int _reconnectAttempts = 0;
  Timer? _reconnectTimer;

  // ---------------------------------------------------- 调谐/音频参数
  int _freqHz = 144000000; // 144.0 MHz 业余段
  DemodMode _mode = DemodMode.nfm;
  double _gainDb = 20;
  bool _autoGain = true;
  double _sampleRateHz = 2.048e6;
  double _volume = 1.0;
  bool _muted = false;

  /// 静噪门控：电平来自真实解调音频 RMS（见 dsp/squelch.dart）。
  final SquelchGate _squelch = SquelchGate();

  /// 关门时复用的静音帧缓冲（按当前块长度惰性增长，避免逐块分配）。
  Float32List _silence = Float32List(0);

  late final FftProcessor _fft = FftProcessor(fftSize: 2048);
  FmDemod? _demod;

  // 有界最近 IQ 缓冲（满则丢旧），供 30fps 频谱取最新 N 点。
  final List<double> _recentI = <double>[];
  final List<double> _recentQ = <double>[];
  late final int _bufferCap = _fft.fftSize * 8;

  Timer? _spectrumTimer;
  final _spectrumCtrl = StreamController<SpectrumFrame>.broadcast();
  final _audioCtrl = StreamController<Float32List>.broadcast();

  // ---------------------------------------------------- getters
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
  double get volume => _volume;
  @override
  bool get muted => _muted;
  @override
  bool get squelchEnabled => _squelch.enabled;
  @override
  double get squelchThresholdDb => _squelch.thresholdDb;
  @override
  bool get squelchOpen => _squelch.open;
  @override
  double get squelchLevelDb => _squelch.levelDb;
  @override
  Stream<SpectrumFrame> get spectrumStream => _spectrumCtrl.stream;
  @override
  Stream<Float32List> get audioStream => _audioCtrl.stream;

  void _setStatus(ConnectionStatus s, [String? err]) {
    _status = s;
    _errorMessage = err;
    notifyListeners();
  }

  // ---------------------------------------------------- 连接 / 重连
  @override
  Future<void> connect(String host, int port) async {
    if (_status == ConnectionStatus.connected ||
        _status == ConnectionStatus.connecting) {
      return;
    }
    // 若正处于自动重连，先停掉退避定时器，用新参数立即建立。
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _userDisconnected = false;
    _reconnectAttempts = 0;
    _lastHost = host;
    _lastPort = port;
    _setStatus(ConnectionStatus.connecting);

    final ok = await _establish();
    if (ok) {
      _setStatus(ConnectionStatus.connected);
    } else {
      _beginReconnect('连接失败: ${_client?.lastError ?? '未知错误'}');
    }
  }

  /// 用注入工厂新建一条客户端并完成握手（采样率→增益→频率→解调→sink）。
  /// 成功返回 true；失败时调用方负责进入重连/错误态。
  Future<bool> _establish() async {
    final host = _lastHost;
    final port = _lastPort;
    if (host == null || port == null) return false;

    final client = _clientFactory();
    _client = client;
    _iqSub = client.iqStream.listen(
      _onIqBlock,
      onError: _onIqError,
    );

    try {
      await client.connect(host, port);
    } catch (e) {
      return false;
    }
    if (client.status != ConnectionStatus.connected) {
      return false;
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

    // 打开音频输出（重复调用安全：实现应先释放旧资源再重启）。
    try {
      await _sink.start(sampleRateHz: audioSampleRateHz);
    } catch (_) {
      // sink 打开失败不阻断数据接收，仅无声。
    }
    return true;
  }

  /// IQ 流异常 / 对端断开：不直接报错挂死，进入自动重连。
  void _onIqError(Object e, StackTrace st) {
    if (_userDisconnected) return;
    // 已主动断开或已在重连中，忽略迟到的 error。
    if (_status == ConnectionStatus.disconnected ||
        _status == ConnectionStatus.reconnecting) {
      return;
    }
    _beginReconnect('传输中断: $e');
  }

  /// 进入重连：拆掉旧连接，按指数退避调度下一次尝试。
  void _beginReconnect(String reason) {
    _teardownClient();
    _reconnectAttempts += 1;
    final delay = _backoffDelay(_reconnectAttempts);
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, _reconnectTick);
    _setStatus(
      ConnectionStatus.reconnecting,
      '$reason；${delay.inSeconds}s 后第 $_reconnectAttempts 次自动重连',
    );
  }

  /// 退避策略：1s, 2s, 4s, 8s, … 封顶 15s。
  static Duration _backoffDelay(int attempt) {
    final ms = 1000 * (1 << (attempt - 1));
    return Duration(milliseconds: ms > 15000 ? 15000 : ms);
  }

  Future<void> _reconnectTick() async {
    if (_userDisconnected) return;
    if (_lastHost == null || _lastPort == null) return;
    _setStatus(ConnectionStatus.connecting, '正在自动重连…');
    final ok = await _establish();
    if (ok) {
      _reconnectAttempts = 0;
      _setStatus(ConnectionStatus.connected);
    } else {
      _beginReconnect('重连失败: ${_client?.lastError ?? '无法连接'}');
    }
  }

  /// 拆掉当前客户端、订阅与频谱定时器（不改变连接状态由调用方决定）。
  void _teardownClient() {
    _spectrumTimer?.cancel();
    _spectrumTimer = null;
    unawaited(_iqSub?.cancel());
    _iqSub = null;
    final c = _client;
    _client = null;
    if (c != null) unawaited(c.disconnect());
    _recentI.clear();
    _recentQ.clear();
    _demod = null;
    _squelch.reset();
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

    // 连续送入当前模式解调器（产出的音频帧同时被 _audioSub 喂入 sink）。
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

  /// 解调音频逐帧处理：用真实 RMS 电平跑静噪门，开门送原帧、关门送等长静音。
  ///
  /// 这就是移动端静噪的真实静音落点——与手动静音/音量同走 Dart→原生 PCM 路径。
  /// 注意：原生播放侧（PlatformPcmSink 的 MethodChannel 实现）目前「真机待验」，
  /// 但门控本身在 Dart 侧是真实生效的：关门时送给 sink 的是全零帧而非原音频。
  void _onAudioFrame(Float32List frame) {
    final wasOpen = _squelch.open;
    final open = _squelch.process(
      frame,
      audioSampleRateHz: audioSampleRateHz.toDouble(),
    );
    if (open) {
      _sink.write(frame);
    } else {
      if (_silence.length != frame.length) {
        _silence = Float32List(frame.length);
      }
      _sink.write(_silence);
    }
    // 仅在开门/关门跳变时通知 UI，避免每帧抖动 rebuild。
    if (open != wasOpen) notifyListeners();
  }

  // ---------------------------------------------------- 会话恢复
  /// 启动时（connect 之前）一次性恢复上次的频率/模式/音量/静音。
  ///
  /// 频率/模式只写入字段（尚未连接，不下发命令）；音量/静音同时转发给 sink，
  /// 这样后续 sink.start 时就带着正确的增益。传 null 的参数保持不变。
  Future<void> applySession({
    int? freqHz,
    DemodMode? mode,
    double? volume,
    bool? muted,
  }) async {
    if (freqHz != null) _freqHz = freqHz;
    if (mode != null) _mode = mode;
    if (volume != null) {
      _volume = volume.clamp(0.0, 1.0).toDouble();
      _sink.setVolume(_volume);
    }
    if (muted != null) {
      _muted = muted;
      _sink.setMuted(_muted);
    }
    notifyListeners();
  }

  // ---------------------------------------------------- 断开 / 参数
  @override
  Future<void> disconnect() async {
    _userDisconnected = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _sink.dispose();
    _teardownClient();
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
  void setVolume(double v) {
    _volume = v.clamp(0.0, 1.0).toDouble();
    _sink.setVolume(_volume);
    notifyListeners();
  }

  @override
  void setMuted(bool m) {
    _muted = m;
    _sink.setMuted(m);
    notifyListeners();
  }

  @override
  void setSquelchEnabled(bool on) {
    _squelch.enabled = on;
    // 重新开关后让门按当前电平立即判定，不沿用旧 hangover。
    _squelch.reset();
    notifyListeners();
  }

  @override
  void setSquelchThresholdDb(double db) {
    _squelch.thresholdDb = db.clamp(
      AppTokens.squelchThresholdMinDb,
      AppTokens.squelchThresholdMaxDb,
    );
    notifyListeners();
  }

  @override
  void dispose() {
    _userDisconnected = true;
    _reconnectTimer?.cancel();
    _audioSub.cancel();
    _spectrumTimer?.cancel();
    unawaited(_sink.dispose());
    _teardownClient();
    unawaited(_spectrumCtrl.close());
    unawaited(_audioCtrl.close());
    super.dispose();
  }
}
