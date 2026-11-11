#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
右栏切节重置滚动位置（W47 / roadmap P1-04）反证
================================================

判据用**具体滚动数值**，不断言「调用了某个函数」：

1. 滚到第 6 节（`verticalScrollBar().value() > 0`，且第 6 节标题确实在视口内）；
2. 用户点节标题（切节）；
3. `verticalScrollBar().value()` 必须回到 **0**。

反证（改前是坏的）：HEAD 版 `right_column.py` 同一场景跑同一脚本，点标题后
滚动条停在原值（非 0），见 `docs/pending-patches` 之外的复跑记录。
本用例在 HEAD 上必须红。

同时加一条守卫：程序化 `setCurrentIndex()`（「跳到分组」菜单 / 浮动面板归位
走的就是它）必须**仍然**滚到目标节，不许被「切节归零」误伤。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_scroll_reset.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_VIEW_W = 320
_VIEW_H = 300
_BODY_H = 600
_SECTION_COUNT = 8
_DEEP_SECTION = 6


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=60):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(ms, app.quit)
    app.exec()


def _build_column(qapp):
    from right_column import RightColumn
    col = RightColumn()
    col.resize(_VIEW_W, _VIEW_H)
    for i in range(_SECTION_COUNT):
        body = QWidget()
        body.setFixedHeight(_BODY_H)
        col.addTab(body, f"节{i}")
    col.show()
    _pump(qapp, 80)
    return col


def _header_y(col, index):
    """节标题在滚动容器坐标系里的 y。"""
    sec = col._sections[index]
    return sec.header.mapTo(col._container, QPoint(0, 0)).y()


def _scroll_to_section(col, index):
    """把滚动位置设到第 index 节标题处（超出上限则贴底），返回实际值。"""
    bar = col.verticalScrollBar()
    y = _header_y(col, index)
    bar.setValue(min(y, bar.maximum()))
    return bar.value()


def test_switching_section_by_header_click_resets_scroll_to_zero(
        qapp, isolated_profile):
    """滚到第 6 节 → 点节标题切节 → 滚动位置必须是 0。"""
    col = _build_column(qapp)
    try:
        # 先展开前几节，让长列真的能滚起来（点标题=用户切节，走同一路径）。
        for i in (1, 2, 3, 4, 5):
            col._sections[i].header.click()
            _pump(qapp, 20)
        bar = col.verticalScrollBar()
        assert bar.maximum() > 0, "前置条件不成立：长列没有可滚区间"

        value = _scroll_to_section(col, _DEEP_SECTION)
        _pump(qapp, 40)
        assert value > 0, "前置条件不成立：没能滚到第 6 节"
        assert bar.value() == value, (
            f"前置条件不成立：设置滚动值被改写 {value} -> {bar.value()}")

        # 第 6 节标题确实在视口内（"滚到第 6 节"是真的，不是数字游戏）。
        sec6 = col._sections[_DEEP_SECTION]
        top = sec6.header.mapTo(col.viewport(), QPoint(0, 0)).y()
        assert 0 <= top < col.viewport().height(), (
            f"前置条件不成立：第 6 节标题不在视口内（y={top}）")
        assert not sec6.is_expanded(), "前置条件不成立：第 6 节应处于折叠态"

        # 切节：用户点第 6 节标题（折叠 → 展开）。
        sec6.header.click()
        _pump(qapp, 80)

        assert sec6.is_expanded(), "切节没有生效：第 6 节仍是折叠态"
        assert bar.value() == 0, (
            f"切节后滚动位置未归零，仍停在 {bar.value()}（应为 0）")
    finally:
        col.close()


def test_scroll_to_top_entry_point(qapp, isolated_profile):
    """「回到顶部」行为入口：scroll_to_top() 把滚动位置复位到 0。"""
    col = _build_column(qapp)
    try:
        for i in (1, 2, 3, 4, 5):
            col._sections[i].header.click()
            _pump(qapp, 20)
        assert _scroll_to_section(col, _DEEP_SECTION) > 0
        col.scroll_to_top()
        assert col.verticalScrollBar().value() == 0
    finally:
        col.close()


def test_programmatic_jump_still_reveals_target_section(
        qapp, isolated_profile):
    """守卫：程序化跳转（跳到分组/面板归位）仍要滚到目标节，不许被归零误伤。"""
    col = _build_column(qapp)
    try:
        for i in (1, 2, 3, 4, 5):
            col._sections[i].header.click()
            _pump(qapp, 20)
        bar = col.verticalScrollBar()
        col.setCurrentIndex(_DEEP_SECTION)
        _pump(qapp, 120)
        assert col._sections[_DEEP_SECTION].is_expanded()
        assert bar.value() > 0, "程序化跳转被切节归零误伤：滚动条停在顶部"
        top = col._sections[_DEEP_SECTION].header.mapTo(
            col.viewport(), QPoint(0, 0)).y()
        assert -8 <= top < col.viewport().height(), (
            f"程序化跳转后目标节标题不在视口内（y={top}）")
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
