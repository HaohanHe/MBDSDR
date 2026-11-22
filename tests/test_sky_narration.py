# SPDX-License-Identifier: MIT
"""AR 口语文本的纯逻辑测试（离线、无 Qt、无设备）。

覆盖口径
--------
* 八方向 22.5° 扇区边界与非法方位；
* 高度分档边界（75/45/15/0）与非法高度；
* 位姿不可用（None/空 dict/``valid=False``/NaN）只说「还没对准」；
* L4 明确「手动接管」；正常句含方向与高度短语；
* 目标按亮度排序、同名去重、最多三颗、缺 name 跳过、缺星等省略亮度词；
* 所有分支的文本都不得出现 az/alt/vmag/GMST/FOV 这些英文术语。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_narration import (  # noqa: E402
    altitude_phrase,
    direction_word,
    narrate_pose,
    narrate_targets,
)

FORBIDDEN_TERMS = ("az", "alt", "vmag", "GMST", "FOV")
NOT_ALIGNED = "还没对准，先让设备看到星空"
POSE = {"valid": True, "az_deg": 225.0, "alt_deg": 20.0,
        "roll_deg": 0.0, "level": "fused", "reason": ""}


@pytest.mark.parametrize("az,expected", [
    (0.0, "北"),
    (22.4, "北"),
    (22.5, "东北"),
    (45.0, "东北"),
    (67.5, "东"),
    (90.0, "东"),
    (112.5, "东南"),
    (135.0, "东南"),
    (157.5, "南"),
    (180.0, "南"),
    (202.5, "西南"),
    (225.0, "西南"),
    (247.5, "西"),
    (270.0, "西"),
    (292.5, "西北"),
    (315.0, "西北"),
    (337.5, "北"),
    (359.9, "北"),
    (382.5, "东北"),
    (-90.0, "西"),
])
def test_direction_word_sectors(az, expected):
    assert direction_word(az) == expected


def test_direction_word_non_finite():
    for bad in (float("nan"), float("inf"), float("-inf"), None, "abc"):
        assert direction_word(bad) == "未知方向"


@pytest.mark.parametrize("alt,expected", [
    (74.9, "半空"),
    (75.0, "接近头顶"),
    (90.0, "接近头顶"),
    (44.9, "离地平线不太高"),
    (45.0, "半空"),
    (14.9, "贴着地平线"),
    (15.0, "离地平线不太高"),
    (0.0, "贴着地平线"),
    (-0.1, "在地平线以下"),
    (float("nan"), "高度未知"),
    (float("inf"), "高度未知"),
    (None, "高度未知"),
])
def test_altitude_phrase_boundaries(alt, expected):
    assert altitude_phrase(alt) == expected


@pytest.mark.parametrize("pose", [
    None,
    {},
    {"valid": False, "az_deg": 10.0, "alt_deg": 20.0},
    {"valid": True, "az_deg": float("nan"), "alt_deg": 20.0},
    {"valid": True, "az_deg": 10.0, "alt_deg": float("inf")},
    {"valid": True, "az_deg": 10.0},
])
def test_narrate_pose_invalid_inputs(pose):
    assert narrate_pose(pose) == NOT_ALIGNED


def test_narrate_pose_manual_override():
    text = narrate_pose(POSE, level="L4")
    assert "手动接管" in text
    assert "西南" in text


def test_narrate_pose_normal_contains_direction_and_altitude():
    text = narrate_pose(POSE)
    assert text == "你正看向西南，离地平线不太高"


def test_narrate_pose_targets_sorted_deduped_and_capped():
    targets = [
        {"name": "织女星", "vmag": 0.0},
        {"name": "天狼星", "vmag": -1.4},
        {"name": "天津四", "vmag": 1.25},
        {"name": "天狼星", "vmag": -1.4},
        {"name": "牛郎星", "vmag": 0.77},
        {"vmag": 2.0},
    ]
    text = narrate_pose(POSE, targets)
    assert text.startswith("你正看向西南，离地平线不太高。附近能看到：")
    assert text.count("天狼星") == 1
    # 亮度排序：天狼星(-1.4) → 织女星(0.0) → 牛郎星(0.77)；
    # 天津四(1.25) 是第四颗，被上限截掉。
    assert text.index("天狼星") < text.index("织女星") < text.index("牛郎星")
    assert "天津四" not in text
    assert "（很亮）" in text
    assert "（明亮）" in text


def test_narrate_pose_targets_without_vmag_omit_brightness():
    text = narrate_pose(POSE, [{"name": "某星"}])
    assert "某星" in text
    assert "（" not in text


def test_narrate_pose_skips_unusable_targets():
    text = narrate_pose(POSE, [{"vmag": 1.0}, {"name": "  ", "vmag": 2.0}])
    assert "附近能看到" not in text
    assert text == "你正看向西南，离地平线不太高"


def test_narrate_targets_empty():
    for empty in (None, [], (), "abc", {"name": "天狼星"}):
        assert narrate_targets(empty) == "附近没有星表里的亮星"


def test_narrate_targets_sentence():
    text = narrate_targets([{"name": "天狼星", "vmag": -1.4}])
    assert text == "附近能看到：天狼星（很亮）"


def _all_generated_texts():
    texts = [
        direction_word(0.0), direction_word(45.0), direction_word(200.0),
        direction_word(float("nan")),
        altitude_phrase(80.0), altitude_phrase(50.0),
        altitude_phrase(20.0), altitude_phrase(5.0),
        altitude_phrase(-5.0), altitude_phrase(float("nan")),
        narrate_pose(None),
        narrate_pose({}),
        narrate_pose({"valid": False}),
        narrate_pose({"valid": True, "az_deg": 225.0, "alt_deg": 20.0}),
        narrate_pose(POSE, level="L4"),
        narrate_pose(POSE, [{"name": "天狼星", "vmag": -1.4},
                            {"name": "织女星", "vmag": 0.0}]),
        narrate_pose(POSE, [{"name": "某星"}]),
        narrate_targets([]),
        narrate_targets([{"name": "天狼星", "vmag": -1.4}]),
    ]
    return texts


def test_no_engineering_terms_anywhere():
    for text in _all_generated_texts():
        lowered = text.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in lowered, (text, term)
