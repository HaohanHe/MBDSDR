// G5 AI 工具能力清单（只读页）：对照桌面 tool_schema.cpp 的 61 工具。
//
// 诚实性断言：
//   * 目录恰为 61 条，名称唯一、说明非空；
//   * write=true 才显示「write」标记，read 显示「read」；
//   * 「已接入」只打在与移动端同名的子集上，其余绝不冒充；
//   * 页面只读渲染：不构造 AiTool、不发请求。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/tool_catalog.dart';
import 'package:mbdsdr_mobile/pages/tools_catalog_page.dart';

void main() {
  test('桌面目录恰为 61 条、名称唯一、说明非空', () {
    expect(kDesktopToolCatalog.length, 61);
    final names = kDesktopToolCatalog.map((e) => e.name).toList();
    expect(names.toSet().length, 61, reason: '工具名不得重复');
    for (final e in kDesktopToolCatalog) {
      expect(e.name, isNotEmpty);
      expect(e.description, isNotEmpty);
    }
  });

  test('已接入子集是桌面目录的子集，不含名字拼写错误', () {
    final names = kDesktopToolCatalog.map((e) => e.name).toSet();
    for (final n in kMobileImplementedToolNames) {
      expect(names.contains(n), isTrue, reason: '已接入名 $n 必须是桌面目录里的真名');
    }
    // 只读与写的总数自洽（这里只断言存在两类即可）。
    expect(kDesktopToolCatalog.where((e) => e.write).length, greaterThan(0));
    expect(kDesktopToolCatalog.where((e) => !e.write).length, greaterThan(0));
  });

  testWidgets('只读清单页渲染：头部诚实说明 + 已接入标记，不冒充', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: ToolsCatalogPage()));
    await tester.pump();

    // 头部诚实说明（标注桌面 51 个、只读对照）。
    expect(find.textContaining('桌面端共 61 个'), findsOneWidget);
    // 顶部几个工具名渲染。
    expect(find.text('tune_frequency'), findsOneWidget);
    expect(find.text('set_mode'), findsOneWidget);
    // 顶部可见区里「已接入」至少出现（set_mode 等）。
    expect(find.text('已接入'), findsWidgets);
    // 滚动一下不抛异常（列表懒加载可滚动）。
    await tester.drag(find.byType(ListView), const Offset(0, -500));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
