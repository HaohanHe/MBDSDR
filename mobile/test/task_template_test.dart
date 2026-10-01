// 任务模板入口测试：草稿生成、参数中性（不硬编码台名）、chips 渲染与空态。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/task_template.dart';
import 'package:mbdsdr_mobile/widgets/task_templates_bar.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('内置模板（中性、参数可改）', () {
    final list = builtinTaskTemplates();

    test('至少两个模板：扫频找信号 / 监听目标频率', () {
      expect(list.length, greaterThanOrEqualTo(2));
      expect(list.any((t) => t.title.contains('扫频')), isTrue);
      expect(list.any((t) => t.title.contains('监听')), isTrue);
    });

    test('草稿提到 set_frequency/get_status 真实工具链，且标注可修改', () {
      for (final t in list) {
        final d = t.buildDraft();
        expect(d, contains('set_frequency'));
        expect(d, contains('修改'));
      }
    });

    test('草稿不含具体台名/呼号（中性占位）', () {
      // 不应出现常见台名占位；仅校验不含明显呼号式字符串（字母+数字结尾）。
      for (final t in list) {
        final d = t.buildDraft();
        expect(RegExp(r'[A-Z]{1,2}\d[A-Z]+').hasMatch(d), isFalse,
            reason: '草稿不应硬编码呼号: $d');
      }
    });
  });

  testWidgets('chips 渲染；点击回调草稿正文（不自动发送）', (tester) async {
    String? picked;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: TaskTemplatesBar(
          onSelect: (d) => picked = d,
        ),
      ),
    ));
    expect(find.text('扫频找信号'), findsOneWidget);
    await tester.tap(find.text('扫频找信号'));
    await tester.pump();
    expect(picked, isNotNull);
    expect(picked, contains('set_frequency'));
  });

  testWidgets('空模板清单 → 不渲染（诚实空态）', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: TaskTemplatesBar(onSelect: (_) {}, templates: const []),
      ),
    ));
    expect(find.text('任务模板（点击填入草稿，可再修改）'), findsNothing);
  });
}
