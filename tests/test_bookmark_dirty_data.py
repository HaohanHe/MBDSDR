# SPDX-License-Identifier: MIT
"""书签脏数据不得撑爆控制面板（offscreen 回归）。

实测缺陷：``~/.mbdsdr/bookmarks.json`` 里一条 100012 字符的书签名，让
``bookmark_combo.minimumSizeHint`` 变成 1,300,208px，控制面板被撑到
1,300,272×1010，``freq_display.grab()`` 归一化后高度只剩 1px。

本文件钉住三条：
1. 极端脏数据（10 万字符）不得改变控制面板宽度 / freq_display 截图尺寸。
2. 超长名只在显示层截断 + 标异常（⚠ + tooltip），原始数据不被删改。
3. 新输入超过上限被拒绝（提示窗口打桩，断言未加入书签）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def panel(qtapp):
    from control_panel import ControlPanel
    p = ControlPanel()
    p.resize(320, 900)
    p.show()
    qtapp.processEvents()
    yield p
    p.close()


def _grab_size(widget):
    img = widget.grab()
    return img.width(), img.height()


def test_extreme_bookmark_name_does_not_blow_up_panel(qtapp, panel):
    from control_panel import BOOKMARK_NAME_MAX_CHARS  # noqa: F401

    baseline_panel_w = panel.minimumSizeHint().width()
    baseline_freq = _grab_size(panel.freq_display)

    dirty = "A" * 100_012
    panel.load_bookmarks_from_list([(98_500_000.0, dirty, "FM")])
    qtapp.processEvents()

    # 1) 面板宽度不受影响（允许 0 增长）
    assert panel.minimumSizeHint().width() <= baseline_panel_w, (
        "超长书签名不得撑宽控制面板："
        f"{panel.minimumSizeHint().width()} > {baseline_panel_w}"
    )
    # 2) 下拉框自身 sizeHint 也不得随文本线性增长
    assert panel.bookmark_combo.minimumSizeHint().width() <= 400, (
        "bookmark_combo minimumSizeHint 仍被 item 文本撑开："
        f"{panel.bookmark_combo.minimumSizeHint().width()}"
    )
    # 3) freq_display 截图尺寸正常（宽不被撑长、高不退化成 1px）
    w, h = _grab_size(panel.freq_display)
    assert w <= baseline_freq[0] + 10, f"freq_display 被拉宽：{w} vs {baseline_freq[0]}"
    assert h >= 20, f"freq_display 截图高度退化：{h}px"


def test_dirty_name_is_marked_not_deleted(qtapp, panel):
    from PySide6.QtCore import Qt
    from control_panel import BOOKMARK_NAME_MAX_CHARS
    dirty = "A" * 100_012
    panel.load_bookmarks_from_list([(100_000_000.0, dirty, "FM")])
    qtapp.processEvents()

    # 原始数据仍在内存里，长度未变（不静默删除/裁剪用户数据）
    assert panel._bookmarks[0][1] == dirty
    # 显示层截断 + ⚠ 标记 + tooltip 说明
    shown = panel.bookmark_combo.itemText(0)
    assert shown.startswith("⚠ ")
    assert len(shown) <= BOOKMARK_NAME_MAX_CHARS + 30
    tip = panel.bookmark_combo.itemData(0, Qt.ToolTipRole)
    assert tip and "超过上限" in tip
    assert panel.bookmark_combo.toolTip()


def test_overlong_new_input_rejected(qtapp, panel, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from control_panel import BOOKMARK_NAME_MAX_CHARS

    panel._freq_hz = 98_500_000.0
    panel._bookmarks = []
    panel._refresh_bookmarks()
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("B" * 200, True)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)))
    panel._on_bookmark_add()
    assert panel._bookmarks == [], "超长书签名必须拒绝写入"
    assert warnings, "超长输入必须有用户提示"
    assert str(BOOKMARK_NAME_MAX_CHARS) in str(warnings[0])
