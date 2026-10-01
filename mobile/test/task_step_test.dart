// 任务进度纯逻辑与渲染测试：状态分类（成功/失败/手动 gate/Error/running）、
// 摘要提取、视图渲染与空态。全部基于真实工具返回文本，不造假。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/models/task_step.dart';
import 'package:mbdsdr_mobile/widgets/task_progress.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('isFailureResult（真实结果分类）', () {
    test('{ok:true} 成功；{ok:false} 失败', () {
      expect(isFailureResult('{"ok": true, "frequency_hz": 145000000}'), false);
      expect(isFailureResult('{"ok":false,"error":"手动模式：未执行"}'), true);
    });
    test('手动模式 gated 也是失败（未执行）', () {
      expect(
        isFailureResult(
            '{"ok": false, "gated": true, "error": "手动模式：未执行 set_frequency"}'),
        true,
      );
    });
    test('Error 前缀视为失败；空串不视为失败', () {
      expect(isFailureResult('Error: tool "x" is not registered.'), true);
      expect(isFailureResult(''), false);
    });
  });

  group('TaskStep.fromCall', () {
    test('未 done → running；done+ok:true → success', () {
      final r = TaskStep.fromCall(
        tool: 'set_frequency',
        argumentsPreview: '{"frequency_mhz":145}',
        done: false,
        result: '',
      );
      expect(r.status, TaskStepStatus.running);

      final s = TaskStep.finished(
        tool: 'get_status',
        argumentsPreview: '{}',
        result: '{"ok": true, "frequency_hz": 145000000}',
      );
      expect(s.status, TaskStepStatus.success);
      expect(s.summary, isNotEmpty);
    });

    test('done+ok:false → failed，摘要取 error 字段', () {
      final f = TaskStep.finished(
        tool: 'set_gain',
        argumentsPreview: '{"gain_db":99}',
        result: '{"ok": false, "error": "增益超出范围 0–49.6 dB"}',
      );
      expect(f.status, TaskStepStatus.failed);
      expect(f.summary, contains('增益超出范围'));
    });
  });

  testWidgets('空步骤列表 → 不渲染任何内容（诚实空态）', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: TaskProgressView(steps: [])),
    ));
    expect(find.text('执行步骤'), findsNothing);
  });

  testWidgets('有步骤 → 渲染工具名与成功/失败状态', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: TaskProgressView(steps: [
          TaskStep.running(tool: 'set_frequency', argumentsPreview: '{"mhz":145}'),
          TaskStep.finished(
            tool: 'get_status',
            argumentsPreview: '{}',
            result: '{"ok": true}',
          ),
          TaskStep.finished(
            tool: 'set_gain',
            argumentsPreview: '{}',
            result: '{"ok": false, "error": "手动模式：未执行"}',
          ),
        ]),
      ),
    ));
    expect(find.text('执行步骤'), findsOneWidget);
    expect(find.text('set_frequency'), findsOneWidget);
    expect(find.text('get_status'), findsOneWidget);
    expect(find.text('set_gain'), findsOneWidget);
    expect(find.byIcon(Icons.check), findsOneWidget);
    expect(find.byIcon(Icons.close), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}
