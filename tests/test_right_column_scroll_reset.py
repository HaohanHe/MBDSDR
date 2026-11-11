#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
右栏切节重置滚动位置 + 程序化跳转落点（W47 P1-04 / W46 P1-01）反证
==================================================================

口径裁定（用户 2026-11-11 确认，防以后有人「顺手统一」）：
**只有用户点节标题（切节）才归零，程序化 `setCurrentIndex()` 不归零。**
`setCurrentIndex()` 是「展开并滚到目标节」，被 `main_window` 三处使用：
`跳到分组` 菜单、`_show_right_tab_of()`（各面板入口）、浮动面板归位
（`_return_panel`）。若把它也改成归零，这三处会全部跳到长列顶部而不是
目标节，等于把「跳到分组」废掉——所以两条路径必须分开处理，不得合并。

W47 判据（具体滚动数值，不断言「调用了某个函数」）：

1. 滚到第 6 节（`verticalScrollBar().value() > 0`，且第 6 节标题确实在视口内）；
2. 用户点节标题（切节）；
3. `verticalScrollBar().value()` 必须回到 **0**。

W46 判据（程序化跳转落点）：

- 目标节**标题完整可见**且顶在视口顶（`y == 0`、`y + 标题高 <= 视口高`）。
  改前 `ensureWidgetVisible(s, 0, 8)` 只做最小滚动：目标已经露出一条边时
  一帧都不动（真 MainWindow 实测跳「控制」后 y=-281，标题被切在视口上方），
  目标比视口高时只把标题压到视口底边（y=529/562）。

反证（改前是坏的）：HEAD 版 `right_column.py` 同一场景跑同一脚本，点标题后
滚动条停在原值（非 0）、程序化跳转后标题 y<0 或贴视口底边。本用例在 HEAD 上必须红。

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
_FIRST_BODY_H = 1100        # 首节长正文：展开后自身高度 > 视口，可复现"标题被切"
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
        body.setFixedHeight(_FIRST_BODY_H if i == 0 else _BODY_H)
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


def test_programmatic_jump_puts_target_header_at_viewport_top(
        qapp, isolated_profile):
    """W46：程序化跳转（跳到分组/面板归位）把目标节标题顶到视口顶。

    场景即 P1-01 的现场：首节正文比视口高、用户已经聊到深处，首节标题
    被切在视口上方但正文下半截还露在视口里（ensureWidgetVisible 会判定
    「已经可见」而一帧都不动）。改前实测 y=−412、value 停在 412；
    改后应为 y=0、value=0，且标题 24 px 完整可见。
    """
    col = _build_column(qapp)
    try:
        bar = col.verticalScrollBar()
        sec = col._sections[0]
        bar.setValue(600)
        _pump(qapp, 60)
        y_before = sec.header.mapTo(col.viewport(), QPoint(0, 0)).y()
        assert y_before < 0, (
            f"前置条件不成立：首节标题没被切在视口上方（y={y_before}）")
        assert bar.value() == 600, "前置条件不成立：滚动位置没停到 600"

        col.setCurrentIndex(0)     # 「跳到分组」菜单 / 面板归位走的就是它
        _pump(qapp, 150)

        vp_h = col.viewport().height()
        y = sec.header.mapTo(col.viewport(), QPoint(0, 0)).y()
        head_h = sec.header.height()
        assert sec.is_expanded()
        assert y == 0, (
            f"程序化跳转后标题没顶到视口顶：y={y}（改前 {y_before}），"
            f"value={bar.value()}")
        assert y + head_h <= vp_h, (
            f"标题不完整：y={y} 高={head_h} 视口={vp_h}")
        assert bar.value() == 0, (
            f"跳首节后滚动位置应为 0，实际 {bar.value()}")
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
