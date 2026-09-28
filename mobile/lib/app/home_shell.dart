import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/radio_state.dart';
import '../models/satellite.dart';
import '../pages/chat_page.dart';
import '../pages/settings_page.dart';
import '../pages/sky_page.dart';
import '../pages/spectrum_page.dart';
import '../services/ai_client.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/status_chip.dart';
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
      icon: Icon(Icons.settings_outlined),
      activeIcon: Icon(Icons.settings),
      label: '设置',
    ),
  ];

  void _openSettings() {
    setState(() => _index = 3);
  }

  AppBar _buildAppBar() {
    return AppBar(
      title: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Text('MBDSDR'),
          const SizedBox(width: AppTokens.spacingM),
          Consumer<RadioController>(
            builder: (BuildContext context, RadioController radio, _) {
              final Color chipColor = switch (radio.status) {
                ConnectionStatus.connected => AppTokens.success,
                ConnectionStatus.reconnecting => AppTokens.warning,
                _ => AppTokens.danger,
              };
              return StatusChip(
                color: chipColor,
                text: switch (radio.status) {
                  ConnectionStatus.connected => 'rtl_tcp 已连接',
                  ConnectionStatus.connecting => '连接中…',
                  ConnectionStatus.reconnecting => '重连中…',
                  ConnectionStatus.error => '连接失败',
                  ConnectionStatus.disconnected => 'rtl_tcp 未连接',
                },
              );
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
              onOpenSettings: _openSettings,
            ),
            SkyPage(manualStation: station),
            ChatPage(
              isConfigured: settings.apiKey.isNotEmpty,
              clientFactory: () => AiClient(
                apiKey: settings.apiKey,
                model: settings.apiModel,
                tools: buildRadioTools(radio),
              ),
              onOpenSettings: _openSettings,
            ),
            SettingsPage(settings: settings),
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
