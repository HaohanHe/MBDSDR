// 频谱页：头部控制区 + 统一频谱/瀑布显示。
//
// 诚实原则：未连接 rtl_tcp 时绝不画模拟峰，只给空态文案 + 连接按钮。
// 构造契约固定：SpectrumPage({controller, rtlHost, rtlPort, onOpenSettings})。
library;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../dsp/fft_processor.dart';
import '../models/radio_state.dart';
import '../services/radio_controller.dart';
import '../widgets/spectrum_display.dart';

class SpectrumPage extends StatelessWidget {
  /// 收音机接口（运行时为 ChangeNotifier，用于监听状态）。
  final RadioApi controller;

  /// rtl_tcp 主机（空串表示未配置，引导去设置页）。
  final String rtlHost;

  /// rtl_tcp 端口。
  final int rtlPort;

  /// 空 host 时引导用户去设置页。
  final VoidCallback? onOpenSettings;

  const SpectrumPage({
    super.key,
    required this.controller,
    required this.rtlHost,
    required this.rtlPort,
    this.onOpenSettings,
  });

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller as Listenable,
      builder: (context, _) {
        final isLandscape =
            MediaQuery.sizeOf(context).width > MediaQuery.sizeOf(context).height;
        final body = isLandscape
            ? Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SizedBox(
                    width: 320,
                    child: _ControlPanel(
                      controller: controller,
                      rtlHost: rtlHost,
                      rtlPort: rtlPort,
                      onOpenSettings: onOpenSettings,
                    ),
                  ),
                  Expanded(child: _DisplayArea(controller: controller)),
                ],
              )
            : Column(
                children: [
                  _ControlPanel(
                    controller: controller,
                    rtlHost: rtlHost,
                    rtlPort: rtlPort,
                    onOpenSettings: onOpenSettings,
                  ),
                  Expanded(child: _DisplayArea(controller: controller)),
                ],
              );
        return Scaffold(
          backgroundColor: AppTokens.bgMain,
          body: SafeArea(child: body),
        );
      },
    );
  }
}

/// 显示区：根据连接状态给空态 / 转圈 / 真实频谱。
class _DisplayArea extends StatelessWidget {
  final RadioApi controller;
  const _DisplayArea({required this.controller});

  @override
  Widget build(BuildContext context) {
    switch (controller.status) {
      case ConnectionStatus.disconnected:
      case ConnectionStatus.error:
        return _EmptyState(
          message: controller.status == ConnectionStatus.error
              ? '连接错误：${controller.errorMessage ?? '未知错误'}\n未连接 rtl_tcp，当前无实时 IQ。'
              : '未连接 rtl_tcp：当前无实时 IQ，不显示模拟数据。',
        );
      case ConnectionStatus.connecting:
        return const Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              CircularProgressIndicator(),
              SizedBox(height: AppTokens.spacingM),
              Text('正在连接 rtl_tcp…', style: AppTokens.auxiliary),
            ],
          ),
        );
      case ConnectionStatus.connected:
        return StreamBuilder<SpectrumFrame>(
          stream: controller.spectrumStream,
          builder: (context, snap) {
            return SpectrumDisplay(
              frame: snap.data,
              channelBandwidthHz: controller.mode == DemodMode.nfm
                  ? 12500
                  : 200000,
              onTapFrequency: (hz) => controller.setFrequencyHz(hz.round()),
            );
          },
        );
    }
  }
}

class _EmptyState extends StatelessWidget {
  final String message;
  const _EmptyState({required this.message});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTokens.spacingL),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.waterfall_chart_outlined,
                size: 40, color: AppTokens.textSecondary),
            const SizedBox(height: AppTokens.spacingM),
            Text(
              message,
              textAlign: TextAlign.center,
              style: AppTokens.auxiliary,
            ),
          ],
        ),
      ),
    );
  }
}

/// 头部控制区。
class _ControlPanel extends StatelessWidget {
  final RadioApi controller;
  final String rtlHost;
  final int rtlPort;
  final VoidCallback? onOpenSettings;

  const _ControlPanel({
    required this.controller,
    required this.rtlHost,
    required this.rtlPort,
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

  @override
  Widget build(BuildContext context) {
    final connected = controller.status == ConnectionStatus.connected;
    return Container(
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
                style: const TextStyle(
                  fontSize: 26,
                  fontWeight: FontWeight.w600,
                  color: AppTokens.textPrimary,
                  fontFamilyFallback: AppTokens.monoFallback,
                ),
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
              _StepButton(
                  label: '-1M',
                  onTap: () =>
                      controller.setFrequencyHz(controller.freqHz - 1000000)),
              _StepButton(
                  label: '-100k',
                  onTap: () =>
                      controller.setFrequencyHz(controller.freqHz - 100000)),
              _StepButton(
                  label: '+100k',
                  onTap: () =>
                      controller.setFrequencyHz(controller.freqHz + 100000)),
              _StepButton(
                  label: '+1M',
                  onTap: () =>
                      controller.setFrequencyHz(controller.freqHz + 1000000)),
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
        ],
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
