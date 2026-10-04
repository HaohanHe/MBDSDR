import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../audio/file_player.dart';
import '../models/recording.dart';
import '../models/satellite.dart';
import '../pages/activity_log_page.dart';
import '../pages/chat_page.dart';
import '../pages/recordings_page.dart';
import '../pages/settings_page.dart';
import '../pages/sky_page.dart';
import '../pages/spectrum_page.dart';
import '../services/ai_client.dart';
import '../services/radio_controller.dart';
import '../services/recording_store.dart';
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

  /// 当前正在回放的录音（FilePlayer 状态镜像）；无回放/未注入时为 null。
  RecordingMeta? _playing;
  StreamSubscription<PlaybackState>? _playSub;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // 订阅原生回放完成事件，播完即清播放态（未注入 FilePlayer 时静默跳过）。
    _playSub?.cancel();
    final FilePlayer? player = _maybeRead();
    _playSub = player?.onState.listen((s) {
      if (s.completed && mounted) setState(() => _playing = null);
    });
  }

  @override
  void dispose() {
    _playSub?.cancel();
    super.dispose();
  }

  /// 可选读取：未注入 RecordingStore/FilePlayer（如外壳导航单测）时退化为
  /// null——录音页因此不渲染回放按钮，诚实空态、不假接。
  T? _maybeRead<T>() {
    try {
      return context.read<T>();
    } catch (_) {
      return null;
    }
  }

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
        // 回放注入：store 解析 wav 绝对路径，player 走 mbdsdr/audio 通道。
        // 任一缺失（导航单测）→ onPlay/onStop 为 null，录音页不渲染假播放按钮。
        final RecordingStore? store = _maybeRead<RecordingStore>();
        final FilePlayer? player = _maybeRead<FilePlayer>();
        Station? station;
        if (settings.hasManualStation) {
          station = Station(
            lat: settings.stationLat!,
            lon: settings.stationLon!,
            alt: settings.stationAlt!,
          );
        }
        return IndexedStack(
          index: _index,
          children: <Widget>[
            SpectrumPage(
              controller: radio,
              rtlHost: settings.rtlHost,
              rtlPort: settings.rtlPort,
              controlHubHost: settings.controlHubHost,
              controlHubPort: settings.controlHubPort,
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
            RecordingsPage(
              radio: radio,
              settings: settings,
              // 有 wavFileName 的条目才渲染播放按钮；store/player 齐全才可点。
              onPlay: (store != null && player != null)
                  ? (RecordingMeta meta) async {
                      final wav = meta.wavFileName;
                      if (wav == null || wav.isEmpty) return;
                      try {
                        final dir = await store.recordingsDir();
                        await player.startFile(
                          path: '${dir.path}/$wav',
                          sampleRate:
                              meta.sampleRateHz ?? RadioController.audioSampleRateHz,
                          channels: meta.channels ?? 1,
                        );
                        if (mounted) setState(() => _playing = meta);
                      } catch (_) {
                        // 原生回放未就绪/文件缺失：保持不播，不假出声。
                      }
                    }
                  : null,
              onStop: (player != null)
                  ? () async {
                      try {
                        await player.stopFile();
                      } catch (_) {}
                      if (mounted) setState(() => _playing = null);
                    }
                  : null,
              playing: _playing,
            ),
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
