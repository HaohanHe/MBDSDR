# SPDX-License-Identifier: MIT
"""`--rule Axx` 在测绘与反证自检两个入口必须走同一张映射。

反证：把 ``AXIOM_RULES["A13"]`` 里的 ``A13-undecidable`` 删掉，
``test_mapping_covers_every_selftest_case`` 与
``test_two_entrypoints_select_same_ruleset`` 必红。

运行：python -m pytest tests/test_axioms_rule_filter.py -v
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.axiom_rules import (  # noqa: E402
    AXIOM_RULES,
    select_findings,
    select_selftest,
)
from tools.axioms.model import Finding  # noqa: E402
from tools.axioms.selftest import CASES  # noqa: E402


class _Dim:
    """只保留过滤用到的两个字段，避免测试依赖 survey 的采集结果。"""

    def __init__(self, dim_id, rules):
        self.id = dim_id
        self.findings = [
            Finding(rule, "x.py", 1, "", "", "") for rule in rules
        ]


def _synthetic_dims():
    axiom_rules = [r for rules in AXIOM_RULES.values() for r in rules]
    return [
        _Dim("D1", ["D1-cycle", "D1-test-reverse"]),
        _Dim("D2", axiom_rules + ["D2-multi-producer"]),
    ]


def _synthetic_selftest():
    return [{"rule": c.match or c.rule, "name": c.name, "ok": True}
            for c in CASES]


def test_mapping_covers_every_selftest_case():
    for case in CASES:
        if not case.rule.startswith("A"):
            continue
        assert case.rule in AXIOM_RULES, f"公理 {case.rule} 不在映射表里"
        assert (case.match or case.rule) in AXIOM_RULES[case.rule], \
            f"{case.rule} 的用例 {case.name!r} 产出 {case.match or case.rule!r}，映射没覆盖"


def test_two_entrypoints_select_same_ruleset():
    """同一个 Axx，两个入口解析出的规则名集合必须逐条相同。"""
    for axx in sorted(AXIOM_RULES):
        dims = _synthetic_dims()
        select_findings(dims, axx)
        from_findings = {f.rule for d in dims for f in d.findings}
        from_selftest = {r["rule"] for r in select_selftest(_synthetic_selftest(), axx)}
        assert from_findings == set(AXIOM_RULES[axx]), axx
        assert from_selftest == set(AXIOM_RULES[axx]), axx


def test_dimension_and_exact_rule_selectors_still_work():
    dims = _synthetic_dims()
    select_findings(dims, "D1")
    assert {d.id for d in dims if d.findings} == {"D1"}

    dims = _synthetic_dims()
    select_findings(dims, "A13-undecidable")
    assert {f.rule for d in dims for f in d.findings} == {"A13-undecidable"}


def test_cli_selftest_filter_runs_only_requested_axiom(capsys):
    import scripts.axioms as cli

    code = cli.main(["--self-test-only", "--rule", "A13"])
    out = capsys.readouterr().out
    assert code == 0
    assert "反证自检 2 条（过滤 A13）" in out
    assert "PASS A13 " in out
    assert "PASS A01 " not in out
