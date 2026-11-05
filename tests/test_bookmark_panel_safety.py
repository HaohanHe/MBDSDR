# SPDX-License-Identifier: MIT
"""8a：删除书签必须先确认，取消就什么都不发生。

反证：当前 ``_on_bookmark_del`` 不弹任何对话框，点一下就从内存移除并落盘；
下面第一条断言（必须弹确认框）与删除数量断言在修复前必红。
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


@pytest.fixture
def panel(qtapp):
    from control_panel import ControlPanel
    p = ControlPanel()
    yield p
    p.close()


class _FakeStore:
    def __init__(self):
        self.removed = []

    def remove(self, name):
        self.removed.append(name)
        return True


def _load(panel, *items):
    """按当前面板协议装载书签（3 元组，兼容旧调用）。"""
    panel.load_bookmarks_from_list(list(items))


def test_delete_custom_bookmark_requires_confirmation(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = _FakeStore()
    panel.set_bookmark_store(store)
    _load(panel, (98_500_000.0, "我的本地台", "FM"))
    assert panel.bookmark_combo.count() == 1

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))

    panel._on_bookmark_del()

    assert asked, "删除书签必须先弹确认框，不能点一下就消失"
    text = str(asked[0][2])
    assert "我的本地台" in text, "确认框必须写明书签名称"
    assert "98.500" in text, "确认框必须写明频率"
    assert len(panel._bookmarks) == 1, "取消后书签必须原样保留"
    assert store.removed == [], "取消时不得落盘删除"


def test_delete_custom_bookmark_on_confirm(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = _FakeStore()
    panel.set_bookmark_store(store)
    _load(panel, (98_500_000.0, "我的本地台", "FM"),
          (145_800_000.0, "ISS", "FM"))

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.Yes))

    panel._on_bookmark_del()

    assert len(panel._bookmarks) == 1, "确认后只删当前选中项"
    assert store.removed == ["我的本地台"], "确认后必须落盘删除"
