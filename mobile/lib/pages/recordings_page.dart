import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/radio_state.dart';
import '../models/recording.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/empty_state.dart';

// ============================================================================
// 录音页：录音列表 + 真实删除 +（可选）文件回放。
// ----------------------------------------------------------------------------
// 诚实性说明：
//   * 真实文件录制链路已就绪：FileRecordingSink 把解调音频量化为 16-bit 小端 PCM
//     写成本机 .wav，并产出 sidecar JSON；录制结束由上层 addRecording 进本列表。
//   * 无录制时列表恒为空态「暂无录音」，绝不预置假条目；
//   * 删除是真实的：从索引 + RecordingStore 配对删 .wav/.json；
//   * 回放：若注入了 [onPlay]/[onStop]/[playing]，每条可播放项显示播放/停止按钮；
//     未注入时不渲染假播放按钮（原生 AudioTrack/AVAudioEngine 未真机验证前诚实空）。
// ============================================================================

class RecordingsPage extends StatelessWidget {
  const RecordingsPage({
    super.key,
    required this.radio,
    required this.settings,
    this.onPlay,
    this.onStop,
    this.playing,
  });

  final RadioApi radio;
  final SettingsService settings;

  /// 注入后：有 wavFileName 的条目可点播放；未注入则不渲染播放按钮。
  final void Function(RecordingMeta meta)? onPlay;
  final void Function()? onStop;

  /// 当前正在播放的那条（用于切换图标）；未注入回放时为 null。
  final RecordingMeta? playing;

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
                        message: '真实录制的 .wav 会保存在本机；录制会话结束后，'
                            '按时间 / 频率 / 模式列在这里。',
                      )
                    : _RecordingList(
                        recordings: recordings,
                        onDelete: (m) => _confirmDelete(context, m),
                        onPlay: onPlay,
                        onStop: onStop,
                        playing: playing,
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
  const _RecordingList({
    required this.recordings,
    required this.onDelete,
    this.onPlay,
    this.onStop,
    this.playing,
  });

  final List<RecordingMeta> recordings;
  final void Function(RecordingMeta meta) onDelete;
  final void Function(RecordingMeta meta)? onPlay;
  final void Function()? onStop;
  final RecordingMeta? playing;

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
        final canPlay = onPlay != null &&
            onStop != null &&
            r.wavFileName != null &&
            r.wavFileName!.isNotEmpty;
        final isPlaying = playing != null &&
            playing!.startedAtEpochMs == r.startedAtEpochMs &&
            playing!.frequencyHz == r.frequencyHz;
        return ListTile(
          dense: true,
          leading: Icon(
            canPlay
                ? (isPlaying ? Icons.stop_circle_outlined : Icons.play_arrow)
                : Icons.audiotrack,
            color: canPlay ? AppTokens.success : AppTokens.accent,
            size: AppTokens.iconSizeInlineLg,
          ),
          onTap: canPlay
              ? () => isPlaying
                  ? onStop!()
                  : onPlay!(r)
              : null,
          title: Text('$mhz MHz · ${r.mode.toUpperCase()}',
              style: AppTokens.body),
          subtitle: Text(
            '${_fmtTime(r.startedAt)}'
            '${r.note.isEmpty ? '' : ' · ${r.note}'}'
            '${r.durationMs != null ? ' · ${(r.durationMs! / 1000).round()}s' : ''}',
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
