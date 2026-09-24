import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:geolocator/geolocator.dart';
import 'package:flutter_compass/flutter_compass.dart';
import 'package:sensors_plus/sensors_plus.dart';

// ============================================================================
// MBDSDR 移动端 WebSocket 连接服务
// ============================================================================
// 职责：
//   1. 管理与电脑端的 WebSocket 连接（自动重连 + 心跳 ping/pong）
//   2. 采集 GPS / 指南针 / IMU 并周期上报 telemetry
//   3. 将收到的 fft / satellite_passes / ai_command / pointing 通过
//      广播 Stream 暴露给各页面
//
// 协议（与电脑端约定）：
//   上行（手机 -> 电脑）：
//     {"type":"handshake","source":"mobile","payload":{device,capabilities}}
//     {"type":"telemetry","kind":"fix|heading|imu","payload":{...}}
//     {"type":"chat","payload":{"text":"..."}}
//     {"type":"ping"}
//   下行（电脑 -> 手机）：
//     {"type":"fft","payload":{"bins":[...],"center_freq_mhz":98.5,"span_mhz":4.0}}
//     {"type":"satellite_passes","payload":{"passes":[{name,max_el,azimuth,rise_time}]}}
//     {"type":"pointing","payload":{"satellite":"ISS","azimuth":135.0,"elevation":45.0}}
//     {"type":"ai_command","payload":{"text":"..."}}
//     {"type":"pong"}
// ============================================================================

enum ConnState { disconnected, connecting, connected, reconnecting }

class ConnectionService extends ChangeNotifier {
  // ---- 可配置 ----
  String serverUrl;
  final Duration reconnectInterval;
  final Duration heartbeatInterval;
  final Duration heartbeatTimeout;

  // ---- 内部状态 ----
  WebSocketChannel? _ws;
  StreamSubscription<dynamic>? _wsSub;
  Timer? _reconnectTimer;
  Timer? _heartbeatTimer;
  Timer? _heartbeatWatchdog;
  bool _manualDisconnect = false;

  ConnState _state = ConnState.disconnected;
  ConnState get state => _state;
  bool get isConnected => _state == ConnState.connected;

  // 传感器读数（供 sky_page 等直接读取）
  double _heading = 0;
  double get heading => _heading;
  String _positionFix = '定位中...';
  String get positionFix => _positionFix;

  // ---- 广播流（页面用 StreamBuilder 订阅）----
  final StreamController<List<double>> _fftCtrl =
      StreamController<List<double>>.broadcast();
  Stream<List<double>> get fftStream => _fftCtrl.stream;

  final StreamController<List<dynamic>> _passesCtrl =
      StreamController<List<dynamic>>.broadcast();
  Stream<List<dynamic>> get passesStream => _passesCtrl.stream;

  final StreamController<Map<String, dynamic>> _pointingCtrl =
      StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get pointingStream => _pointingCtrl.stream;

  final StreamController<String> _aiCmdCtrl =
      StreamController<String>.broadcast();
  Stream<String> get aiCommandStream => _aiCmdCtrl.stream;

  // 最近一帧 FFT 元数据
  double _centerFreqMhz = 98.5;
  double get centerFreqMhz => _centerFreqMhz;
  double _spanMhz = 4.0;
  double get spanMhz => _spanMhz;

  // 最近一次指向目标
  Map<String, dynamic>? _lastPointing;
  Map<String, dynamic>? get lastPointing => _lastPointing;

  // 日志（调试用）
  final List<String> _log = [];
  List<String> get logLines => List.unmodifiable(_log);
  static const int _maxLog = 200;

  ConnectionService({
    this.serverUrl = 'ws://192.168.1.10:8765',
    this.reconnectInterval = const Duration(seconds: 3),
    this.heartbeatInterval = const Duration(seconds: 15),
    this.heartbeatTimeout = const Duration(seconds: 5),
  });

  // ======================================================================
  // 连接管理
  // ======================================================================

  Future<void> connect() async {
    if (_state == ConnState.connecting || _state == ConnState.connected) return;
    _manualDisconnect = false;
    _setState(ConnState.connecting);
    _logLine('正在连接 $serverUrl ...');
    try {
      _ws = WebSocketChannel.connect(Uri.parse(serverUrl));
      await _ws!.ready;
      _wsSub = _ws!.stream.listen(
        _onMessage,
        onDone: _onDone,
        onError: _onError,
      );
      _sendHandshake();
      _setState(ConnState.connected);
      _logLine('已连接');
      _startSensors();
      _startHeartbeat();
    } catch (e) {
      _logLine('连接失败: $e');
      _scheduleReconnect();
    }
  }

  void disconnect() {
    _manualDisconnect = true;
    _teardown();
    _setState(ConnState.disconnected);
    _logLine('已断开');
  }

  void _teardown() {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _heartbeatWatchdog?.cancel();
    _heartbeatWatchdog = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _wsSub?.cancel();
    _wsSub = null;
    _ws?.sink.close();
    _ws = null;
  }

  void _scheduleReconnect() {
    if (_manualDisconnect) return;
    _teardown();
    _setState(ConnState.reconnecting);
    _logLine('${reconnectInterval.inSeconds}s 后重连...');
    _reconnectTimer = Timer(reconnectInterval, () {
      connect();
    });
  }

  void _onDone() {
    _logLine('连接关闭');
    _scheduleReconnect();
  }

  void _onError(Object e) {
    _logLine('WS 错误: $e');
    _scheduleReconnect();
  }

  void _setState(ConnState s) {
    _state = s;
    notifyListeners();
  }

  // ======================================================================
  // 心跳
  // ======================================================================

  void _startHeartbeat() {
    _heartbeatTimer?.cancel();
    _heartbeatWatchdog?.cancel();
    _heartbeatTimer = Timer.periodic(heartbeatInterval, (_) {
      _sendRaw(jsonEncode({'type': 'ping'}));
      _heartbeatWatchdog?.cancel();
      _heartbeatWatchdog = Timer(heartbeatTimeout, () {
        _logLine('心跳超时，触发重连');
        _scheduleReconnect();
      });
    });
  }

  // ======================================================================
  // 消息收发
  // ======================================================================

  void _sendHandshake() {
    _sendRaw(jsonEncode({
      'type': 'handshake',
      'source': 'mobile',
      'payload': {
        'device': 'flutter',
        'capabilities': ['gps', 'compass', 'imu', 'camera'],
      },
    }));
  }

  void sendTelemetry(String kind, Map<String, dynamic> data) {
    if (!isConnected) return;
    _sendRaw(jsonEncode({'type': 'telemetry', 'kind': kind, 'payload': data}));
  }

  void sendChat(String text) {
    if (text.trim().isEmpty) return;
    _sendRaw(jsonEncode({'type': 'chat', 'payload': {'text': text}}));
  }

  void _sendRaw(String raw) {
    if (_ws == null) return;
    try {
      _ws!.sink.add(raw);
    } catch (e) {
      _logLine('发送失败: $e');
    }
  }

  void _onMessage(dynamic raw) {
    if (raw is! String) return;
    Map<String, dynamic> m;
    try {
      m = jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      return;
    }
    switch (m['type']) {
      case 'pong':
        _heartbeatWatchdog?.cancel();
        _heartbeatWatchdog = null;
        break;
      case 'fft':
        final payload = m['payload'] as Map<String, dynamic>?;
        if (payload != null) {
          final bins = (payload['bins'] as List?)
                  ?.map((e) => (e as num).toDouble())
                  .toList() ??
              const <double>[];
          if (payload['center_freq_mhz'] != null) {
            _centerFreqMhz = (payload['center_freq_mhz'] as num).toDouble();
          }
          if (payload['span_mhz'] != null) {
            _spanMhz = (payload['span_mhz'] as num).toDouble();
          }
          if (bins.isNotEmpty) _fftCtrl.add(bins);
        }
        break;
      case 'satellite_passes':
        final passes =
            (m['payload']?['passes'] as List?) ?? const <dynamic>[];
        _passesCtrl.add(passes);
        break;
      case 'pointing':
        final payload = m['payload'] as Map<String, dynamic>?;
        if (payload != null) {
          _lastPointing = payload;
          _pointingCtrl.add(payload);
        }
        break;
      case 'ai_command':
        final text = (m['payload']?['text'] as String?) ?? '';
        if (text.isNotEmpty) _aiCmdCtrl.add(text);
        break;
      default:
        break;
    }
  }

  // ======================================================================
  // 传感器采集（GPS / 指南针 / IMU）
  // ======================================================================

  StreamSubscription<Position>? _posSub;
  StreamSubscription<CompassEvent>? _compassSub;
  StreamSubscription<AccelerometerEvent>? _accelSub;

  void _startSensors() {
    _posSub?.cancel();
    _compassSub?.cancel();
    _accelSub?.cancel();

    _posSub = Geolocator.getPositionStream().listen((pos) {
      _positionFix =
          '${pos.latitude.toStringAsFixed(4)}, ${pos.longitude.toStringAsFixed(4)}'
          '  alt ${pos.altitude.toStringAsFixed(0)}m';
      notifyListeners();
      sendTelemetry('fix', {
        'lat': pos.latitude,
        'lon': pos.longitude,
        'alt': pos.altitude,
      });
    }, onError: (e) => _logLine('GPS 错误: $e'));

    _compassSub = FlutterCompass.events?.listen((evt) {
      _heading = evt.heading ?? 0;
      notifyListeners();
      sendTelemetry('heading', {'deg': _heading});
    });

    _accelSub = accelerometerEventStream().listen((e) {
      sendTelemetry('imu', {'x': e.x, 'y': e.y, 'z': e.z});
    });
  }

  // ======================================================================
  // 日志
  // ======================================================================

  void _logLine(String s) {
    final ts = DateTime.now().toIso8601String().substring(11, 19);
    _log.insert(0, '$ts  $s');
    if (_log.length > _maxLog) _log.removeLast();
    notifyListeners();
  }

  @override
  void dispose() {
    _manualDisconnect = true;
    _teardown();
    _posSub?.cancel();
    _compassSub?.cancel();
    _accelSub?.cancel();
    _fftCtrl.close();
    _passesCtrl.close();
    _pointingCtrl.close();
    _aiCmdCtrl.close();
    super.dispose();
  }
}
