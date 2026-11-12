# SPDX-License-Identifier: MIT
"""右栏节的顺序（2026-11-12，键盘普查「提示 15」）。

`RightColumn.insertTab` 用 ``self._lay.insertWidget(index, section)``
（right_column.py:288）把节按 index 插进竖列，顺序完全靠调用方的书写顺序。
视觉上一直是对的，但没有任何用例拦「有人调换了两节的注册顺序」——这类改动
不报错、不崩，只是用户熟悉的位置变了。

本文件只加断言，不改实现。证据分两路：
  * **生产注册顺序**：AST 读 ``main_window.py`` 里 ``_add_right_tab("标题", ...)``
    的书写顺序，与实测快照 ``_EXPECTED`` 逐位比对（名字，不是索引）；
  * **视觉顺序**：拿真 ``RightColumn`` 按同一顺序装节、显示后按标题真实 y
    排序，结果必须与名字表一致——防 ``insertWidget`` 退化成 ``addWidget``
    这类「索引对、视觉错」的布局回归；
  * 新增/删除节按**名字**取锚点（``index("状态")``），不写死索引。

不构造 MainWindow：本文件必须能在 Qt 锁被别的会话占着时独立跑
（``--noconftest``），MainWindow 的节数/注册顺序由上面两路共同钉住。

反证（实测）：把 main_window 里「控制」「状态」两条 ``_add_right_tab`` 的
标题对调，前两条用例立刻红。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_section_order.py -q
"""
import ast
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_MAIN_WINDOW = os.path.join(_ROOT, "desktop", "main_window.py")

#: 实测可视顺序（自上而下）。按名字断言：新增/删除节时改这张表，
#: 不写死索引——索引会随任何插队变化而漂。
_EXPECTED = (
    "控制", "状态", "AI 智能体", "卫星跟踪", "模块", "气象云图", "新时空",
    "扫频", "调制识别", "书签", "服务设置", "设置",
    "VFO", "ANR", "星座图", "设备", "增益",
    "ADS-B", "APRS", "卫星云图",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _registered_titles():
    """main_window 里 ``_add_right_tab("标题", ...)`` 的书写顺序。

    必须按行号排序：``ast.walk`` 是广度优先，嵌套在 ``if``/``try`` 里的调用
    （扫频/调制识别…、卫星跟踪）会被提到前面，读出来的顺序不是执行顺序。
    """
    with open(_MAIN_WINDOW, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=_MAIN_WINDOW)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", getattr(func, "attr", ""))
        if name != "_add_right_tab" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((node.lineno, first.value))
    return [title for _, title in sorted(found)]


def test_registration_order_matches_the_measured_order():
    """生产代码的注册顺序 = 实测可视顺序（名字逐位比对）。"""
    titles = _registered_titles()
    assert titles == list(_EXPECTED), (
        "右栏节注册顺序变了：\n"
        f"  实测 {titles}\n  期望 {list(_EXPECTED)}")


def test_visual_top_to_bottom_matches_the_same_order(qapp):
    """按标题真实 y 排序 = 用户看到的顺序，必须与注册顺序同名。"""
    from right_column import RightColumn

    col = RightColumn()
    col.resize(400, 800)
    for title in _registered_titles():
        col.addTab(QWidget(), title)
    col.show()
    qapp.processEvents()

    # 注意 RightColumn.widget 是 QTabWidget 兼容 API（要 index），
    # 容器本身是私有属性；既有右栏用例同样直接读 _sections。
    container = col._container
    paired = []
    for i in range(col.count()):
        sec = col._sections[i]
        y = sec.header.mapTo(container, QPoint(0, 0)).y()
        paired.append((y, col.tabText(i)))
    visual = [name for _, name in sorted(paired)]
    assert visual == list(_EXPECTED), (
        "右栏视觉顺序与注册顺序不一致（insertWidget 的 index 没生效？）：\n"
        f"  视觉 {visual}\n  期望 {list(_EXPECTED)}")
    col.deleteLater()


def test_insert_and_remove_anchor_by_name_not_index(qapp):
    """insertTab/removeTab 用**名字**找锚点，中间插队/删除不改相对顺序。"""
    from right_column import RightColumn

    col = RightColumn()
    for title in ("控制", "状态", "设置"):
        col.addTab(QWidget(), title)

    anchor = [col.tabText(i) for i in range(col.count())].index("状态")
    col.insertTab(anchor, QWidget(), "书签")
    assert [col.tabText(i) for i in range(col.count())] == [
        "控制", "书签", "状态", "设置"]

    col.removeTab([col.tabText(i) for i in range(col.count())].index("书签"))
    assert [col.tabText(i) for i in range(col.count())] == [
        "控制", "状态", "设置"]
    col.deleteLater()
