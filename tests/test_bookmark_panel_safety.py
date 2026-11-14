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


# ---------------------------------------------------------------------------
# A4 / B2：书签面板自己的改名与频率输入
# ---------------------------------------------------------------------------
@pytest.fixture
def bm_panel(qtapp, tmp_path):
    from bookmark_panel import BookmarkPanel

    p = BookmarkPanel(config_dir=str(tmp_path))
    yield p
    p.close()


def _seed(bm_panel, name="原名"):
    from mbdsdr_ai.bookmark_manager import Bookmark

    bm_panel._mgr.add(Bookmark(frequency_hz=98_500_000, name=name,
                               modulation="FM", bandwidth_hz=200_000))
    bm_panel.refresh_list()
    bm_panel.tree.setCurrentItem(bm_panel.tree.topLevelItem(0))


def test_delete_confirm_defaults_to_no_and_states_consequence(
        bm_panel, monkeypatch):
    """删书签的确认框必须默认「否」，正文写清不可撤销。

    反证：旧实现不传 defaultButton，Qt 默认选中第一个按钮（Yes），
    回车即删；且正文只有「删除 X @ freq？」没有后果说明。
    """
    from PySide6.QtWidgets import QMessageBox

    _seed(bm_panel)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))

    bm_panel._on_delete()

    assert asked, "删除书签必须弹确认框"
    args = asked[0]
    assert args[3] == (QMessageBox.Yes | QMessageBox.No), \
        f"确认框应给「是/否」两个选项，实际: {args[3]}"
    assert len(args) >= 5, \
        "确认框必须显式传 defaultButton=No；不传时 Qt 默认选中 Yes（回车即删）"
    assert args[4] == QMessageBox.No, \
        f"默认按钮必须是 No（回车不能删除），实际: {args[4]}"
    text = str(args[2])
    assert "不能撤销" in text, f"必须写明不可撤销: {text}"
    assert "bookmarks.json" in text, "必须写明会落盘到哪里"
    assert bm_panel._mgr.all()[0].name == "原名", "取消后书签必须原样保留"


def test_edit_empty_name_rejected_and_original_kept(bm_panel, monkeypatch):
    """清空名称 ≠ 取消改名：空名必须拒绝，不得把原名落库成空白。"""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    _seed(bm_panel)
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("   ", True)))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a)))

    bm_panel._on_edit()

    assert warnings, "空名称必须给出可见提示"
    assert "不能为空" in str(warnings[0][2])
    assert bm_panel._mgr.all()[0].name == "原名", "原名必须原样保留"


def test_add_out_of_range_frequency_reprompts_instead_of_clamping(
        bm_panel, monkeypatch):
    """7000 MHz 不许被对话框夹成 6000 后静默落库，必须提示重输。"""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    calls = []
    answers = [(7000.0, True), (145.5, True)]

    def fake_double(*a, **k):
        calls.append(k)
        return answers[len(calls) - 1]

    groups = [("自定义", True), ("NFM", True)]
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("测试台", True)))
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(fake_double))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: groups.pop(0)))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a)))

    bm_panel._on_add_current()

    assert len(calls) == 2, "超范围后必须重新询问，而不是直接落库"
    assert calls[0]["max"] > 6000, "对话框自身不得把超范围值夹到 6000"
    assert warnings and "超出" in str(warnings[0][2])
    assert "6000" in str(warnings[0][2])
    stored = bm_panel._mgr.all()
    assert len(stored) == 1
    assert stored[0].frequency_hz == 145_500_000, "落库的应是用户重输的值"

