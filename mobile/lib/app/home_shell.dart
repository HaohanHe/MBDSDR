import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/satellite.dart';
import '../pages/chat_page.dart';
import '../pages/recordings_page.dart';
import '../pages/settings_page.dart';
import '../pages/sky_page.dart';
import '../pages/spectrum_page.dart';
import '../services/ai_client.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/connection_status_line.dart';
import 'ai_tools.dart';
import 'tokens.dart';

// ============================================================================
// App 外壳：响应式导航
//   * 宽屏（≥720）左侧 NavigationRail（≥900 自动展开）；
//   * 窄屏底部 BottomNavigationBar；
//   * IndexedStack 保活四个页面：频谱 / 天空 / AI / 设置。
// ============================================================================

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const List<NavigationRailDestination> _railDestinations =
      <NavigationRailDestination>[
    NavigationRailDestination(
      icon: Icon(Icons.waterfall_chart_outlined),
      selectedIcon: Icon(Icons.waterfall_chart),
      label: Text('频谱'),
    ),
    NavigationRailDestination(
      icon: Icon(Icons.public_outlined),
      selectedIcon: Icon(Icons.public),
      label: Text('天空'),
    ),
    NavigationRailDestination(
      icon: Icon(Icons.chat_bubble_outline),
      selectedIcon: Icon(Icons.chat_bubble),
      label: Text('AI'),
    ),
    NavigationRailDestination(
      icon: Icon(Icons.fiber_manual_record_outlined),
      selectedIcon: Icon(Icons.fiber_manual_record),
      label: Text('录音'),
    ),
    NavigationRailDestination(
      icon: Icon(Icons.settings_outlined),
      selectedIcon: Icon(Icons.settings),
      label: Text('设置'),
    ),
  ];

  static const List<BottomNavigationBarItem> _barItems =
      <BottomNavigationBarItem>[
    BottomNavigationBarItem(
      icon: Icon(Icons.waterfall_chart_outlined),
      activeIcon: Icon(Icons.waterfall_chart),
      label: '频谱',
    ),
    BottomNavigationBarItem(
      icon: Icon(Icons.public_outlined),
      activeIcon: Icon(Icons.public),
      label: '天空',
    ),
    BottomNavigationBarItem(
      icon: Icon(Icons.chat_bubble_outline),
      activeIcon: Icon(Icons.chat_bubble),
      label: 'AI',
    ),
    BottomNavigationBarItem(
      icon: Icon(Icons.fiber_manual_record_outlined),
      activeIcon: Icon(Icons.fiber_manual_record),
      label: '录音',
    ),
    BottomNavigationBarItem(
      icon: Icon(Icons.settings_outlined),
      activeIcon: Icon(Icons.settings),
      label: '设置',
    ),
  ];

  void _openSettings() {
    setState(() => _index = 4);
  }

  AppBar _buildAppBar() {
    return AppBar(
      title: Row(
        children: <Widget>[
          const Text('MBDSDR'),
          const SizedBox(width: AppTokens.spacingM),
          // 安静状态行：订阅 RadioController（ChangeNotifier），状态变化即
          // 重建。连接中/已连接/掉线等待重插/真实失败原因，均为克制小字。
          Consumer<RadioController>(
            builder: (BuildContext context, RadioController radio, _) {
              return ConnectionStatusLine(radio: radio);
            },
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    return Consumer<SettingsService>(
      builder: (BuildContext context, SettingsService settings, _) {
        final RadioController radio = context.read<RadioController>();
        final Station? station = settings.hasManualStation
            ? Station(
                lat: settings.stationLat!,
                lon: settings.stationLon!,
                alt: settings.stationAlt!,
              )
            : null;
        return IndexedStack(
          index: _index,
          children: <Widget>[
            SpectrumPage(
              controller: radio,
              rtlHost: settings.rtlHost,
              rtlPort: settings.rtlPort,
              bookmarks: settings.bookmarks,
              onAddBookmark: (name, hz, mode, bw) =>
                  settings.addBookmark(
                name: name,
                frequencyHz: hz,
                mode: mode,
                bandwidthHz: bw,
              ),
              onRemoveBookmark: (hz) => settings.removeBookmark(hz),
              fixedMarksHz: settings.fixedMarksHz,
              onAddFixedMark: () =>
                  settings.addFixedMarkHz(radio.freqHz.toDouble()),
              onRemoveFixedMark: (hz) => settings.removeFixedMarkHz(hz),
              onOpenSettings: _openSettings,
            ),
            SkyPage(manualStation: station, radio: radio),
            ChatPage(
              isConfigured: settings.apiKey.isNotEmpty,
              // 每次发送时新建 client：闭包实时读取当前 aiManualMode，
              // 模式切换后下一轮对话立即生效。
              clientFactory: () => AiClient(
                apiKey: settings.apiKey,
                model: settings.apiModel,
                tools: buildRadioTools(
                  radio,
                  manualMode: settings.aiManualMode,
                ),
              ),
              manualMode: settings.aiManualMode,
              onOpenSettings: _openSettings,
            ),
            RecordingsPage(radio: radio, settings: settings),
            SettingsPage(settings: settings, radio: radio),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final AppBar appBar = _buildAppBar();
        final Widget body = _buildBody();
        if (constraints.maxWidth >= 720) {
          return Scaffold(
            appBar: appBar,
            body: Row(
              children: <Widget>[
                NavigationRail(
                  extended: constraints.maxWidth >= 900,
                  selectedIndex: _index,
                  onDestinationSelected: (int i) =>
                      setState(() => _index = i),
                  destinations: _railDestinations,
                ),
                const VerticalDivider(
                  width: 1,
                  thickness: 1,
                  color: AppTokens.cardEdge,
                ),
                Expanded(child: body),
              ],
            ),
          );
        }
        return Scaffold(
          appBar: appBar,
          body: body,
          bottomNavigationBar: BottomNavigationBar(
            currentIndex: _index,
            onTap: (int i) => setState(() => _index = i),
            items: _barItems,
          ),
        );
      },
    );
  }
}
