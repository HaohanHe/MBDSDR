// ============================================================================
// tool_arguments_validator —— 工具 arguments 的 JSON Schema（draft-7）校验
// ----------------------------------------------------------------------------
// 背景：MiMo / 硅基流动官方明示「模型生成的 arguments 不保证是合法 JSON，
// 且可能虚构 schema 外的参数」。执行工具（驱动 SDR 硬件调谐）之前，必须先
// 按工具注册的 parameters schema 做白名单校验：
//   ① required 缺失；② 类型（number/integer/string/boolean）；
//   ③ enum 成员；④ minimum/maximum；⑤ schema.properties 之外的键一律拒绝。
// 校验失败不调用 execute，直接把结构化错误 JSON 作为 role=tool 的结果回灌，
// 让模型下一轮自纠（禁 eval、禁把未校验参数透传给硬件）。
//
// 纯函数、无网络/无 UI 依赖，可离线单测。
// ============================================================================

import 'dart:convert';

/// 校验结果。[ok]==false 时 [error] 为首条人类可读原因、[reasons] 为全部问题。
class ValidationResult {
  final bool ok;
  final String? error;
  final List<String> reasons;

  const ValidationResult.ok()
      : ok = true,
        error = null,
        reasons = const <String>[];

  ValidationResult.fail(List<String> reasons)
      : ok = false,
        reasons = reasons,
        error = reasons.isEmpty ? '参数校验失败' : '参数校验失败：${reasons.first}';
}

/// 把校验结果序列化成可直接作为 role=tool content 回灌的紧凑 JSON 文本。
/// 形状：`{"ok":false,"tool":"...","error":"...","reasons":[...]}`
String validationErrorToToolResult(String toolName, ValidationResult r) {
  return jsonEncode(<String, dynamic>{
    'ok': false,
    'tool': toolName,
    'error': r.error,
    'reasons': r.reasons,
  });
}

/// 校验已 jsonDecode 的 arguments map 是否符合工具的 parameters schema。
///
/// [args] 是模型给出的 arguments（必须已是 Map，调用方负责 parse）；
/// [parametersSchema] 即 AiTool.parameters（draft-7：type=object + properties + required）。
ValidationResult validateToolArguments(
  Map<String, dynamic> args,
  Map<String, dynamic> parametersSchema,
) {
  final Object? propsRaw = parametersSchema['properties'];
  final Map<Object?, Object?> props = propsRaw is Map
      ? propsRaw
      : const <Object?, Object?>{};

  final Object? requiredRaw = parametersSchema['required'];
  final List<Object?> required = requiredRaw is List
      ? requiredRaw
      : const <Object?>[];

  final List<String> reasons = <String>[];

  // ① 必填存在性
  for (final Object? key in required) {
    if (key is! String) continue;
    if (!args.containsKey(key)) {
      reasons.add('缺少必填参数: $key');
    }
  }

  // ②~⑤ 逐键：类型 / enum / min-max / 未知键白名单
  for (final MapEntry<Object?, Object?> entry in args.entries) {
    final Object? keyObj = entry.key;
    if (keyObj is! String) continue;
    final String key = keyObj;
    final Object? value = entry.value;

    final Object? specRaw = props[key];
    if (specRaw is! Map) {
      // schema 未声明的键 = 幻觉/越界参数，白名单拒绝。
      reasons.add('未知参数: $key');
      continue;
    }
    final Map<Object?, Object?> spec = specRaw;

    final Object? type = spec['type'];
    bool typeOk = true;
    if (type == 'number' || type == 'integer') {
      if (value is! num) {
        reasons.add('参数 $key 应为数字，实际为 ${_typeName(value)}');
        typeOk = false;
      }
    } else if (type == 'string') {
      if (value is! String) {
        reasons.add('参数 $key 应为字符串，实际为 ${_typeName(value)}');
        typeOk = false;
      }
    } else if (type == 'boolean') {
      if (value is! bool) {
        reasons.add('参数 $key 应为布尔，实际为 ${_typeName(value)}');
        typeOk = false;
      }
    }

    if (!typeOk) continue;

    // ③ enum 成员（字符串枚举或数值档位枚举都适用）
    final Object? enumRaw = spec['enum'];
    if (enumRaw is List && !enumRaw.contains(value)) {
      reasons.add(
          '参数 $key=$value 不在允许值内（${enumRaw.map((Object? e) => e.toString()).join(' / ')}）');
    }

    // ④ minimum / maximum（仅对数值有意义）
    if (value is num) {
      final Object? min = spec['minimum'];
      final Object? max = spec['maximum'];
      if (min is num && value < min) {
        reasons.add('参数 $key=$value 小于最小值 $min');
      }
      if (max is num && value > max) {
        reasons.add('参数 $key=$value 大于最大值 $max');
      }
    }
  }

  if (reasons.isEmpty) return const ValidationResult.ok();
  return ValidationResult.fail(reasons);
}

String _typeName(Object? v) => v == null ? 'null' : v.runtimeType.toString();
