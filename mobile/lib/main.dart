import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:geolocator/geolocator.dart';
import 'package:flutter_compass/flutter_compass.dart';
import 'package:sensors_plus/sensors_plus.dart';

// MBDSDR 原生手机端
// 定位：把手机变成 AI 的"眼睛和手臂"——GPS 给位置、指南针给朝向、IMU 给姿态，
// AI 据此算出卫星仰角/方位角，反向指挥人把天线/手机转到正对卫星的方向。

// 日式低饱和主题（与桌面端一致）
const Color kBg = Color(0xFFF5F3EF);
const Color kInk = Color(0xFF5B7B8C);
const Color kAccent = Color(0xFFC4845C);

void main() => runApp(const MbdsdrApp());

class MbdsdrApp extends StatelessWidget {
  const MbdsdrApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MBDSDR',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        scaffoldBackgroundColor: kBg,
        colorScheme: ColorScheme.fromSeed(seedColor: kInk),
        fontFamily: 'MiSans',
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _urlCtrl = TextEditingController(text: 'ws://192.168.1.10:8765');
  WebSocketChannel? _ws;
  bool _connected = false;
  String _log = '';
  String _fix = '定位中...';
  double _heading = 0;
  List<dynamic> _passes = [];
  final List<String> _chat = [];

  void _logLine(String s) {
    setState(() => _log = '${DateTime.now().second}s  $s\n$_log');
  }

  Future<void> _connect() async {
    try {
      _ws = WebSocketChannel.connect(Uri.parse(_urlCtrl.text.trim()));
      await _ws!.ready;
      _ws!.stream.listen(_onMsg, onDone: _onDisconnect, onError: (e) {
        _logLine('WS 错误: $e');
        _onDisconnect();
      });
      _ws!.sink.add(jsonEncode({
        'type': 'handshake',
        'source': 'mobile',
        'payload': {
          'device': 'flutter',
          'capabilities': ['gps', 'compass', 'imu', 'camera'],
        },
      }));
      setState(() => _connected = true);
      _logLine('已连接 ${_urlCtrl.text}');
      _startSensors();
    } catch (e) {
      _logLine('连接失败: $e');
    }
  }

  void _onDisconnect() {
    setState(() => _connected = false);
    _logLine('连接断开');
  }

  void _onMsg(dynamic raw) {
    final m = jsonDecode(raw as String) as Map<String, dynamic>;
    switch (m['type']) {
      case 'satellite_passes':
        setState(() => _passes = m['payload']?['passes'] ?? []);
        break;
      case 'ai_command':
        final cmd = m['payload']?['text'] ?? '';
        setState(() => _chat.add('AI: $cmd'));
        break;
      default:
        _logLine('收: ${m['type']}');
    }
  }

  // 上报位置/朝向/姿态，AI 据此指挥
  void _startSensors() {
    Geolocator.getPositionStream().listen((pos) {
      _fix = '${pos.latitude.toStringAsFixed(4)}, ${pos.longitude.toStringAsFixed(4)}'
          '  alt ${pos.altitude.toStringAsFixed(0)}m';
      _sendTelemetry('fix', {
        'lat': pos.latitude, 'lon': pos.longitude, 'alt': pos.altitude,
      });
    });
    FlutterCompass.events?.listen((evt) {
      _heading = evt.heading ?? 0;
      _sendTelemetry('heading', {'deg': _heading});
    });
    accelerometerEventStream().listen((e) {
      _sendTelemetry('imu', {'x': e.x, 'y': e.y, 'z': e.z});
    });
  }

  void _sendTelemetry(String kind, Map<String, dynamic> data) {
    if (!_connected) return;
    _ws?.sink.add(jsonEncode({'type': 'telemetry', 'kind': kind, 'payload': data}));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('MBDSDR'),
        backgroundColor: kBg,
        elevation: 0,
        foregroundColor: kInk,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 连接卡
          Row(children: [
            Expanded(
              child: TextField(
                controller: _urlCtrl,
                decoration: const InputDecoration(labelText: '电脑端地址'),
              ),
            ),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: _connected ? null : _connect,
              child: Text(_connected ? '已连' : '连接'),
            ),
          ]),
          const SizedBox(height: 16),
          // 状态卡
          Card(
            child: ListTile(
              leading: Icon(Icons.circle,
                  color: _connected ? Colors.green : Colors.red),
              title: Text(_fix),
              subtitle: Text('朝向 ${_heading.toStringAsFixed(0)}°'),
            ),
          ),
          const SizedBox(height: 12),
          const Text('卫星过境（AI 指挥指向）',
              style: TextStyle(fontWeight: FontWeight.bold, color: kInk)),
          ..._passes.map((p) => ListTile(
                dense: true,
                leading: const Icon(Icons.satellite_alt, color: kAccent),
                title: Text(p['name']?.toString() ?? 'SAT'),
                subtitle: Text(
                    '仰角 ${p['max_el']}°  方位 ${p['azimuth']}°  ${p['rise_time']}'),
              )),
          const Divider(),
          const Text('AI 指令',
              style: TextStyle(fontWeight: FontWeight.bold, color: kInk)),
          ..._chat.map((c) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text(c),
              )),
          const SizedBox(height: 12),
          Container(
            height: 160,
            padding: const EdgeInsets.all(8),
            color: Colors.black12,
            child: SingleChildScrollView(
              child: Text(_log, style: const TextStyle(fontSize: 11)),
            ),
          ),
        ],
      ),
    );
  }
}
