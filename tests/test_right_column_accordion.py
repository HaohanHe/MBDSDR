#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
右栏手风琴互斥 + 跨会话记忆（W48 / D-10 用户裁定 A）反证
========================================================

用户 2026-11-11 裁定：**默认单展开**（同一时刻最多一节展开），保留
「全部折叠 / 全部展开」两个显式动作，不做 Ctrl 叠加这类隐藏交互，展开
状态跨会话记住上次那一节。

判据（全部是状态/数值断言）：

1. 展开第二节时第一节自动收起——全列**只有一节**展开；
2. `setTabText()`（切语言/改标题）不许被当成「用户展开」把别节收起；
3. 关掉重建（同一 QSettings profile）后仍是上次那一节；
4. 「全部展开」真的多开（显式动作允许），「全部折叠」后 0 节展开且
   滚动上限随之变小。

改前复跑（`git show HEAD:desktop/right_column.py` 单独放到临时目录跑同一
场景，实测）：点第 4 节后同时展开 `[0, 3]`（判据 1 FAIL）、重建后回到
`[0]`（判据 3 FAIL）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_accordion.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_VIEW_W = 320
_VIEW_H = 300
_SECTION_COUNT = 8
_BODY_H = 400


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=80):
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
    _pump(qapp, 120)
    return col


def _expanded(col):
    return [i for i, section in enumerate(col._sections)
            if section.is_expanded()]


def test_expanding_another_section_collapses_previous(qapp, isolated_profile):
    """展开第 4 节：第 1 节自动收起，全列只剩一节展开。"""
    col = _build_column(qapp)
    try:
        assert _expanded(col) == [0], "前置条件不成立：默认应只展开首节"

        col._sections[3].header.click()
        _pump(qapp, 120)

        assert _expanded(col) == [3], (
            f"手风琴没生效，同时展开 {_expanded(col)}（改前是 [0, 3]）")

        # 切语言/改标题只重刷标题文本，不能被当成「用户展开了这一节」。
        col.setTabText(0, "控制")
        _pump(qapp, 40)
        assert _expanded(col) == [3], (
            f"setTabText 把手风琴带跑了：展开 {_expanded(col)}")
    finally:
        col.close()


def test_last_expanded_section_restored_after_restart(qapp, isolated_profile):
    """关掉重建（同一 profile）：仍是上次展开的那一节。"""
    col = _build_column(qapp)
    try:
        col._sections[5].header.click()
        _pump(qapp, 120)
        assert _expanded(col) == [5]
    finally:
        col.close()
        col.deleteLater()

    col2 = _build_column(qapp)
    try:
        assert _expanded(col2) == [5], (
            f"重启后没记住上次那一节，展开的是 {_expanded(col2)}（改前是 [0]）")
    finally:
        col2.close()


def test_expand_all_then_collapse_all_shrinks_scroll_range(qapp, isolated_profile):
    """两个显式动作：全部展开允许多开；全部折叠后 0 节展开且上限变小。"""
    col = _build_column(qapp)
    try:
        bar = col.verticalScrollBar()
        one = bar.maximum()

        col.expand_all(True)
        _pump(qapp, 120)
        assert _expanded(col) == list(range(_SECTION_COUNT)), (
            f"「全部展开」没有真的多开：{_expanded(col)}")
        many = bar.maximum()
        assert many > one, f"全部展开后滚动上限没涨：{one} -> {many}"

        col.expand_all(False)
        _pump(qapp, 120)
        assert _expanded(col) == [], f"「全部折叠」后仍有节展开：{_expanded(col)}"
        assert bar.maximum() < many, (
            f"全部折叠后滚动上限没变小：{many} -> {bar.maximum()}")
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
