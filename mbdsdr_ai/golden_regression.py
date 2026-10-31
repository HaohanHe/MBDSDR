# SPDX-License-Identifier: MIT
"""P1-9 金标准回归网：通用 golden 断言机制。

能力拆解（218 条）P1-9：把"工具跑出来的结果和存档 golden 是否
一致"从各测试的散装 assert 收敛成一个原子能力，供测试与 agent
工作流共用。

  golden_assert(actual, golden, tolerances)
    actual    : 工具运行结果（dict，嵌套 list/dict/标量）；
    golden    : 已解析 dict 或 golden 文件路径（JSON）；
    tolerances: {点路径: 容差}，数值字段容差内视为匹配，未列出的
                数值字段要求严格相等；非数值字段一律严格相等。

设计要点（诚实纪律）：
  * golden 只读：本模块不提供任何覆写 golden 的能力——agent 不能
    靠改基线让回归"变绿"，基线变更必须是人类的显式决定；
  * 缺失/多余字段如实上报（missing_fields / extra_fields），
    不静默跳过新增输出；
  * NaN 语义：双方同 NaN 记 match（数值算法的中性结果），
    单方 NaN 记 mismatch；
  * 比对结论带逐字段差异清单（路径/期望值/实际值/差/容差），
    回归定位不用重跑。

点路径形如 "cost_final"、"taps_w.3"、"frames.0.snr_db"。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Union

__all__ = ["golden_assert", "register_tool_registry"]

_GOLDEN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "tests", "golden")


def _resolve_golden(golden: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(golden, dict):
        return golden
    if not isinstance(golden, str) or not golden:
        raise ValueError("golden 须为 dict 或非空路径")
    path = golden
    if not os.path.isabs(path):
        path = os.path.join(_GOLDEN_ROOT, path)
    if not os.path.isfile(path):
        raise ValueError(f"golden 文件不存在: {path}（新基线须人工建档，"
                         "本工具不自动创建/覆写 golden）")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"golden {path} 顶层须为 JSON 对象")
    return data


def _scalar_match(actual: Any, expected: Any,
                  path: str, tolerances: Dict[str, float],
                  mismatches: List[Dict[str, Any]],
                  ) -> bool:
    a_num = isinstance(actual, (int, float)) and not isinstance(actual, bool)
    e_num = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    if a_num and e_num:
        a_nan = math.isnan(float(actual))
        e_nan = math.isnan(float(expected))
        if a_nan or e_nan:
            ok = a_nan and e_nan
            if not ok:
                mismatches.append({"path": path, "expected": expected,
                                   "actual": actual, "kind": "nan_mismatch"})
            return ok
        diff = abs(float(actual) - float(expected))
        tol = tolerances.get(path, 0.0)
        if diff > tol:
            mismatches.append({"path": path, "expected": expected,
                               "actual": actual, "abs_diff": diff,
                               "tolerance": tol})
            return False
        return True
    ok = actual == expected
    if not ok:
        mismatches.append({"path": path, "expected": expected,
                           "actual": actual, "kind": "value_mismatch"})
    return ok


def _walk(actual: Any, expected: Any, prefix: str,
          tolerances: Dict[str, float],
          mismatches: List[Dict[str, Any]],
          missing: List[str], extra: List[str],
          checked: List[str],
          ) -> None:
    path = prefix
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            mismatches.append({"path": path or ".", "kind": "type_mismatch",
                               "expected": "dict", "actual": type(actual).__name__})
            return
        for key in expected:
            child = f"{path}.{key}" if path else key
            if key not in actual:
                missing.append(child)
            else:
                _walk(actual[key], expected[key], child, tolerances,
                      mismatches, missing, extra, checked)
        for key in actual:
            if key not in expected:
                child = f"{path}.{key}" if path else key
                extra.append(child)
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            mismatches.append({"path": path or ".", "kind": "type_mismatch",
                               "expected": "list", "actual": type(actual).__name__})
            return
        if len(actual) != len(expected):
            mismatches.append({"path": path or ".",
                               "kind": "length_mismatch",
                               "expected_len": len(expected),
                               "actual_len": len(actual)})
        for i, (a_item, e_item) in enumerate(zip(actual, expected)):
            _walk(a_item, e_item, f"{path}.{i}", tolerances,
                  mismatches, missing, extra, checked)
        return
    checked.append(path or ".")
    _scalar_match(actual, expected, path or ".", tolerances, mismatches)


def golden_assert(actual: Dict[str, Any],
                  golden: Union[str, Dict[str, Any]],
                  tolerances: Optional[Dict[str, float]] = None,
                  ) -> Dict[str, Any]:
    """工具结果 vs golden 基线对拍。

    返回 {match, fields_checked, n_mismatches, mismatches,
          missing_fields, extra_fields, tolerance_paths}。
    match = 无 mismatch 且无缺失字段（多余字段只报告不判负——
    新增输出是正常演进，但如实可见）。
    """
    if not isinstance(actual, dict):
        raise ValueError("actual 须为 dict（工具结果）")
    golden_d = _resolve_golden(golden)
    tol = {str(k): float(v) for k, v in (tolerances or {}).items()}
    for k, v in tol.items():
        if v < 0.0:
            raise ValueError(f"容差 {k} 为负")

    mismatches: List[Dict[str, Any]] = []
    missing: List[str] = []
    extra: List[str] = []
    checked: List[str] = []
    _walk(actual, golden_d, "", tol, mismatches, missing, extra, checked)
    return {
        "match": bool(not mismatches and not missing),
        "fields_checked": len(checked),
        "n_mismatches": len(mismatches),
        "mismatches": mismatches,
        "missing_fields": missing,
        "extra_fields": extra,
        "tolerance_paths": sorted(tol),
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-9 golden 回归断言积木（只读，golden 永不由工具覆写）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _assert(args):
        try:
            actual = args.get("actual")
            if not isinstance(actual, dict):
                raise ValueError("actual 须为工具结果 dict")
            tolerances = args.get("tolerances") or {}
            res = golden_assert(actual, args.get("golden", ""), tolerances)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"golden 断言失败: {clean_exc(e)}")
        ok = bool(res["match"])
        return ToolResult(
            success=ok,
            content=_json.dumps(res, ensure_ascii=False),
            data=res)

    registry.register(
        name="golden_assert",
        description=(
            "工具结果与 golden 基线对拍（点路径容差，NaN 双方中性），"
            "缺失/多余/差异字段逐一如实上报；golden 只读，基线变更"
            "须人工——agent 不能改基线让回归变绿。"),
        parameters={
            "type": "object",
            "properties": {
                "actual": {"type": "object", "description": "工具结果 dict"},
                "golden": {"type": ["string", "object"],
                           "description": "golden 文件名（tests/golden/ 下）"
                                          "或已解析 dict"},
                "tolerances": {"type": "object",
                               "description": "{点路径: 数值容差}"},
            },
            "required": ["actual", "golden"],
        },
        handler=_assert,
        category="testing")
