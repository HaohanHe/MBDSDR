# SPDX-License-Identifier: MIT
"""P1-9 金标准回归断言锚点测试。"""
from __future__ import annotations

import json
import math

import pytest

from mbdsdr_ai.golden_regression import golden_assert, register_tool_registry

GOLDEN = {
    "cost_initial": 1.0,
    "cost_final": 0.42,
    "converged": True,
    "taps_w": [1.0, -0.5, 0.25],
    "meta": {"solver": "cma", "notes": None},
}


def test_exact_match():
    res = golden_assert(dict(GOLDEN), GOLDEN)
    assert res["match"]
    assert res["n_mismatches"] == 0
    assert res["fields_checked"] >= 8


def test_tolerance_pass_and_fail():
    actual = json.loads(json.dumps(GOLDEN))
    actual["cost_final"] = 0.425
    res = golden_assert(actual, GOLDEN, {"cost_final": 0.01})
    assert res["match"]
    actual["cost_final"] = 0.45
    res = golden_assert(actual, GOLDEN, {"cost_final": 0.01})
    assert not res["match"]
    m = res["mismatches"][0]
    assert m["path"] == "cost_final" and m["abs_diff"] == pytest.approx(0.03)
    # 未列出的数值字段严格相等
    actual2 = json.loads(json.dumps(GOLDEN))
    actual2["cost_initial"] = 1.0001
    assert not golden_assert(actual2, GOLDEN, {"cost_final": 0.01})["match"]


def test_nested_list_index_paths():
    actual = json.loads(json.dumps(GOLDEN))
    actual["taps_w"][1] = -0.5001
    res = golden_assert(actual, GOLDEN, {"taps_w.1": 0.001})
    assert res["match"]
    actual["taps_w"][2] = 0.9
    res = golden_assert(actual, GOLDEN, {"taps_w.1": 0.001})
    assert not res["match"]
    assert res["mismatches"][0]["path"] == "taps_w.2"


def test_missing_and_extra_reported():
    actual = json.loads(json.dumps(GOLDEN))
    del actual["converged"]
    actual["brand_new_field"] = 42
    res = golden_assert(actual, GOLDEN)
    assert not res["match"]               # 缺失判负
    assert res["missing_fields"] == ["converged"]
    assert res["extra_fields"] == ["brand_new_field"]
    # 多余字段不判负：补回缺失后 match
    actual["converged"] = True
    assert golden_assert(actual, GOLDEN)["match"]


def test_nan_semantics():
    actual = json.loads(json.dumps(GOLDEN))
    actual["cost_final"] = math.nan
    g_nan = json.loads(json.dumps(GOLDEN))
    g_nan["cost_final"] = math.nan
    assert golden_assert(actual, g_nan, {"cost_final": 1e9})["match"]
    res = golden_assert(actual, GOLDEN, {"cost_final": 1e9})
    assert not res["match"]
    assert res["mismatches"][0]["kind"] == "nan_mismatch"


def test_type_and_length_mismatch():
    actual = json.loads(json.dumps(GOLDEN))
    actual["taps_w"] = "not-a-list"
    res = golden_assert(actual, GOLDEN)
    assert not res["match"]
    kinds = {m["kind"] for m in res["mismatches"]}
    assert "type_mismatch" in kinds

    actual2 = json.loads(json.dumps(GOLDEN))
    actual2["taps_w"] = [1.0, -0.5]
    res = golden_assert(actual2, GOLDEN)
    assert not res["match"]
    assert any(m["kind"] == "length_mismatch" for m in res["mismatches"])


def test_golden_file_resolution(tmp_path):
    p = tmp_path / "sample_golden.json"
    p.write_text(json.dumps(GOLDEN), encoding="utf-8")
    res = golden_assert(json.loads(json.dumps(GOLDEN)), str(p))
    assert res["match"]

    with pytest.raises(ValueError):
        golden_assert({}, str(tmp_path / "nope.json"))
    with pytest.raises(ValueError):
        golden_assert({}, "")


def test_negative_tolerance_rejected():
    with pytest.raises(ValueError):
        golden_assert({}, GOLDEN, {"a": -1.0})
    with pytest.raises(ValueError):
        golden_assert([], GOLDEN)


def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 1
    assert "golden_assert" in r.tools

    actual = json.loads(json.dumps(GOLDEN))
    res = r.tools["golden_assert"]["handler"](
        {"actual": actual, "golden": GOLDEN})
    assert res.success
    actual["cost_final"] = 9.9
    res = r.tools["golden_assert"]["handler"](
        {"actual": actual, "golden": GOLDEN})
    assert not res.success and res.data["mismatches"]
