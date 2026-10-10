// AI 工具能力清单（只读页）：对照桌面端 61 个 Agent 工具，逐项展示
// 名称 / 一句话说明 / read|write 标记，并诚实标注哪些是移动端当前已接入。
//
// 诚实性：本页**不构造 AiTool、不发请求、不执行任何动作**，纯只读展示。
// 「移动端已接入」仅指与移动端 buildRadioTools() 同名的工具；其余为桌面端能力，
// 移动端未实现，绝不冒充。无数据时（catalog 为空）走 EmptyState。
library;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../app/tool_catalog.dart';
import '../widgets/empty_state.dart';

class ToolsCatalogPage extends StatelessWidget {
  const ToolsCatalogPage({super.key, this.implemented = kMobileImplementedToolNames});

  /// 移动端当前已接入的工具名集合（默认 = buildRadioTools 同名子集）。
  final Set<String> implemented;

  @override
  Widget build(BuildContext context) {
    const all = kDesktopToolCatalog;
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('AI 工具清单', style: AppTokens.appTitle),
      ),
      body: all.isEmpty
          ? const EmptyState(
              icon: Icons.build_circle_outlined,
              title: '暂无工具目录',
              message: '工具清单数据为空。',
            )
          : ListView(
              children: [
                // 顶部诚实说明：这是桌面端参考目录，不是移动端能力声明。
                Padding(
                  padding: const EdgeInsets.all(AppTokens.spacingM),
                  child: Text(
                    '桌面端共 ${all.length} 个 Agent 工具；本页只读对照。'
                    '「已接入」表示与移动端 AI 当前注册工具同名；其余为桌面端能力，'
                    '移动端未实现。read=只读 / write=写动作（手动模式被拦截）。',
                    style: AppTokens.auxiliary,
                  ),
                ),
                const Divider(height: 1, color: AppTokens.cardEdge),
                for (final e in all) _ToolTile(entry: e, implemented: implemented.contains(e.name)),
              ],
            ),
    );
  }
}

class _ToolTile extends StatelessWidget {
  const _ToolTile({required this.entry, required this.implemented});

  final ToolCatalogEntry entry;
  final bool implemented;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      dense: true,
      leading: Icon(
        entry.write ? Icons.edit_note_outlined : Icons.visibility_outlined,
        size: AppTokens.iconSizeInlineLg,
        color: entry.write ? AppTokens.warning : AppTokens.success,
      ),
      title: Text(entry.name, style: AppTokens.mono),
      subtitle: Text(entry.description, style: AppTokens.auxiliary),
      trailing: Text(
        implemented ? '已接入' : (entry.write ? 'write' : 'read'),
        style: AppTokens.auxiliary.copyWith(
          color: implemented ? AppTokens.success : AppTokens.textSecondary,
        ),
      ),
    );
  }
}
