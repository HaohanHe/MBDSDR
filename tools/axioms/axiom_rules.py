# SPDX-License-Identifier: MIT
"""公理 ID → 检查实际产出的规则名。

`survey` 产出的 ``Finding.rule`` 与反证自检产出的 ``result["rule"]`` 才是
可过滤的规则名；`docs/axioms.md` 里的「检查名」（如 ``readout_single_source``）
是概念名，不是实际值。本表是**唯一映射**：`--rule Axx` 与
`--self-test-only --rule Axx` 都走这里，避免两个入口各判一套。

A13 有两种产出：一般违规 ``A13``，以及「记录里没有唯一字段、无法判定」的
``A13-undecidable``；两者都必须归到 A13 名下。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple

#: 公理 ID → 该公理在工具里实际使用的规则名（含变体）。
AXIOM_RULES: Dict[str, Tuple[str, ...]] = {
    "A01": ("A01",),
    "A02": ("A02",),
    "A03": ("A03",),
    "A04": ("A04",),
    "A05": ("A05",),
    "A06": ("A06",),
    "A07": ("A07",),
    "A08": ("A08",),
    "A09": ("A09",),
    "A10": ("A10",),
    "A11": ("A11",),
    "A12": ("A12",),
    "A13": ("A13", "A13-undecidable"),
    "A14": ("A14",),
}

_AXIOM_RE = re.compile(r"^A\d{2}$")
_DIM_RE = re.compile(r"^D[1-8]$")


def is_axiom(selector: str) -> bool:
    return bool(_AXIOM_RE.match(selector.strip().upper()))


def is_dimension(selector: str) -> bool:
    return bool(_DIM_RE.match(selector.strip().upper()))


def rules_for(selector: str) -> Set[str]:
    """选择器 → 可接受的规则名集合。

    Axx 展开为 ``AXIOM_RULES``；Dx 只匹配维度本身（维度不展开成规则）；
    其他按规则名原样匹配。
    """
    sel = selector.strip().upper()
    if is_axiom(sel):
        return {r.upper() for r in AXIOM_RULES.get(sel, (sel,))}
    return {sel}


def select_findings(dims: Sequence, selector: str) -> Sequence:
    """按 ``--rule`` 过滤各维度 findings（原地裁剪，与旧行为一致）。

    - ``A01``：保留规则名属于 ``AXIOM_RULES["A01"]`` 的条目；
    - ``D1``：保留该维度，其余维度清空；
    - 其他：按规则名精确匹配（大小写不敏感）。
    """
    sel = selector.strip().upper()
    if is_axiom(sel):
        allowed = rules_for(sel)
        for dim in dims:
            dim.findings = [f for f in dim.findings if f.rule.upper() in allowed]
        return dims
    if is_dimension(sel):
        for dim in dims:
            if dim.id.upper() != sel:
                dim.findings = []
        return dims
    for dim in dims:
        dim.findings = [f for f in dim.findings if f.rule.upper() == sel]
    return dims


def select_selftest(results: Iterable[dict], selector: str) -> List[dict]:
    """按 ``--rule`` 过滤反证自检结果，口径与 :func:`select_findings` 相同。"""
    sel = selector.strip().upper()
    allowed = rules_for(sel)
    return [r for r in results if str(r.get("rule", "")).upper() in allowed]
