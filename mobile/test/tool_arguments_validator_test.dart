// ============================================================================
// tool_arguments_validator 离线单测 —— 纯函数，无网络。
// 覆盖：必填缺失 / 类型错 / enum 外 / 数值越界 / 幻觉键 / 合法通过 / 空参数工具。
// ============================================================================

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/services/tool_arguments_validator.dart';

void main() {
  // 一个贴近 set_frequency 子集的 schema：
  //   frequency_hz: integer, required, min 24e6, max 1700e6
  //   mode: string, enum [nfm, wfm]
  //   auto: boolean
  const Map<String, dynamic> schema = <String, dynamic>{
    'type': 'object',
    'properties': <String, dynamic>{
      'frequency_hz': <String, dynamic>{
        'type': 'integer',
        'minimum': 24000000,
        'maximum': 1700000000,
      },
      'mode': <String, dynamic>{
        'type': 'string',
        'enum': <String>['nfm', 'wfm'],
      },
      'auto': <String, dynamic>{'type': 'boolean'},
    },
    'required': <String>['frequency_hz'],
  };

  group('validateToolArguments', () {
    test('合法参数通过', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'frequency_hz': 145000000, 'mode': 'nfm'},
        schema,
      );
      expect(r.ok, isTrue);
      expect(r.reasons, isEmpty);
    });

    test('必填缺失被拒', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'mode': 'nfm'},
        schema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons, contains('缺少必填参数: frequency_hz'));
    });

    test('类型错误被拒（number 给了 string）', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'frequency_hz': '145000000'},
        schema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons.join(' '), contains('应为数字'));
    });

    test('类型错误（boolean 给了 num）', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'frequency_hz': 145000000, 'auto': 1},
        schema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons.join(' '), contains('应为布尔'));
    });

    test('enum 外被拒', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'frequency_hz': 145000000, 'mode': 'usb'},
        schema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons.join(' '), contains('不在允许值'));
    });

    test('数值越界被拒（小于 minimum / 大于 maximum）', () {
      final ValidationResult low = validateToolArguments(
        <String, dynamic>{'frequency_hz': 1000},
        schema,
      );
      expect(low.ok, isFalse);
      expect(low.reasons.join(' '), contains('小于最小值'));

      final ValidationResult high = validateToolArguments(
        <String, dynamic>{'frequency_hz': 3000000000},
        schema,
      );
      expect(high.ok, isFalse);
      expect(high.reasons.join(' '), contains('大于最大值'));
    });

    test('幻觉键（schema 外参数）被拒', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{
          'frequency_hz': 145000000,
          'totally_made_up': 42,
        },
        schema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons, contains('未知参数: totally_made_up'));
    });

    test('空参数工具（properties={}）接受空对象、拒绝任意键', () {
      const Map<String, dynamic> emptySchema = <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{},
      };
      expect(validateToolArguments(<String, dynamic>{}, emptySchema).ok, isTrue);
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'x': 1},
        emptySchema,
      );
      expect(r.ok, isFalse);
      expect(r.reasons, contains('未知参数: x'));
    });

    test('数值档位 enum（采样率）', () {
      const Map<String, dynamic> rateSchema = <String, dynamic>{
        'type': 'object',
        'properties': <String, dynamic>{
          'sample_rate_hz': <String, dynamic>{
            'type': 'number',
            'enum': <double>[1.024e6, 2.048e6],
          },
        },
        'required': <String>['sample_rate_hz'],
      };
      expect(
        validateToolArguments(<String, dynamic>{'sample_rate_hz': 2.048e6}, rateSchema).ok,
        isTrue,
      );
      expect(
        validateToolArguments(<String, dynamic>{'sample_rate_hz': 5.0e6}, rateSchema).ok,
        isFalse,
      );
    });
  });

  group('validationErrorToToolResult', () {
    test('产出可作为 tool 回灌的 JSON 文本', () {
      final ValidationResult r = validateToolArguments(
        <String, dynamic>{'frequency_hz': 1, 'ghost': 2},
        schema,
      );
      final String text = validationErrorToToolResult('set_frequency', r);
      final Map<String, dynamic> decoded = jsonDecode(text) as Map<String, dynamic>;
      expect(decoded['ok'], isFalse);
      expect(decoded['tool'], 'set_frequency');
      expect(decoded['error'], isA<String>());
      final List<dynamic> reasons = decoded['reasons']! as List<dynamic>;
      expect(reasons, isNotEmpty);
    });
  });
}
