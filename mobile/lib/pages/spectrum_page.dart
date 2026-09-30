// 频谱页：头部控制区 + 紧凑仪器状态栏 + 统一频谱/瀑布显示。
//
// 诚实原则：未连接 rtl_tcp 时绝不画模拟峰，只给空态（复用 EmptyState）+ 连接按钮。
// 状态栏信息全部来自真实 controller / 最新帧：RSSI 取最新帧峰值 dBFS（真实测量）；
// 静噪（SQ）基于解调音频真实 RMS 电平门控，显示 OPEN/CLOSED/OFF，不造假读数。
// 构造契约固定：SpectrumPage({controller, rtlHost, rtlPort, onOpenSettings})。
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../dsp/fft_processor.dart';
import '../models/bookmark.dart';
import '../models/radio_state.dart';
import '../services/radio_controller.dart';
import '../widgets/empty_state.dart';
import '../widgets/spectrum_display.dart';

/// 由解调模式派生的信道带宽（Hz）。移动端带宽随模式而定，不单独控制。
int bandwidthForMode(DemodMode mode) => switch (mode) {
      DemodMode.nfm => 12500,
      DemodMode.wfm => 200000,
    };

class SpectrumPage extends StatelessWidget {
  /// 收音机接口（运行时为 ChangeNotifier，用于监听状态）。
  final RadioApi controller;

  /// rtl_tcp 主机（空串表示未配置，引导去设置页）。
  final String rtlHost;

  /// rtl_tcp 端口。
  final int rtlPort;

  /// 书签列表（名称/频率/模式/带宽），来自真实持久化，默认空。
  final List<Bookmark> bookmarks;

  /// 收藏当前频点：由页面收集名称后，连同真实频率/当前模式/带宽一起回调。
  final void Function(String name, int frequencyHz, String mode, int bandwidthHz)?
      onAddBookmark;

  /// 按频率移除书签。
  final ValueChanged<int>? onRemoveBookmark;

  /// 空 host 时引导用户去设置页。
  final VoidCallback? onOpenSettings;

  const SpectrumPage({
    super.key,
    required this.controller,
    required this.rtlHost,
    required this.rtlPort,
    this.bookmarks = const <Bookmark>[],
    this.onAddBookmark,
    this.onRemoveBookmark,
    this.onOpenSettings,
  });

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller as Listenable,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppTokens.bgMain,
          body: SafeArea(
            child: LayoutBuilder(
              builder: (context, constraints) {
                // 宽屏（横屏/平板）：控制区在左，显示区在右；否则竖排。
                final wide = constraints.maxWidth >= 600;
                final panelW =
                    (constraints.maxWidth * 0.30).clamp(240.0, 340.0);
                final panel = _ControlPanel(
                  controller: controller,
                  rtlHost: rtlHost,
                  rtlPort: rtlPort,
                  bookmarks: bookmarks,
                  onAddBookmark: onAddBookmark,
                  onRemoveBookmark: onRemoveBookmark,
                  onOpenSettings: onOpenSettings,
                );
                final display = _DisplayArea(
                  controller: controller,
                  rtlHost: rtlHost,
                  onOpenSettings: onOpenSettings,
                );
                return wide
                    ? Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          SizedBox(width: panelW, child: panel),
                          Expanded(child: display),
                        ],
                      )
                    : Column(
                        children: [
                          panel,
                          Expanded(child: display),
                        ],
                      );
              },
            ),
          ),
        );
      },
    );
  }
}

/// 显示区：根据连接状态给空态 / 转圈 / 真实频谱。
class _DisplayArea extends StatelessWidget {
  final RadioApi controller;
  final String rtlHost;
  final VoidCallback? onOpenSettings;

  const _DisplayArea({
    required this.controller,
    required this.rtlHost,
    required this.onOpenSettings,
  });

  @override
  Widget build(BuildContext context) {
    switch (controller.status) {
      case ConnectionStatus.disconnected:
      case ConnectionStatus.error:
        final hasHost = rtlHost.trim().isNotEmpty;
        return EmptyState(
          icon: Icons.waterfall_chart_outlined,
          title: controller.status == ConnectionStatus.error
              ? '连接错误'
              : '未连接 rtl_tcp',
          message: controller.status == ConnectionStatus.error
              ? '${controller.errorMessage ?? '未知错误'}\n当前无实时 IQ，不显示模拟数据。'
              : '当前无实时 IQ，不显示模拟数据。',
          actionLabel: hasHost ? null : '设置 rtl_tcp 地址',
          onAction: hasHost ? null : onOpenSettings,
        );
      case ConnectionStatus.connecting:
      case ConnectionStatus.reconnecting:
        return Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const CircularProgressIndicator(),
              const SizedBox(height: AppTokens.spacingM),
              Text(
                controller.status == ConnectionStatus.reconnecting
                    ? '设备断开，等待重插'
                    : '正在连接 rtl_tcp…',
                style: AppTokens.auxiliary,
              ),
            ],
          ),
        );
      case ConnectionStatus.connected:
        return _ConnectedBody(controller: controller);
    }
  }
}

/// 已连接：持有最新一帧，同时喂给状态栏（RSSI）与频谱显示。
class _ConnectedBody extends StatefulWidget {
  final RadioApi controller;
  const _ConnectedBody({required this.controller});

  @override
  State<_ConnectedBody> createState() => _ConnectedBodyState();
}

class _ConnectedBodyState extends State<_ConnectedBody> {
  StreamSubscription<SpectrumFrame>? _sub;
  SpectrumFrame? _frame;

  @override
  void initState() {
    super.initState();
    _sub = widget.controller.spectrumStream.listen((f) {
      if (mounted) setState(() => _frame = f);
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _StatusBar(controller: widget.controller, frame: _frame),
        Expanded(
          child: SpectrumDisplay(
            frame: _frame,
            channelBandwidthHz: bandwidthForMode(widget.controller.mode).toDouble(),
            onTapFrequency: (hz) =>
                widget.controller.setFrequencyHz(hz.round()),
          ),
        ),
      ],
    );
  }
}

/// 紧凑仪器状态栏：频率(mono) / 模式 / RSSI / 静噪 / 采样率 / 增益。
/// 信息诚实：无帧时 RSSI 显示 --；静噪未实现显示 —。
class _StatusBar extends StatelessWidget {
  final RadioApi controller;
  final SpectrumFrame? frame;
  const _StatusBar({required this.controller, required this.frame});

  /// RSSI = 最新帧峰值 dBFS（真实测量）。
  double? get _rssiDb {
    final f = frame;
    if (f == null) return null;
    var m = AppTokens.dbLowerDefault;
    for (final v in f.db) {
      if (v > m) m = v;
    }
    return m;
  }

  @override
  Widget build(BuildContext context) {
    final rssi = _rssiDb;
    return Container(
      width: double.infinity,
      color: AppTokens.bgBar,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingM,
        vertical: AppTokens.spacingS,
      ),
      child: Wrap(
        spacing: AppTokens.spacingM,
        runSpacing: AppTokens.spacingS,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          _StatusChip(
            '${(controller.freqHz / 1e6).toStringAsFixed(4)} MHz',
            primary: true,
          ),
          _StatusChip(controller.mode.label),
          _StatusChip(
            rssi == null ? 'RSSI --' : 'RSSI ${rssi.toStringAsFixed(0)} dB',
          ),
          // 静噪：基于解调音频真实 RMS 电平的门控开合，克制小字。
          _StatusChip(
            controller.squelchEnabled
                ? (controller.squelchOpen ? 'SQ OPEN' : 'SQ CLOSED')
                : 'SQ OFF',
          ),
          _StatusChip('${(controller.sampleRateHz / 1e6).toStringAsFixed(2)}Msps'),
          _StatusChip(
            controller.autoGain ? 'AGC' : 'G ${controller.gainDb.toStringAsFixed(1)}dB',
          ),
        ],
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final String text;
  final bool primary;
  const _StatusChip(this.text, {this.primary = false});

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppTokens.mono.copyWith(
        color: primary ? AppTokens.textPrimary : AppTokens.textSecondary,
        fontWeight: primary ? FontWeight.w600 : FontWeight.w500,
      ),
    );
  }
}

/// 头部控制区。
class _ControlPanel extends StatelessWidget {
  final RadioApi controller;
  final String rtlHost;
  final int rtlPort;
  final List<Bookmark> bookmarks;
  final void Function(String name, int frequencyHz, String mode, int bandwidthHz)?
      onAddBookmark;
  final ValueChanged<int>? onRemoveBookmark;
  final VoidCallback? onOpenSettings;

  const _ControlPanel({
    required this.controller,
    required this.rtlHost,
    required this.rtlPort,
    required this.bookmarks,
    required this.onAddBookmark,
    required this.onRemoveBookmark,
    this.onOpenSettings,
  });

  bool get _hasHost => rtlHost.trim().isNotEmpty;

  Future<void> _connectOrDisconnect(BuildContext context) async {
    if (controller.status == ConnectionStatus.connected ||
        controller.status == ConnectionStatus.connecting) {
      await controller.disconnect();
    } else {
      await controller.connect(rtlHost.trim(), rtlPort);
    }
  }

  Future<void> _promptFrequency(BuildContext context) async {
    final ctrl = TextEditingController(
      text: (controller.freqHz / 1e6).toStringAsFixed(4),
    );
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: const Text('输入频率 (MHz)', style: AppTokens.sectionTitle),
        content: TextField(
          controller: ctrl,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: AppTokens.mono,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
    if (result == null) return;
    final mhz = double.tryParse(result.trim());
    if (mhz != null) {
      await controller.setFrequencyHz((mhz * 1e6).round());
    }
  }

  /// 收藏当前频点：弹窗让用户命名（默认用频率占位，可改；不留空则存无名书签）。
  /// 自动带入真实当前频率 / 模式 / 带宽。绝不预存假台名。
  Future<void> _promptAddBookmark(BuildContext context) async {
    final preset = (controller.freqHz / 1e6).toStringAsFixed(4);
    final nameCtrl = TextEditingController(text: preset);
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: const Text('收藏此频点', style: AppTokens.sectionTitle),
        content: TextField(
          controller: nameCtrl,
          style: AppTokens.body,
          decoration: const InputDecoration(
            hintText: '名称（可留空，默认显示频率）',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, nameCtrl.text),
            child: const Text('收藏'),
          ),
        ],
      ),
    );
    if (result == null) return;
    onAddBookmark?.call(
      result.trim(),
      controller.freqHz,
      controller.mode.name,
      bandwidthForMode(controller.mode),
    );
  }

  @override
  Widget build(BuildContext context) {
    final connected = controller.status == ConnectionStatus.connected;
    return SingleChildScrollView(
      child: Container(
        margin: const EdgeInsets.all(AppTokens.spacingM),
        padding: const EdgeInsets.all(AppTokens.spacingM),
        decoration: AppTokens.cardDecoration(),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // 大频率读数 + 点按输入。
            Center(
              child: GestureDetector(
                onTap: () => _promptFrequency(context),
                child: Text(
                  '${(controller.freqHz / 1e6).toStringAsFixed(4)} MHz',
                  style: AppTokens.freqReadout,
                ),
              ),
            ),
            const SizedBox(height: AppTokens.spacingS),
            // NFM/WFM 分段。
            SegmentedButton<DemodMode>(
              segments: const [
                ButtonSegment(value: DemodMode.nfm, label: Text('NFM')),
                ButtonSegment(value: DemodMode.wfm, label: Text('WFM')),
              ],
              selected: {controller.mode},
              onSelectionChanged: (s) => controller.setMode(s.first),
            ),
            const SizedBox(height: AppTokens.spacingS),
            // 频率步进。
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                for (final b in [
                  ('-1M', controller.freqHz - 1000000),
                  ('-100k', controller.freqHz - 100000),
                  ('+100k', controller.freqHz + 100000),
                  ('+1M', controller.freqHz + 1000000),
                ])
                  Flexible(
                    child: _StepButton(
                      label: b.$1,
                      onTap: () => controller.setFrequencyHz(b.$2),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: AppTokens.spacingS),
            // 采样率下拉。
            Row(
              children: [
                const Text('采样率', style: AppTokens.auxiliary),
                const SizedBox(width: AppTokens.spacingM),
                Expanded(
                  child: DropdownButton<double>(
                    value: AppTokens.sampleRatesHz.contains(controller.sampleRateHz)
                        ? controller.sampleRateHz
                        : AppTokens.sampleRatesHz.first,
                    isExpanded: true,
                    dropdownColor: AppTokens.bgBar,
                    style: AppTokens.mono,
                    items: AppTokens.sampleRatesHz
                        .map((r) => DropdownMenuItem(
                              value: r,
                              child: Text('${(r / 1e6).toStringAsFixed(2)} Msps'),
                            ))
                        .toList(),
                    onChanged: (v) =>
                        v == null ? null : controller.setSampleRateHz(v),
                  ),
                ),
              ],
            ),
            // 自动增益开关。
            Row(
              children: [
                const Text('自动增益', style: AppTokens.auxiliary),
                const Spacer(),
                Switch(
                  value: controller.autoGain,
                  onChanged: (v) => controller.setAutoGain(v),
                ),
              ],
            ),
            // 增益滑杆。
            Row(
              children: [
                const Text('增益', style: AppTokens.auxiliary),
                Expanded(
                  child: Slider(
                    min: AppTokens.gainMinDb,
                    max: AppTokens.gainMaxDb,
                    value: controller.gainDb
                        .clamp(AppTokens.gainMinDb, AppTokens.gainMaxDb),
                    onChanged: controller.autoGain
                        ? null
                        : (v) => controller.setGainDb(v),
                  ),
                ),
                Text('${controller.gainDb.toStringAsFixed(1)} dB',
                    style: AppTokens.mono),
              ],
            ),
            // 音频快捷控制：静音 toggle + 紧凑音量滑块（未连接时置灰）。
            Row(
              children: [
                IconButton(
                  tooltip: controller.muted ? '取消静音' : '静音',
                  icon: Icon(
                    controller.muted ? Icons.volume_off : Icons.volume_up,
                  ),
                  onPressed: connected
                      ? () => controller.setMuted(!controller.muted)
                      : null,
                ),
                Expanded(
                  child: Slider(
                    min: 0,
                    max: 1,
                    value: controller.volume.clamp(0.0, 1.0),
                    onChanged: connected ? (v) => controller.setVolume(v) : null,
                  ),
                ),
                Text(
                  '${(controller.volume.clamp(0.0, 1.0) * 100).round()}',
                  style: AppTokens.mono,
                ),
              ],
            ),
            // 静噪门控：开关 + 真实电平门限滑杆（走 AppTokens 区间）。
            // 门限越高（越接近 0）越严；未连接时置灰。
            Row(
              children: [
                const Text('静噪', style: AppTokens.auxiliary),
                const Spacer(),
                Switch(
                  value: controller.squelchEnabled,
                  onChanged: connected ? controller.setSquelchEnabled : null,
                ),
              ],
            ),
            Row(
              children: [
                const Text('门限', style: AppTokens.auxiliary),
                Expanded(
                  child: Slider(
                    min: AppTokens.squelchThresholdMinDb,
                    max: AppTokens.squelchThresholdMaxDb,
                    value: controller.squelchThresholdDb.clamp(
                      AppTokens.squelchThresholdMinDb,
                      AppTokens.squelchThresholdMaxDb,
                    ),
                    onChanged: (connected && controller.squelchEnabled)
                        ? controller.setSquelchThresholdDb
                        : null,
                  ),
                ),
                Text(
                  '${controller.squelchThresholdDb.toStringAsFixed(0)} dB',
                  style: AppTokens.mono,
                ),
              ],
            ),
            const SizedBox(height: AppTokens.spacingS),
            // 连接 / 断开。
            FilledButton.icon(
              onPressed: _hasHost
                  ? () => _connectOrDisconnect(context)
                  : onOpenSettings,
              icon: Icon(connected ? Icons.link_off : Icons.rss_feed),
              label: Text(
                !_hasHost
                    ? '设置 rtl_tcp 地址'
                    : connected
                        ? '断开'
                        : '连接 ${rtlHost.trim()}:$rtlPort',
              ),
            ),
            // 收藏当前频点（命名弹窗）+ 已收藏列表：点击真实跳频并应用模式/带宽。
            // 书签默认空，不内置台名/位置。
            if (onAddBookmark != null) ...[
              const SizedBox(height: AppTokens.spacingS),
              Row(
                children: [
                  IconButton(
                    tooltip: '收藏此频点',
                    icon: const Icon(Icons.bookmark_add_outlined),
                    onPressed: () => _promptAddBookmark(context),
                  ),
                  const SizedBox(width: AppTokens.spacingS),
                  Expanded(
                    child: Text(
                      bookmarks.any((b) => b.frequencyHz == controller.freqHz)
                          ? '已收藏当前频点'
                          : '收藏当前频点',
                      style: AppTokens.auxiliary,
                    ),
                  ),
                ],
              ),
              if (bookmarks.isNotEmpty)
                Wrap(
                  spacing: AppTokens.spacingS,
                  runSpacing: AppTokens.spacingS,
                  children: [
                    for (final bm in bookmarks)
                      InputChip(
                        label: Text(
                          bm.displayLabel,
                          style: AppTokens.mono,
                        ),
                        // 跳频 + 模式可用时一并应用（移动端带宽随模式派生）。
                        onPressed: () {
                          controller.setFrequencyHz(bm.frequencyHz);
                          final m = bm.modeEnum;
                          if (m != null) controller.setMode(m);
                        },
                        onDeleted: onRemoveBookmark == null
                            ? null
                            : () => onRemoveBookmark!(bm.frequencyHz),
                      ),
                  ],
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _StepButton extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _StepButton({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: onTap,
      child: Text(label, style: AppTokens.mono),
    );
  }
}
