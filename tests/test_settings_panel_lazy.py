# SPDX-License-Identifier: MIT
"""设置面板惰性构建（W89 启动成本 / 定义 C）。

低负载窗口的分组计时：ctor 1715 ms 里 `SettingsPanel` 230.8 ms / 342 控件。
设置页在右栏不是首屏，那 38 行在启动时没人看得到。手法与 `f0f82d9a`
（能力矩阵首次打开才装载）一致：`__init__` 只建外框，首次显示同步补建。

本文件钉三件事：

  1. 构造后确实没建内容（否则「惰性」是假的，成本还在 ctor 里）；
  2. 首次 show 时**同步**建完（showEvent 在首帧绘制之前 → 不新增空窗）；
  3. 二次 show 不重建（同一个控件对象，不是每切一次长一遍）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import settings_panel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make(qapp):
    """host=None：只验证构建时机，不推任何设置到消费点。"""
    return settings_panel.SettingsPanel()


def test_ctor_builds_shell_only(qapp):
    """构造后没有内容控件；读 UI 属性才补建（外部访问不被这套机制破坏）。"""
    p = _make(qapp)
    try:
        assert p._ui_built is False
        assert p.findChildren(QWidget) == [], "构造期就把 38 行建出来了，惰性无效"
        # 旧调用方按名字直接取：必须还能取到（补建），不能 AttributeError
        assert p.search_edit is not None
        assert p._ui_built is True
        assert p.findChildren(QWidget) != []
    finally:
        p.close()
        qapp.processEvents()


def test_first_show_builds_before_paint(qapp):
    """show 返回时内容已齐全（不在事件循环下一拍才长出来）。"""
    p = _make(qapp)
    try:
        keys_before = len(p.findChildren(QWidget))
        p.show()
        # 不 processEvents：showEvent 里已同步建完
        assert p._ui_built is True
        assert len(p._rows) > 0
        assert len(p._rows) == len(
            [k for k in p._rows if k in p._key_section])
        assert len(p.findChildren(QWidget)) > keys_before
        assert p.minimumSizeHint().width() > 0
    finally:
        p.close()
        qapp.processEvents()


def test_second_show_reuses_built_content(qapp):
    """隐藏再显示不重建：控件对象身份不变，行数不变。"""
    p = _make(qapp)
    try:
        p.show()
        qapp.processEvents()
        key = sorted(p._rows)[0]
        control = p._rows[key]
        rows = len(p._rows)
        p.hide()
        p.show()
        qapp.processEvents()
        assert len(p._rows) == rows
        assert p._rows[key] is control, "二次显示重建了控件（每打开一次长一遍）"
    finally:
        p.close()
        qapp.processEvents()
