# SPDX-License-Identifier: MIT
"""8b：内置书签必须在列表里看得出是「内置」。

反证：修前主窗口把 ``FrequencyManager`` 的 Bookmark 压成 3 元组，丢掉
``builtin`` 字段，内置项与用户收藏在控制面板里外观完全一致。
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


def test_panel_labels_builtin_bookmark(panel):
    panel.load_bookmarks_from_list([
        (121_500_000.0, "航空应急频率 121.500", "AM", True),
        (98_500_000.0, "我的本地台", "FM", False),
    ])
    builtin_text = panel.bookmark_combo.itemText(0)
    custom_text = panel.bookmark_combo.itemText(1)
    assert "内置" in builtin_text, f"内置项必须标注，实际: {builtin_text}"
    assert "内置" not in custom_text, f"自定义项不该标内置，实际: {custom_text}"


_EXPECTED_BUILTIN_NOTE = "收藏当前频率"


class _FakeStore:
    def __init__(self):
        self.removed = []

    def remove(self, name):
        self.removed.append(name)
        return True


def test_builtin_bookmark_delete_is_refused_with_next_step(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = _FakeStore()
    panel.set_bookmark_store(store)
    panel.load_bookmarks_from_list([
        (121_500_000.0, "航空应急频率 121.500", "AM", True),
    ])
    infos = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: infos.append(a)))

    panel._on_bookmark_del()

    assert infos, "删内置项必须说明为什么不能删"
    text = str(infos[0][2])
    assert "不能删除" in text
    assert _EXPECTED_BUILTIN_NOTE in text, "必须给出可执行的下一步"
    assert len(panel._bookmarks) == 1, "内置项不得被删掉"
    assert store.removed == [], "内置项不得落盘删除"


@pytest.fixture(scope="module")
def window(qtapp, isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


def test_main_window_passes_builtin_to_panel(window):
    combo = window.control_panel.bookmark_combo
    texts = [combo.itemText(i) for i in range(combo.count())]
    hit = [t for t in texts if "121.500" in t]
    assert hit, "内置 121.500 书签应出现在控制面板列表里"
    assert "内置" in hit[0], (
        "主窗口必须把 Bookmark.builtin 传给控制面板，实际: " + hit[0])
