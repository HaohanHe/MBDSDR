import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'theme.dart';
import 'connection.dart';
import 'pages/spectrum_page.dart';
import 'pages/sky_page.dart';
import 'pages/chat_page.dart';

// MBDSDR 原生手机端入口
// 定位：把手机变成 AI 的"眼睛和手臂"——GPS 给位置、指南针给朝向、IMU 给姿态，
// AI 据此算出卫星仰角/方位角，反向指挥人把天线/手机转到正对卫星的方向。
//
// 架构：
//   main.dart              — 入口 + 路由 + 底部导航 + 连接设置
//   connection.dart        — WebSocket 客户端（自动重连 / 心跳 / 传感器采集 / 广播流）
//   theme.dart             — 日式低饱和主题
//   pages/spectrum_page.dart — 实时频谱（FFT 柱状图）
//   pages/sky_page.dart      — 卫星指向引导（罗盘 + 箭头 + 仰角）
//   pages/chat_page.dart     — AI 对话

void main() => runApp(const MbdsdrApp());

class MbdsdrApp extends StatelessWidget {
  const MbdsdrApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => ConnectionService(),
      child: MaterialApp(
        title: 'MBDSDR',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light,
        home: const HomePage(),
      ),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _currentIndex = 0;
  final _urlCtrl = TextEditingController(text: 'ws://192.168.1.10:8765');

  @override
  void dispose() {
    _urlCtrl.dispose();
    super.dispose();
  }

  void _onItemTapped(int index) {
    setState(() => _currentIndex = index);
  }

  void _openConnectionSheet() {
    final conn = context.read<ConnectionService>();
    _urlCtrl.text = conn.serverUrl;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppTheme.card,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 20,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 20,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '连接设置',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
                color: AppTheme.text,
              ),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _urlCtrl,
              decoration: const InputDecoration(
                labelText: '电脑端 WebSocket 地址',
                hintText: 'ws://192.168.1.10:8765',
                prefixIcon: Icon(Icons.cable, color: AppTheme.primary),
              ),
              keyboardType: TextInputType.url,
            ),
            const SizedBox(height: 12),
            Consumer<ConnectionService>(
              builder: (_, conn, __) {
                final connected = conn.isConnected;
                return Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: connected
                            ? null
                            : () {
                                conn.serverUrl = _urlCtrl.text.trim();
                                conn.connect();
                                Navigator.pop(ctx);
                              },
                        icon: Icon(connected
                            ? Icons.check_circle
                            : Icons.sync_alt),
                        label: Text(connected ? '已连接' : '连接'),
                      ),
                    ),
                    if (connected) ...[
                      const SizedBox(width: 8),
                      OutlinedButton.icon(
                        onPressed: () {
                          conn.disconnect();
                          Navigator.pop(ctx);
                        },
                        icon: const Icon(Icons.link_off),
                        label: const Text('断开'),
                      ),
                    ],
                  ],
                );
              },
            ),
            const SizedBox(height: 8),
            Consumer<ConnectionService>(
              builder: (_, conn, __) => Text(
                '状态：${_stateText(conn.state)}  |  定位：${conn.positionFix}',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppTheme.textSecondary,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _stateText(ConnState s) {
    switch (s) {
      case ConnState.connected:
        return '已连接';
      case ConnState.connecting:
        return '连接中...';
      case ConnState.reconnecting:
        return '重连中...';
      case ConnState.disconnected:
        return '未连接';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<ConnectionService>(
      builder: (context, conn, _) {
        final pages = [
          SpectrumPage(connection: conn),
          SkyPage(connection: conn),
          ChatPage(connection: conn),
        ];
        return Scaffold(
          appBar: AppBar(
            title: Row(
              children: [
                const Text('MBDSDR',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(width: 10),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: conn.isConnected
                        ? AppTheme.success.withOpacity(0.15)
                        : AppTheme.danger.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        conn.isConnected
                            ? Icons.circle
                            : Icons.circle_outlined,
                        size: 8,
                        color: conn.isConnected
                            ? AppTheme.success
                            : AppTheme.danger,
                      ),
                      const SizedBox(width: 4),
                      Text(
                        conn.isConnected ? '在线' : '离线',
                        style: TextStyle(
                          fontSize: 11,
                          color: conn.isConnected
                              ? AppTheme.success
                              : AppTheme.danger,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            actions: [
              IconButton(
                icon: const Icon(Icons.settings_ethernet),
                tooltip: '连接设置',
                onPressed: _openConnectionSheet,
              ),
            ],
          ),
          body: IndexedStack(
            index: _currentIndex,
            children: pages,
          ),
          bottomNavigationBar: BottomNavigationBar(
            currentIndex: _currentIndex,
            onTap: _onItemTapped,
            items: const [
              BottomNavigationBarItem(
                icon: Icon(Icons.bar_chart),
                label: '频谱',
              ),
              BottomNavigationBarItem(
                icon: Icon(Icons.explore),
                label: '指向',
              ),
              BottomNavigationBarItem(
                icon: Icon(Icons.chat_bubble_outline),
                label: 'AI 对话',
              ),
            ],
          ),
        );
      },
    );
  }
}
