# SPDX-License-Identifier: MIT
"""目标合并（``mbdsdr_ai.sky_targets.merge_targets``）的纯逻辑契约。

只测数据口径：去重键、亮度/角距优先、缺值排后、limit 截断与非法参数、
入参不被修改。不碰 Qt、不查星表。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_targets import merge_targets  # noqa: E402


def test_same_key_keeps_brighter_target():
    dim = {"name": "Vega", "vmag": 2.0, "angular_distance_deg": 0.1}
    bright = {"name": "Vega", "vmag": 0.5, "angular_distance_deg": 0.8}
    out = merge_targets([dim, bright], limit=6)

    assert len(out) == 1
    assert out[0]["vmag"] == 0.5
    assert out[0]["angular_distance_deg"] == 0.8


def test_same_name_different_kind_stays_separate():
    star = {"name": "同名词", "vmag": 1.0}
    body = {"name": "同名词", "kind": "moon", "magnitude": -12.0}
    out = merge_targets([star, body], limit=6)

    assert len(out) == 2
    assert {item["kind"] if "kind" in item else "star" for item in out} == {
        "star", "moon"}


def test_missing_magnitude_sorted_last():
    groups = [
        {"name": "NoMag"},
        {"name": "Dim", "vmag": 5.0},
        {"name": "Bright", "vmag": 0.1},
        {"name": "Planet", "kind": "planet", "magnitude": 1.0},
    ]
    out = merge_targets(groups, limit=6)

    assert [item["name"] for item in out] == ["Bright", "Planet", "Dim", "NoMag"]


def test_same_magnitude_sorted_by_angular_distance():
    far = {"name": "Far", "vmag": 1.0, "angular_distance_deg": 2.0}
    near = {"name": "Near", "vmag": 1.0, "angular_distance_deg": 0.5}
    tie_a = {"name": "Aaa", "vmag": 1.0, "angular_distance_deg": 0.5}
    out = merge_targets([far, near, tie_a], limit=6)

    # 同星等先按角距；角距仍相同按 name 稳定。
    assert [item["name"] for item in out] == ["Aaa", "Near", "Far"]


def test_limit_truncates_after_sorting():
    groups = [
        {"name": "C", "vmag": 3.0},
        {"name": "A", "vmag": 1.0},
        {"name": "B", "vmag": 2.0},
    ]
    out = merge_targets(groups, limit=2)

    assert [item["name"] for item in out] == ["A", "B"]


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3", None, True])
def test_invalid_limit_raises_value_error(bad):
    with pytest.raises(ValueError):
        merge_targets([{"name": "A", "vmag": 1.0}], limit=bad)


def test_inputs_not_modified_and_result_is_a_copy():
    first = {"name": "A", "vmag": 1.0, "note": "keep"}
    second = {"name": "B", "vmag": 2.0}
    group = [first, second]
    other = [{"name": "A", "vmag": 3.0}]      # 同键更暗，应被丢弃
    out = merge_targets(group, other, limit=6)

    assert group == [first, second]
    assert other == [{"name": "A", "vmag": 3.0}]
    assert out is not group and out is not other
    assert out[0] is not first
    out[0]["name"] = "changed"
    assert first["name"] == "A"
    # 非 Mapping 与空名字都被跳过，且不影响正常项。
    assert merge_targets([None, "x", {"name": "  "}, first], limit=6) == [
        {"name": "A", "vmag": 1.0, "note": "keep"}]
