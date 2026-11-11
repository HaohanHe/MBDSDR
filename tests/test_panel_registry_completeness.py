# SPDX-License-Identifier: MIT
"""W68 反证：右栏已存在的 APRS / 卫星云图页签必须登记进面板注册表。

改前：这两个页签在 `MainWindow._attach_digital_panels` 里真实创建，但
`panel_registry` 没有对应 PanelSpec——「面板」菜单里找不到、能力目录
（capability_catalog 由注册表派生）也看不见，用户只能靠猜右栏有没有。

改后：注册表与能力目录都能查到，且描述里写清「需要什么条件才算可用」，
不把入口存在说成「都能收到」。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from panel_registry import AREA_RIGHT, registry  # noqa: E402

_EXPECTED = {"aprs": "解码", "sat_image": "卫星"}


@pytest.mark.parametrize("pid", sorted(_EXPECTED))
def test_right_column_tabs_are_registered(pid):
    spec = registry().get(pid)
    assert spec is not None, f"面板注册表缺 {pid}（右栏页签在菜单里不可见）"
    assert spec.area == AREA_RIGHT, f"{pid} 实际是右栏节，area 却写成 {spec.area}"
    assert spec.closable, f"{pid} 与其他右栏节一样可收起"
    assert spec.name and spec.summary, f"{pid} 缺名称或说明"
    assert len(spec.summary) >= 12, f"{pid} 说明 ${spec.summary!r} 过于空泛"


def test_capability_catalog_exposes_both_panels():
    from capability_catalog import PANEL_CATEGORY, build_catalog

    caps = build_catalog()
    by_source = {c.source: c for c in caps if c.source.startswith("panel_registry:")}
    for pid, category in _EXPECTED.items():
        src = f"panel_registry:{pid}"
        assert src in by_source, f"能力目录未出现 {src}；已出现：{sorted(by_source)}"
        cap = by_source[src]
        assert cap.category == category, (pid, cap.category)
        assert PANEL_CATEGORY.get(pid) == category
        assert "面板" in cap.trigger and "菜单" in cap.trigger


def test_aprs_summary_states_the_real_precondition():
    """APRS 只有 NFM+对准频点才出报文；说明不得写成「打开就能收」。"""
    s = registry().get("aprs").summary
    assert "NFM" in s
    assert "144.390" in s or "144.8" in s


def test_sat_image_summary_is_not_overclaimed():
    """卫星云图是离线 IQ 出图，不能写成实时接收。"""
    s = registry().get("sat_image").summary
    assert "LRPT" in s or "APT" in s
    assert "不是实时" in s
