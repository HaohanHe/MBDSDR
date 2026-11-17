# SPDX-License-Identifier: MIT
"""Task 100 — 恒星信息卡显示当前已开启文化下的星名。

判据
----
1. ``_culture_names_for_hip`` 只读当前开启的文化，每种文化一个名字
   （native > zh/en > 任意），形如 ``日本：枢 · 印度：ध्रुवः``；
2. 未开启的文化不出现；只开 IAU 时不出现日/印名；
3. 未知 HIP、负数、坏数据/异常 → 返回空串且不抛；
4. 星卡 ``desc`` 在「光谱型/星座」之后、星表来源（HYG v4.1）之前插入该行；
5. 中国星官不重复报（已有 ``star_culture`` 默认中文名）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_star_culture_names.py -v
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.skyengine.sky_culture import load_registry  # noqa: E402

JAPANESE = "japanese"
INDIAN = "indian"


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(600, 600)
    v.set_observer(40.0, 116.0, 0.0)
    return v


def _hip_in_both() -> int:
    """从数据里找一个 japanese 与 indian 都有名字的 HIP（不写死编号）。"""
    packs = {p.id: p for p in load_registry()}
    common = set(packs[JAPANESE].names) & set(packs[INDIAN].names)
    assert common, "数据里应至少有一颗星同时被日本/印度文化命名"
    return min(common)


def _best_name(entry) -> str:
    for key in ("native", "zh", "en"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in entry.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tag(system_id: str) -> str:
    return str(_constel.system_info(system_id)["tag"])


# ---------------------------------------------------------------------------
# 1. 方法本身
# ---------------------------------------------------------------------------
def test_two_cultures_show_both_names():
    hip = _hip_in_both()
    packs = {p.id: p for p in load_registry()}
    v = _new_view()
    v.set_constellation_systems({JAPANESE, INDIAN})

    text = v._culture_names_for_hip(hip)
    assert set(text.split(" · ")) == {
        f"{_tag(JAPANESE)}：{_best_name(packs[JAPANESE].names[hip])}",
        f"{_tag(INDIAN)}：{_best_name(packs[INDIAN].names[hip])}",
    }


def test_only_enabled_cultures_appear():
    hip = _hip_in_both()
    v = _new_view()
    v.set_constellation_systems({JAPANESE})
    text = v._culture_names_for_hip(hip)
    assert text.startswith(f"{_tag(JAPANESE)}：")
    assert _tag(INDIAN) not in text


def test_iau_only_has_no_culture_names():
    """默认视图只开 IAU：不得冒出日/印名（允许只有 IAU 自己的条目）。"""
    hip = _hip_in_both()
    v = _new_view()
    assert v.constellation_systems() == (_constel.SYSTEM_IAU,)
    text = v._culture_names_for_hip(hip)
    assert _tag(JAPANESE) not in text and _tag(INDIAN) not in text
    assert text == "" or text.startswith("IAU：")


def test_chinese_xingguan_not_duplicated():
    """中国星官名由 star_culture 默认中文名承担，这里不再重复一行。"""
    hip = _hip_in_both()
    packs = {p.id: p for p in load_registry()}
    v = _new_view()
    v.set_constellation_systems(
        {_constel.SYSTEM_CHINESE, JAPANESE, INDIAN})
    text = v._culture_names_for_hip(hip)
    assert _constel.system_info(_constel.SYSTEM_CHINESE)["tag"] not in text
    entry = packs.get(_constel.SYSTEM_CHINESE)
    if entry is not None and hip in entry.names:
        for value in entry.names[hip].values():
            if isinstance(value, str) and value.strip():
                assert value.strip() not in text


def test_max_cultures_limit():
    hip = _hip_in_both()
    v = _new_view()
    v.set_constellation_systems(set(_constel.system_ids()))
    text = v._culture_names_for_hip(hip, max_cultures=3)
    assert len(text.split(" · ")) == 3


def test_unknown_hip_returns_empty_without_raising():
    v = _new_view()
    assert v._culture_names_for_hip(999999) == ""
    assert v._culture_names_for_hip(0) == ""
    assert v._culture_names_for_hip(-5) == ""
    assert v._culture_names_for_hip(None) == ""
    assert v._culture_names_for_hip("not-a-hip") == ""


def test_bad_name_data_returns_empty(monkeypatch):
    hip = _hip_in_both()
    v = _new_view()
    v.set_constellation_systems({JAPANESE, INDIAN})

    from mbdsdr_ai.skyengine import sky_culture

    monkeypatch.setattr(sky_culture, "names_for_hip",
                        lambda _hip: {JAPANESE: "坏数据", INDIAN: ["x"]})
    assert v._culture_names_for_hip(hip) == ""

    monkeypatch.setattr(sky_culture, "names_for_hip",
                        lambda _hip: {JAPANESE: {"native": 123}})
    assert v._culture_names_for_hip(hip) == ""

    monkeypatch.setattr(sky_culture, "names_for_hip",
                        lambda _hip: (_ for _ in ()).throw(RuntimeError("坏")))
    assert v._culture_names_for_hip(hip) == ""

    monkeypatch.setattr(sky_culture, "names_for_hip", lambda _hip: None)
    assert v._culture_names_for_hip(hip) == ""


# ---------------------------------------------------------------------------
# 2. 星卡落位
# ---------------------------------------------------------------------------
def _star_body(hip: int) -> dict:
    return {
        "kind": "star", "key": "TEST", "name": "", "hyg_id": 1, "hip": hip,
        "vmag": 1.98, "ra_deg": 37.95, "dec_deg": 89.26,
        "bv": 0.0, "dist_pc": 132.0, "spect": "F7Ib", "con": "UMi",
    }


def test_star_card_desc_includes_culture_names_after_constellation():
    hip = _hip_in_both()
    v = _new_view()
    v.set_constellation_systems({JAPANESE, INDIAN})
    v._build_body_info(_star_body(hip))
    desc = str(v._body_info["desc"])

    names = v._culture_names_for_hip(hip)
    assert names and names in desc
    assert desc.index("星座 ") < desc.index(names)
    assert desc.index(names) < desc.index("HYG v4.1")


def test_star_card_desc_unchanged_when_no_culture_names():
    v = _new_view()
    v.set_constellation_systems({JAPANESE, INDIAN})
    v._build_body_info(_star_body(999999))
    desc = str(v._body_info["desc"])
    assert _tag(JAPANESE) not in desc and _tag(INDIAN) not in desc
    assert desc == ("光谱型 F7Ib · 星座 "
                    f"{_constel.CONSTELLATION_NAMES['UMi']} · HYG v4.1")
