import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/radio_state.dart';
import '../models/recording.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/empty_state.dart';

// ============================================================================
// 录音页：录音列表 + 真实删除。
// ----------------------------------------------------------------------------
// 诚实性说明：移动端目前没有真实文件录制路径——RecordingPcmSink 仅为
// 单测内存件，不写盘；RadioController 也没有录制开关。因此本页：
//   * 顶部只展示真实存在的「音频路由状态」（是否连上 rtl_tcp、静噪门是否
//     开门、当前频率/模式），不假装正在录制；
//   * 下方录音列表读取 SettingsService.recordings（就绪的持久化索引），
//     真机上恒为空态「暂无录音」，并明确说明暂未实现真实文件录制；
//   * 删除是真实的：从持久化索引移除单条 / 清空全部，均带确认；不提供假回放
//     按钮（无录音文件、无文件播放器，回放暂不真实，故不渲染）。
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

  Future<void> _confirmDelete(BuildContext context, RecordingMeta meta) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: const Text('删除这条录音', style: AppTokens.sectionTitle),
        content: const Text('将从录音索引中移除这条记录。此操作不可撤销。',
            style: AppTokens.body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除', style: TextStyle(color: AppTokens.danger)),
          ),
        ],
      ),
    );
    if (ok == true) settings.removeRecording(meta);
  }

  Future<void> _confirmClear(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: const Text('清空录音索引', style: AppTokens.sectionTitle),
        content: const Text('将移除全部录音索引记录。此操作不可撤销。',
            style: AppTokens.body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清空', style: TextStyle(color: AppTokens.danger)),
          ),
        ],
      ),
    );
    if (ok == true) settings.clearRecordings();
  }

  @override
  Widget build(BuildContext context) {
    // 监听 settings：删除/清空索引后立即重建列表。
    return ListenableBuilder(
      listenable: settings,
      builder: (context, _) {
        final recordings = settings.recordings;
        return Scaffold(
          backgroundColor: AppTokens.bgMain,
          appBar: AppBar(
            backgroundColor: AppTokens.bgBar,
            title: const Text('录音', style: AppTokens.appTitle),
            actions: [
              IconButton(
                tooltip: '清空',
                icon: const Icon(Icons.delete_sweep_outlined),
                onPressed: recordings.isEmpty
                    ? null
                    : () => _confirmClear(context),
              ),
            ],
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
                    : _RecordingList(
                        recordings: recordings,
                        onDelete: (m) => _confirmDelete(context, m),
                      ),
              ),
            ],
          ),
        );
      },
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
  const _RecordingList({required this.recordings, required this.onDelete});

  final List<RecordingMeta> recordings;
  final void Function(RecordingMeta meta) onDelete;

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
          trailing: IconButton(
            tooltip: '删除',
            icon: const Icon(Icons.delete_outline,
                size: AppTokens.iconSizeInlineLg),
            onPressed: () => onDelete(r),
          ),
        );
      },
    );
  }
}
