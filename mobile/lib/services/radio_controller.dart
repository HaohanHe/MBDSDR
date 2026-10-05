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
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../app/tokens.dart';
import '../audio/file_recording_sink.dart';
import '../audio/null_pcm_sink.dart';
import '../audio/pcm_sink.dart';
import '../dsp/demod.dart';
import '../dsp/fft_processor.dart';
import '../dsp/iq.dart';
import '../dsp/squelch.dart';
import '../models/radio_state.dart';
import '../models/recording.dart';
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

  /// 当前是否正在把解调音频录制成本地 .wav（生产走 [FileRecordingSink]）。
  bool get recording;

  /// 开始录制：把当前解调音频量化成 16-bit 小端 WAV 落盘，并写 sidecar JSON。
  /// 仅在已连接（有真实解调音频）时可用；未连接 / 未配置录音目录时抛 [StateError]。
  Future<void> startRecording();

  /// 停止录制：关 WAV、写 sidecar、产出 [RecordingMeta]（经 onRecordingFinalized 入索引）。
  /// 未在录制时返回 null。
  Future<RecordingMeta?> stopRecording();

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

  /// 设置静噪门限（dBFS），内部 clamp 到 AppTokens 区间。手动设置即退出自动门限。
  void setSquelchThresholdDb(double db);

  /// 静噪是否自动门限（门限 = 实测同域音频噪声底 + 裕量，自动跟随）。
  bool get squelchAuto;

  /// 开关自动门限。开启后门限随噪声底自动跟随；手动拖门限滑杆即退出自动。
  void setSquelchAuto(bool on);

  /// 是否正在范围扫描。
  bool get scanning;

  /// 扫描当前正在调谐的频率（Hz）；未扫描为 null。
  int? get scanHz;

  /// 扫描进度 0..1。
  double get scanProgress;

  /// 扫描是否处于暂停（冻结当前频点调谐与驻留计时；对齐桌面 FrequencyScanner.pause）。
  bool get scanPaused;

  /// 开始范围扫描（真实调谐+真实电平量测，命中写活动日志）。未连接/已在扫描直接返回。
  ///
  /// [direction]：上行（start→end 递增）/ 下行（end→start 递减）。
  /// [hitHoldMs]：命中后在该频点额外驻留的毫秒数（0 = 命中即继续；对齐桌面
  /// HitHoldMode::FixedMs holdMs）。暂停/取消对该驻留同样生效。
  Future<void> startScan({
    required int startHz,
    required int endHz,
    required int stepHz,
    required double thresholdDbfs,
    int dwellMs,
    ScanDirection direction,
    int hitHoldMs,
  });

  /// 请求停止扫描。
  void stopScan();

  /// 暂停扫描：冻结当前频点调谐与驻留计时（对齐桌面 FrequencyScanner.pause）。
  void pauseScan();

  /// 恢复扫描：从暂停处继续（对齐桌面 FrequencyScanner.resume）。
  void resumeScan();

  /// 实时频谱帧流。
  Stream<SpectrumFrame> get spectrumStream;

  /// 解调后的单声道音频流（Float32，[-1,1] 附近）；本控制器会同时喂给 sink。
  Stream<Float32List> get audioStream;
}

/// 真实收音机控制器。
class RadioController extends ChangeNotifier implements RadioApi {
  RadioController({
    PcmSink? sink,
    RtlTcpClient Function()? clientFactory,
    Future<Directory> Function()? recordingsDirProvider,
  })  : _sink = sink ?? NoOpSink(),
        _clientFactory = clientFactory ?? RtlTcpClient.new,
        _recordingsDirProvider = recordingsDirProvider {
    // 订阅自己的解调音频流，逐帧过静噪门后喂入 PCM sink。
    _audioSub = audioStream.listen(_onAudioFrame);
  }

  // ---------------------------------------------------- 依赖注入
  final PcmSink _sink;
  final RtlTcpClient Function() _clientFactory;

  /// 录音目录提供者（生产：RecordingStore.recordingsDir()）。未注入则不能开始录制。
  final Future<Directory> Function()? _recordingsDirProvider;

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

  /// 自动门限跟随去抖：上次已通知 UI 的门限取整值（dB）。门限按 ≥1dB 变化才
  /// 通知重建，避免每帧 rebuild；噪声底收敛后门限不再变化即停止通知。
  int _lastNotifiedSquelchTrunc = 999;

  /// 信号活动回调：真实观察到信号时触发一次（静噪门开门 / 扫描命中）。
  /// 由外壳（main.dart）注入，把真实观察写进信号活动日志；未连接/无观察时不触发。
  /// [source]：'squelch'=值守开门，'scan'=范围扫描命中。
  void Function({
    required int frequencyHz,
    required String mode,
    required double levelDbfs,
    String source,
  })? onSignalActivity;

  // ---------------------------------------------------- 范围扫描
  bool _scanning = false;
  bool _scanCancel = false;
  int? _scanHz;
  double _scanProgress = 0;

  /// 暂停闸：true 时冻结调谐与驻留计时；[_scanResume] 在恢复时 complete 以唤醒循环。
  bool _scanPaused = false;
  Completer<void>? _scanResume;

  /// 是否正在范围扫描。
  @override
  bool get scanning => _scanning;

  /// 扫描当前正在调谐的频率（Hz）；未扫描为 null。
  @override
  int? get scanHz => _scanHz;

  /// 扫描进度 0..1。
  @override
  double get scanProgress => _scanProgress;

  /// 扫描是否暂停（冻结调谐/计时）。
  @override
  bool get scanPaused => _scanPaused;

  // ---------------------------------------------------- 真实文件录制
  /// 当前录制会话（null = 未在录）。写盘走 [FileRecordingSink]（16-bit 小端 WAV）。
  FileRecordingSink? _recSink;
  bool _recording = false;

  @override
  bool get recording => _recording;

  /// 录制结束回调：由外壳（main.dart）注入，把结果 meta 加进 SettingsService 索引。
  /// 未注入时录制仍落盘、但不进列表（测试常用）。
  void Function(RecordingMeta meta)? onRecordingFinalized;

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
  bool get squelchAuto => _squelch.autoThreshold;
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

    // 连接成功后按约定顺序下发：采样率 → 增益模式 → 芯片AGC → 增益 → 频率。
    await client.setSampleRateHz(_sampleRateHz.round());
    await client.setGainMode(automatic: _autoGain);
    // RTL2832 芯片数字 AGC（0x08）：与调谐器增益模式联动，开 AGC 时一并打开。
    await client.setAgcMode(on: _autoGain);
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
    // 过门后的帧（开门=原帧，关门=静音帧）：既送外放，也送录制——录"听到的"。
    // 音量/静音只影响外放（PlatformPcmSink 内部量化），落盘永远是干净解调音频。
    final Float32List out;
    if (open) {
      out = frame;
    } else {
      if (_silence.length != frame.length) {
        _silence = Float32List(frame.length);
      }
      out = _silence;
    }
    _sink.write(out);
    _recSink?.write(out);
    // 自动门限跟随：门限随噪声底变化时通知 UI（滑杆回读），按 1dB 取整去抖。
    final int truncT = _squelch.thresholdDb.truncate();
    final bool autoMoved =
        _squelch.autoThreshold && truncT != _lastNotifiedSquelchTrunc;
    if (autoMoved) _lastNotifiedSquelchTrunc = truncT;
    // 仅在开门/关门跳变 或 自动门限移动时通知 UI，避免每帧抖动 rebuild。
    if (open != wasOpen || autoMoved) {
      // 真实信号活动：静噪门由关→开（连接后首次出声 / 值守命中过门限）。
      // 电平取静噪门平滑后的真实解调 RMS，频率/模式取当前真实调谐。
      if (open) {
        onSignalActivity?.call(
          frequencyHz: _freqHz,
          mode: _mode.name,
          levelDbfs: _squelch.levelDb,
          source: 'squelch',
        );
      }
      notifyListeners();
    }
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

  // ---------------------------------------------------- 录制（真实落盘 .wav）
  @override
  Future<void> startRecording() async {
    if (_recSink != null) return; // 已在录：重复调用安全忽略。
    if (_status != ConnectionStatus.connected) {
      throw StateError('未连接 rtl_tcp：无真实解调音频可录制');
    }
    final provider = _recordingsDirProvider;
    if (provider == null) {
      throw StateError('未配置录音目录（RecordingStore）');
    }
    final dir = await provider();
    // 固定一份开始时刻/频率/模式：FileRecordingSink 的 start 与 dispose 取同一份，
    // 保证 sidecar 文件名与索引 startedAtEpochMs 一致（不两次取时钟造成错位）。
    final started = RecordingMeta(
      startedAtEpochMs: DateTime.now().millisecondsSinceEpoch,
      frequencyHz: _freqHz,
      mode: _mode.name,
      deviceSource: RecordingSource.connected,
    );
    final rec = FileRecordingSink(dir: dir, metaFactory: () => started);
    await rec.start(sampleRateHz: audioSampleRateHz, channels: 1);
    _recSink = rec;
    _recording = true;
    notifyListeners();
  }

  @override
  Future<RecordingMeta?> stopRecording() async {
    final rec = _recSink;
    _recSink = null;
    if (!_recording) {
      _recording = false;
      notifyListeners();
      return null;
    }
    _recording = false;
    RecordingMeta? meta;
    if (rec != null) {
      await rec.dispose();
      meta = rec.result;
      if (meta != null) onRecordingFinalized?.call(meta);
    }
    notifyListeners();
    return meta;
  }

  // ---------------------------------------------------- 断开 / 参数
  @override
  Future<void> disconnect() async {
    _userDisconnected = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    // 断连前若在录制，先收尾落盘（避免断流留下半写的 .wav）。
    if (_recSink != null) {
      try {
        await stopRecording();
      } catch (_) {/* 收尾失败不阻断断连 */}
    }
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

  // ---------------------------------------------------- 范围扫描（真实调谐+真实量测）
  /// 开始范围扫描：从 [startHz] 到 [endHz] 按 [stepHz] 步进，每点驻留 [dwellMs]。
  ///
  /// 全程走真实链路：每点 [setFrequencyHz] 真实下发 rtl_tcp，驻留期间 IQ 解调更新
  /// 静噪门平滑电平（真实 RMS dBFS）；电平 ≥ [thresholdDbfs] 即真实命中，经
  /// [onSignalActivity]（source:'scan'）写进活动日志。未连接/已在扫描时直接返回。
  /// 取消由 [stopScan] 置位原子标志，驻留后即退出循环。
  @override
  Future<void> startScan({
    required int startHz,
    required int endHz,
    required int stepHz,
    required double thresholdDbfs,
    int dwellMs = AppTokens.scanDwellMsDefault,
    ScanDirection direction = ScanDirection.up,
    int hitHoldMs = 0,
  }) async {
    if (_status != ConnectionStatus.connected || _scanning) return;
    if (stepHz <= 0 || endHz <= startHz) return;

    _scanning = true;
    _scanCancel = false;
    _scanPaused = false;
    _scanResume = null;
    final points = ((endHz - startHz) ~/ stepHz) + 1;
    try {
      for (var i = 0; i < points; i++) {
        // 方向：up 从首点递增；down 从末点递减（对齐桌面 ScanDirection::Up/Down）。
        final idx = direction == ScanDirection.up ? i : (points - 1 - i);
        if (_scanCancel) break;
        await _scanFreezeGate();
        if (_scanCancel) break;

        final hz = startHz + idx * stepHz;
        _scanHz = hz;
        _scanProgress = points <= 1 ? 1.0 : idx / (points - 1);
        notifyListeners();

        // 真实调谐到该频点。
        await setFrequencyHz(hz);
        // 驻留：让 IQ 流过解调器，静噪门平滑电平收敛为该频点真实 RMS。
        // 切成小片逐片检查取消/暂停，使暂停真正冻结驻留计时。
        await _dwellInterruptible(dwellMs);
        if (_scanCancel) break;
        await _scanFreezeGate();
        if (_scanCancel) break;

        final level = _squelch.levelDb; // 真实量测电平（dBFS）
        if (level >= thresholdDbfs) {
          onSignalActivity?.call(
            frequencyHz: hz,
            mode: _mode.name,
            levelDbfs: level,
            source: 'scan',
          );
          // 命中停留：在命中频点额外驻留 hitHoldMs（对齐桌面 HitHoldMode::FixedMs）。
          if (hitHoldMs > 0) {
            await _dwellInterruptible(hitHoldMs);
            if (_scanCancel) break;
          }
        }
      }
    } finally {
      _scanning = false;
      _scanPaused = false;
      final c = _scanResume; // 收尾前唤醒可能正阻塞在暂停闸上的循环
      _scanResume = null;
      c?.complete();
      _scanHz = null;
      _scanProgress = 0;
      notifyListeners();
    }
  }

  /// 请求停止扫描（原子标志，驻留后即退出；同时唤醒暂停闸避免循环悬挂）。
  @override
  void stopScan() {
    _scanCancel = true;
    final c = _scanResume;
    _scanResume = null;
    _scanPaused = false;
    c?.complete();
  }

  /// 暂停扫描：冻结当前频点调谐与驻留计时（对齐桌面 FrequencyScanner.pause:88）。
  @override
  void pauseScan() {
    if (!_scanning || _scanPaused) return;
    _scanPaused = true;
    _scanResume = Completer<void>();
    notifyListeners();
  }

  /// 恢复扫描：从暂停处继续（对齐桌面 FrequencyScanner.resume:95）。
  @override
  void resumeScan() {
    if (!_scanPaused) return;
    _scanPaused = false;
    final c = _scanResume;
    _scanResume = null;
    c?.complete();
    notifyListeners();
  }

  /// 暂停闸：处于暂停时一直 await 到恢复或取消；否则立即返回。
  Future<void> _scanFreezeGate() async {
    while (_scanPaused && !_scanCancel) {
      await _scanResume?.future;
    }
  }

  /// 可中断驻留：把驻留切成 [AppTokens.scanDwellSliceMs] 小片，逐片检查取消/暂停。
  Future<void> _dwellInterruptible(int ms) async {
    var remaining = ms;
    while (remaining > 0 && !_scanCancel) {
      if (_scanPaused) {
        await _scanFreezeGate();
        continue;
      }
      final slice = remaining < AppTokens.scanDwellSliceMs
          ? remaining
          : AppTokens.scanDwellSliceMs;
      await Future<void>.delayed(Duration(milliseconds: slice));
      remaining -= slice;
    }
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
    // 同步 RTL2832 芯片数字 AGC（0x08）：开 AGC 时打开，切手动时关闭。
    await _client?.setAgcMode(on: on);
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
    // 用户手动拖滑杆 = 要手动控制：退出自动跟随，避免噪声底与拖杆打架
    //（对齐桌面 sliderPressed → 取消「自动门限」选中）。
    _squelch.autoThreshold = false;
    _squelch.thresholdDb = db.clamp(
      AppTokens.squelchThresholdMinDb,
      AppTokens.squelchThresholdMaxDb,
    );
    notifyListeners();
  }

  @override
  void setSquelchAuto(bool on) {
    _squelch.autoThreshold = on;
    if (on) {
      // 开启即按当前噪声底立即定一次门限（对齐桌面开启按钮时采样当前 floor）。
      _squelch.thresholdDb = (_squelch.noiseFloorDb + AppTokens.squelchAutoMarginDb)
          .clamp(AppTokens.squelchThresholdMinDb, AppTokens.squelchThresholdMaxDb);
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _userDisconnected = true;
    _reconnectTimer?.cancel();
    _audioSub.cancel();
    _spectrumTimer?.cancel();
    if (_recSink != null) {
      unawaited(stopRecording());
    }
    unawaited(_sink.dispose());
    _teardownClient();
    unawaited(_spectrumCtrl.close());
    unawaited(_audioCtrl.close());
    super.dispose();
  }
}
