import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/radio_state.dart';
import '../models/recording.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/empty_state.dart';

// ============================================================================
// 录音页：录音列表（至少能看）。
// ----------------------------------------------------------------------------
// 诚实性说明：移动端目前没有真实文件录制路径——RecordingPcmSink 仅为
// 单测内存件，不写盘；RadioController 也没有录制开关。因此本页：
//   * 顶部只展示真实存在的「音频路由状态」（是否连上 rtl_tcp、静噪门是否
//     开门、当前频率/模式），不假装正在录制；
//   * 下方录音列表读取 SettingsService.recordings（就绪的持久化索引），
//     真机上恒为空态「暂无录音」，并明确说明暂未实现真实文件录制。
// 绝不预置假录音条目。
// ============================================================================

class RecordingsPage extends StatelessWidget {
  const RecordingsPage({
    super.key,
    required this.radio,
    required this.settings,
  });

  final RadioApi radio;
  final SettingsService settings;

  @override
  Widget build(BuildContext context) {
    final recordings = settings.recordings;
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('录音', style: AppTokens.appTitle),
      ),
      body: Column(
        children: [
          _AudioRoutingStatus(radio: radio),
          Expanded(
            child: recordings.isEmpty
                ? const EmptyState(
                    icon: Icons.fiber_manual_record_outlined,
                    title: '暂无录音',
                    message: '移动端暂未实现真实文件录制（当前仅实时收听）。'
                        '录制会话一旦产生，会按时间 / 频率 / 模式列在这里。',
                  )
                : _RecordingList(recordings: recordings),
          ),
        ],
      ),
    );
  }
}

/// 安静的真实音频路由状态行：连接 / 静噪门 / 当前频率模式。
class _AudioRoutingStatus extends StatelessWidget {
  const _AudioRoutingStatus({required this.radio});

  final RadioApi radio;

  @override
  Widget build(BuildContext context) {
    final conn = switch (radio.status) {
      ConnectionStatus.connected => '收音中',
      ConnectionStatus.connecting => '连接中',
      ConnectionStatus.reconnecting => '重连中',
      ConnectionStatus.error => '错误',
      ConnectionStatus.disconnected => '未连接',
    };
    final gate = radio.squelchEnabled
        ? (radio.squelchOpen ? '静噪开·有声' : '静噪关·静音')
        : '静噪旁路';
    final mhz = (radio.freqHz / 1e6).toStringAsFixed(4);
    return Container(
      margin: const EdgeInsets.all(AppTokens.spacingM),
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: AppTokens.cardDecoration(),
      child: Row(
        children: [
          Icon(
            radio.status == ConnectionStatus.connected
                ? Icons.radio_button_checked
                : Icons.radio_button_off,
            size: AppTokens.iconSizeInlineLg,
            color: radio.status == ConnectionStatus.connected
                ? AppTokens.success
                : AppTokens.textAt(AppTokens.textAlphaFaint),
          ),
          const SizedBox(width: AppTokens.spacingM),
          Text('$conn · $gate', style: AppTokens.auxiliary),
          const Spacer(),
          Text('$mhz MHz · ${radio.mode.label}', style: AppTokens.mono),
        ],
      ),
    );
  }
}

class _RecordingList extends StatelessWidget {
  const _RecordingList({required this.recordings});

  final List<RecordingMeta> recordings;

  String _fmtTime(DateTime t) {
    String two(int n) => n.toString().padLeft(2, '0');
    return '${t.year}-${two(t.month)}-${two(t.day)} '
        '${two(t.hour)}:${two(t.minute)}';
  }

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      itemCount: recordings.length,
      itemBuilder: (context, i) {
        final r = recordings[i];
        final mhz = (r.frequencyHz / 1e6).toStringAsFixed(4);
        return ListTile(
          dense: true,
          leading: const Icon(Icons.audiotrack,
              color: AppTokens.accent, size: AppTokens.iconSizeInlineLg),
          title: Text('$mhz MHz · ${r.mode.toUpperCase()}',
              style: AppTokens.body),
          subtitle: Text(
            '${_fmtTime(r.startedAt)}${r.note.isEmpty ? '' : ' · ${r.note}'}',
            style: AppTokens.auxiliary,
          ),
        );
      },
    );
  }
}
