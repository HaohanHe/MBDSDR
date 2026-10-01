import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/satellite.dart';
import '../pages/activity_log_page.dart';
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
//   * 宽屏（≥720）左侧 NavigationRail（≥900 自动展开）：6 个目的地全保留；
//   * 窄屏底部 BottomNavigationBar：收敛为 5 个高频项，「活动记录」收进
//     AppBar 历史入口（Material 3 建议底栏 ≤5）；
//   * IndexedStack 保活全部子页：频谱 / 天空 / AI / 录音 / 活动 / 设置。
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
      icon: Icon(Icons.history_outlined),
      selectedIcon: Icon(Icons.history),
      label: Text('活动'),
    ),
    NavigationRailDestination(
      icon: Icon(Icons.settings_outlined),
      selectedIcon: Icon(Icons.settings),
      label: Text('设置'),
    ),
  ];

  /// 窄屏底栏目的地（≤5，Material 3 建议）。
  /// 宽屏 NavigationRail 保留全部 6 项（Rail 不拥挤）；窄屏把「活动记录」
  /// 收进 AppBar 的历史入口，底栏只放 5 个高频项，避免图标+标签被压窄。
  /// [stackIndex] 指向 IndexedStack 子页下标，底栏位置与页下标解耦。
  static const List<({BottomNavigationBarItem item, int stackIndex})> _barDestinations =
      <({BottomNavigationBarItem item, int stackIndex})>[
    (item: BottomNavigationBarItem(
      icon: Icon(Icons.waterfall_chart_outlined),
      activeIcon: Icon(Icons.waterfall_chart),
      label: '频谱',
    ), stackIndex: 0),
    (item: BottomNavigationBarItem(
      icon: Icon(Icons.public_outlined),
      activeIcon: Icon(Icons.public),
      label: '天空',
    ), stackIndex: 1),
    (item: BottomNavigationBarItem(
      icon: Icon(Icons.chat_bubble_outline),
      activeIcon: Icon(Icons.chat_bubble),
      label: 'AI',
    ), stackIndex: 2),
    (item: BottomNavigationBarItem(
      icon: Icon(Icons.fiber_manual_record_outlined),
      activeIcon: Icon(Icons.fiber_manual_record),
      label: '录音',
    ), stackIndex: 3),
    (item: BottomNavigationBarItem(
      icon: Icon(Icons.settings_outlined),
      activeIcon: Icon(Icons.settings),
      label: '设置',
    ), stackIndex: 5),
  ];

  void _openSettings() {
    setState(() => _index = 5);
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
      actions: <Widget>[
        // 「活动记录」入口：窄屏底栏收敛到 5 项后它不在底栏里，从 AppBar 进入
        // （宽屏 Rail 本就有该项，这里作为快捷入口，行为一致）。
        IconButton(
          icon: const Icon(Icons.history),
          tooltip: '活动记录',
          onPressed: () => setState(() => _index = 4),
        ),
      ],
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
              onMarkChanged: (oldHz, newHz) {
                // 拖动/键盘微调：先删旧再落新，立即持久化。
                settings.removeFixedMarkHz(oldHz);
                settings.addFixedMarkHz(newHz);
              },
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
            const ActivityLogPage(),
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
            // 底栏位置与 IndexedStack 页下标解耦；当前页不在底栏（活动记录）
            // 时 indexWhere 返回 -1，不高亮任何项。
            currentIndex:
                _barDestinations.indexWhere((d) => d.stackIndex == _index),
            onTap: (int i) =>
                setState(() => _index = _barDestinations[i].stackIndex),
            items: <BottomNavigationBarItem>[
              for (final d in _barDestinations) d.item,
            ],
          ),
        );
      },
    );
  }
}
