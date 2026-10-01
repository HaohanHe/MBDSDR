import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app/tokens.dart';
import '../models/activity_log.dart';
import '../services/settings_service.dart';
import '../widgets/empty_state.dart';

// ============================================================================
// 信号活动日志：自动追加的真实观察记录（频率/时间/电平/模式/来源）。
// ----------------------------------------------------------------------------
// 与书签严格区分：书签=用户手工收藏；本页=系统自动记录。
// 数据唯一来源：RadioController 静噪门开门（真实解调音频 RMS 过门限/出声），
// 由外壳写入 SettingsService.addActivity。无真实观察时诚实空态，不预置假信号。
// 操作：清空（带确认）。
// ============================================================================
class ActivityLogPage extends StatelessWidget {
  const ActivityLogPage({super.key});

  Future<void> _confirmClear(BuildContext context, SettingsService s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppTokens.bgBar,
        title: const Text('清空活动日志', style: AppTokens.sectionTitle),
        content: const Text(
          '将删除全部自动观察记录（不影响你的书签）。此操作不可撤销。',
          style: AppTokens.body,
        ),
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
    if (ok == true) {
      s.clearActivities();
    }
  }

  String _fmtTime(DateTime t) {
    String two(int n) => n.toString().padLeft(2, '0');
    return '${t.month}-${t.day} ${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('活动日志', style: AppTokens.appTitle),
        actions: [
          Consumer<SettingsService>(
            builder: (context, settings, _) => IconButton(
              tooltip: '清空',
              icon: const Icon(Icons.delete_sweep_outlined),
              onPressed: settings.activities.isEmpty
                  ? null
                  : () => _confirmClear(context, settings),
            ),
          ),
        ],
      ),
      body: Consumer<SettingsService>(
        builder: (context, settings, _) {
          final items = settings.activities;
          if (items.isEmpty) {
            return const EmptyState(
              icon: Icons.history,
              title: '暂无信号活动',
              message:
                  '连接 rtl_tcp 并值守后，静噪门真实开门（有信号过声）的时刻会自动记录在这里。'
                  '未连接或无信号时保持为空，不预置假记录。',
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingM),
            itemCount: items.length,
            separatorBuilder: (_, _) => const Divider(
              height: 1,
              color: AppTokens.cardEdge,
            ),
            itemBuilder: (context, i) => _ActivityTile(entry: items[i], fmt: _fmtTime),
          );
        },
      ),
    );
  }
}

class _ActivityTile extends StatelessWidget {
  const _ActivityTile({required this.entry, required this.fmt});

  final ActivityEntry entry;
  final String Function(DateTime) fmt;

  @override
  Widget build(BuildContext context) {
    final mhz = (entry.frequencyHz / 1e6).toStringAsFixed(4);
    final mode = entry.mode.isEmpty ? '—' : entry.mode.toUpperCase();
    return ListTile(
      dense: true,
      leading: const Icon(
        Icons.electrical_services,
        color: AppTokens.success,
        size: AppTokens.iconSizeInlineLg,
      ),
      title: Text(
        '$mhz MHz · $mode',
        style: AppTokens.body,
      ),
      subtitle: Text(
        '${fmt(entry.observedAt)} · ${entry.sourceLabel}'
        ' · ${entry.levelDbfs.toStringAsFixed(1)} dBFS',
        style: AppTokens.auxiliary,
      ),
    );
  }
}
