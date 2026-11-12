# SPDX-License-Identifier: MIT
"""右栏程序化跳节的键盘焦点（2026-11-12，键盘普查「中 10」）。

``RightColumn.setCurrentIndex`` 原本只展开 + 滚到目标节，不移动焦点：从
「面板」菜单/命令面板跳过去之后，焦点还留在菜单或原处，接着按 Tab 会从
旧位置继续，用户以为「跳过去就能直接操作」却落空。

修复走纯逻辑路径，不碰绘制：焦点交给**节内第一个可聚焦子控件**（标准
控件自己会画焦点框），节里没有可聚焦控件时退回节标题。``_SectionHeader``
的 ``paintEvent`` 没有因此改动——焦点可见性仍归 FZ-04。

反证（实测）：删掉 ``setCurrentIndex`` 里的 ``_focus_section`` 排队，
本文件第一条与第三条立刻红；第二条（不抢已在本节内的焦点）保持绿。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_programmatic_focus.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app):
    # setCurrentIndex 把交接排在 singleShot(0)：清两轮事件队列。
    app.processEvents()
    app.processEvents()


def _make_column(app, focusable=True):
    """列与「别处焦点」必须在同一个顶层窗口里。

    Qt 只把焦点交给**活动窗口**内的控件；若把 outside 另起一个顶层窗口，
    跳节时 col 所在窗口不是活动的，setFocus 会静默失败——测试就测了个假象。
    """
    from right_column import RightColumn

    host = QWidget()
    host_lay = QVBoxLayout(host)
    col = RightColumn()
    col.resize(420, 600)
    host_lay.addWidget(col)
    outside = QLineEdit("outside")
    host_lay.addWidget(outside)

    edits = []
    for i in range(3):
        body = QWidget()
        lay = QVBoxLayout(body)
        row = []
        if focusable:
            for j in range(3):
                edit = QLineEdit(f"{i}-{j}")
                lay.addWidget(edit)
                row.append(edit)
        else:
            lay.addWidget(QLabel("只有文字"))
        col.addTab(body, f"节{i}")
        edits.append(row)
    host.resize(520, 760)
    host.show()
    app.processEvents()
    return host, col, edits, outside


def test_jump_from_outside_focuses_first_control(qapp):
    """焦点在别处时跳节 → 落到节内第一个可聚焦控件。"""
    host, col, edits, outside = _make_column(qapp)
    outside.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is outside

    col.setCurrentIndex(1)
    _pump(qapp)

    fw = qapp.focusWidget()
    assert fw is edits[1][0], (
        f"跳节后焦点没进目标节：实际 {type(fw).__name__ if fw else None}")
    assert col._sections[1].isAncestorOf(fw)
    host.deleteLater()


def test_jump_does_not_steal_focus_already_inside_section(qapp):
    """AI 提问路径先聚焦输入框再跳节：焦点已被用户占住时不许抢。"""
    host, col, edits, _outside = _make_column(qapp)
    col.setCurrentIndex(2)
    _pump(qapp)

    target = edits[2][1]          # 不是第一个控件
    target.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is target

    col.setCurrentIndex(2)
    _pump(qapp)
    assert qapp.focusWidget() is target, (
        "跳节把用户已经放在本节输入框里的焦点拽走了")
    host.deleteLater()


def test_section_without_focusable_child_falls_back_to_header(qapp):
    """节里没有可聚焦控件时，退回节标题（仍算焦点进了这一节）。"""
    host, col, _edits, outside = _make_column(qapp, focusable=False)
    outside.setFocus()
    qapp.processEvents()

    col.setCurrentIndex(0)
    _pump(qapp)
    assert col._sections[0].header.hasFocus(), "空节跳转后焦点没有落到节标题"
    host.deleteLater()


def test_focus_handoff_does_not_break_header_at_viewport_top(qapp):
    """焦点移交先于落点滚动，W46「标题顶到视口顶」不能被它带跑。"""
    from PySide6.QtCore import QPoint
    from right_column import RightColumn

    host = QWidget()
    host_lay = QVBoxLayout(host)
    col = RightColumn()
    col.resize(420, 300)
    host_lay.addWidget(col)
    # 首节正文高于视口：滚到深处再把首节标题切到视口上方（W46 的现场）。
    first_body = QWidget()
    first_body.setMinimumHeight(1200)
    first_lay = QVBoxLayout(first_body)
    first_lay.addWidget(QLineEdit("0-0"))
    first_lay.addWidget(QLabel("占高"))
    col.addTab(first_body, "节0")
    for i in range(1, 4):
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.addWidget(QLineEdit(f"{i}-0"))
        col.addTab(body, f"节{i}")
    host.resize(520, 320)
    host.show()
    qapp.processEvents()

    bar = col.verticalScrollBar()
    bar.setValue(min(600, bar.maximum()))
    assert bar.value() > 0, "前置条件不成立：滚不到深处"

    col.setCurrentIndex(0)
    _pump(qapp)
    qapp.processEvents()

    y = col._sections[0].header.mapTo(col.viewport(), QPoint(0, 0)).y()
    assert y == 0, f"跳节后标题没顶到视口顶：y={y}，value={bar.value()}"
    host.deleteLater()
