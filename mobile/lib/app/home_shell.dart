import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../audio/file_player.dart';
import '../l10n/gen/app_localizations.dart';
import '../models/recording.dart';
import '../models/satellite.dart';
import '../pages/activity_log_page.dart';
import '../pages/chat_page.dart';
import '../pages/help_page.dart';
import '../pages/recordings_page.dart';
import '../pages/settings_page.dart';
import '../pages/sky_page.dart';
import '../pages/spectrum_page.dart';
import '../services/ai_client.dart';
import '../services/chat_session_store.dart';
import '../services/radio_controller.dart';
import '../services/recording_store.dart';
import '../services/sat_passes_provider.dart';
import '../services/settings_service.dart';
import '../widgets/connection_status_line.dart';
import '../widgets/first_run_guide.dart';
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

  /// AI 工具 predict_passes 的数据源（Celestrak TLE + 本地 SGP4）。构造无副作用，
  /// 首次 predict 才联网；与天空页共用同一预测路径，不另造执行链。未注入前
  /// predict_passes 恒回「未配置」诚实空态（见 ai_tools.dart）。
  final SatPassesService _passesService = SatPassesService();

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

  /// 宽屏 NavigationRail 目的地（文案走 AppLocalizations，随 locale 重建）。
  List<NavigationRailDestination> _railDestinations(AppLocalizations l10n) {
    return <NavigationRailDestination>[
      NavigationRailDestination(
        icon: const Icon(Icons.waterfall_chart_outlined),
        selectedIcon: const Icon(Icons.waterfall_chart),
        label: Text(l10n.navSpectrum),
      ),
      NavigationRailDestination(
        icon: const Icon(Icons.public_outlined),
        selectedIcon: const Icon(Icons.public),
        label: Text(l10n.navSky),
      ),
      NavigationRailDestination(
        icon: const Icon(Icons.chat_bubble_outline),
        selectedIcon: const Icon(Icons.chat_bubble),
        label: Text(l10n.navAi),
      ),
      NavigationRailDestination(
        icon: const Icon(Icons.fiber_manual_record_outlined),
        selectedIcon: const Icon(Icons.fiber_manual_record),
        label: Text(l10n.navRecordings),
      ),
      NavigationRailDestination(
        icon: const Icon(Icons.history_outlined),
        selectedIcon: const Icon(Icons.history),
        label: Text(l10n.navActivity),
      ),
      NavigationRailDestination(
        icon: const Icon(Icons.settings_outlined),
        selectedIcon: const Icon(Icons.settings),
        label: Text(l10n.navSettings),
      ),
    ];
  }

  /// 窄屏底栏目的地（≤5，Material 3 建议）。
  /// 宽屏 NavigationRail 保留全部 6 项（Rail 不拥挤）；窄屏把「活动记录」
  /// 收进 AppBar 的历史入口，底栏只放 5 个高频项，避免图标+标签被压窄。
  /// [stackIndex] 指向 IndexedStack 子页下标，底栏位置与页下标解耦。
  List<({BottomNavigationBarItem item, int stackIndex})> _barDestinations(
    AppLocalizations l10n,
  ) {
    return <({BottomNavigationBarItem item, int stackIndex})>[
      (
        item: BottomNavigationBarItem(
          icon: const Icon(Icons.waterfall_chart_outlined),
          activeIcon: const Icon(Icons.waterfall_chart),
          label: l10n.navSpectrum,
        ),
        stackIndex: 0
      ),
      (
        item: BottomNavigationBarItem(
          icon: const Icon(Icons.public_outlined),
          activeIcon: const Icon(Icons.public),
          label: l10n.navSky,
        ),
        stackIndex: 1
      ),
      (
        item: BottomNavigationBarItem(
          icon: const Icon(Icons.chat_bubble_outline),
          activeIcon: const Icon(Icons.chat_bubble),
          label: l10n.navAi,
        ),
        stackIndex: 2
      ),
      (
        item: BottomNavigationBarItem(
          icon: const Icon(Icons.fiber_manual_record_outlined),
          activeIcon: const Icon(Icons.fiber_manual_record),
          label: l10n.navRecordings,
        ),
        stackIndex: 3
      ),
      (
        item: BottomNavigationBarItem(
          icon: const Icon(Icons.settings_outlined),
          activeIcon: const Icon(Icons.settings),
          label: l10n.navSettings,
        ),
        stackIndex: 5
      ),
    ];
  }

  void _openSettings() {
    setState(() => _index = 5);
  }

  AppBar _buildAppBar(AppLocalizations l10n) {
    return AppBar(
      title: Row(
        children: <Widget>[
          const Text('MBDSDR'),
          const SizedBox(width: AppTokens.spacingM),
          // 安静状态行：订阅 RadioController（ChangeNotifier），状态变化即
          // 重建。连接中/已连接/掉线等待重插/真实失败原因，均为克制小字。
          // Flexible：AppBar 动作区（帮助/历史图标）占位后，状态行让位并
          // 省略号截断，不再整体溢出（360px 窄屏实测 30px 溢出回归）。
          Flexible(
            child: Consumer<RadioController>(
              builder: (BuildContext context, RadioController radio, _) {
                return ConnectionStatusLine(radio: radio);
              },
            ),
          ),
        ],
      ),
      actions: <Widget>[
        // 「帮助」入口：应用内手册（sdroxide F1 模式），快速开始/页面导览/
        // 解调模式/FAQ/术语速查。随时可查，不占导航位。
        IconButton(
          icon: const Icon(Icons.help_outline),
          tooltip: l10n.tooltipHelp,
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const HelpPage()),
          ),
        ),
        // 「活动记录」入口：窄屏底栏收敛到 5 项后它不在底栏里，从 AppBar 进入
        // （宽屏 Rail 本就有该项，这里作为快捷入口，行为一致）。
        IconButton(
          icon: const Icon(Icons.history),
          tooltip: l10n.tooltipActivityLog,
          onPressed: () => setState(() => _index = 4),
        ),
      ],
    );
  }

  Widget _buildBody() {
    return Consumer<SettingsService>(
      builder: (BuildContext context, SettingsService settings, _) {
        final RadioController radio = context.read<RadioController>();
        // 首跑引导（OnAir 向导模式轻量版）：未看过时横幅挂 body 顶部，
        // 不阻断操作；「知道了」写持久化标记后永不再现。
        final bool showGuide = !settings.firstRunGuideSeen;
        // 回放注入：store 解析 wav 绝对路径，player 走 mbdsdr/audio 通道。
        // 任一缺失（导航单测）→ onPlay/onStop 为 null，录音页不渲染假播放按钮。
        final RecordingStore? store = _maybeRead<RecordingStore>();
        final FilePlayer? player = _maybeRead<FilePlayer>();
        // 多会话存储可选注入：未提供（如导航单测）时 ChatPage 退化为纯内存单会话。
        final ChatSessionStore? chatSessions = _maybeRead<ChatSessionStore>();
        Station? station;
        if (settings.hasManualStation) {
          station = Station(
            lat: settings.stationLat!,
            lon: settings.stationLon!,
            alt: settings.stationAlt!,
          );
        }
        final Widget stack = IndexedStack(
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
                  // 注入过境预测数据源与本站坐标：此前缺省导致 predict_passes
                  // 在生产环境恒回「未配置」，工具形同未接线。station 未配置时
                  // 传 null，工具自身回「未配置测站坐标」诚实空态。
                  passesService: _passesService,
                  station: station,
                  // 书签/录音索引的实时视图：每次发送现取（闭包捕获 settings），
                  // 保证 list_bookmarks / list_recordings 读到的是当前真值。
                  bookmarksProvider: () => settings.bookmarks,
                  recordingsProvider: () => settings.recordings,
                ),
              ),
              manualMode: settings.aiManualMode,
              onOpenSettings: _openSettings,
              store: chatSessions,
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
        if (!showGuide) return stack;
        return Column(
          children: <Widget>[
            FirstRunGuide(
              onOpenSettings: _openSettings,
              onDismiss: () => settings.firstRunGuideSeen = true,
            ),
            Expanded(child: stack),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final AppLocalizations l10n = AppLocalizations.of(context)!;
        final AppBar appBar = _buildAppBar(l10n);
        final Widget body = _buildBody();
        final List<({BottomNavigationBarItem item, int stackIndex})>
            barDestinations = _barDestinations(l10n);
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
                  destinations: _railDestinations(l10n),
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
                barDestinations.indexWhere((d) => d.stackIndex == _index),
            onTap: (int i) =>
                setState(() => _index = barDestinations[i].stackIndex),
            items: <BottomNavigationBarItem>[
              for (final d in barDestinations) d.item,
            ],
          ),
        );
      },
    );
  }
}
