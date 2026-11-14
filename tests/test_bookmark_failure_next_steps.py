# SPDX-License-Identifier: MIT
"""C1–C5：书签五个写操作失败时必须给「原因 + 下一步」，不能只甩异常原文。

五处：添加 / 编辑 / 删除 / 导入 CSV / 导出 CSV。判据统一：
打桩 ``QMessageBox.warning`` 捕获正文，断言含
``原因：<异常>`` 与 ``下一步：``，且写明数据是否仍在。

反证：把 ``_warn_action_failed`` 换回 ``QMessageBox.warning(self, t, _clean_exc(e))``
后，五条用例同时红（正文里没有「下一步」）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qtapp, tmp_path):
    from bookmark_panel import BookmarkPanel
    p = BookmarkPanel(config_dir=str(tmp_path))
    yield p
    p.close()


@pytest.fixture
def warned(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: seen.append(a)))
    return seen


def _seed(panel, name="原名"):
    from mbdsdr_ai.bookmark_manager import Bookmark
    panel._mgr.add(Bookmark(frequency_hz=98_500_000, name=name,
                            modulation="FM", bandwidth_hz=200_000))
    panel.refresh_list()
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))


def _assert_three_parts(seen, title, reason="boom", extra=""):
    assert seen, f"{title}必须给出可见提示"
    text = str(seen[0][2])
    assert "原因：" in text and reason in text, f"缺少原因: {text}"
    assert "下一步：" in text, f"缺少可执行的下一步: {text}"
    if extra:
        assert extra in text, f"下一步应写到「{extra}」: {text}"


def test_add_failure_states_next_step(panel, warned, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    def boom(self, bm):
        raise OSError("boom")

    monkeypatch.setattr(type(panel._mgr), "add", boom)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("本地台", True)))
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (98.5, True)))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("自定义", True)))
    panel._current_freq_provider = lambda: 98_500_000.0

    panel._on_add_current()

    _assert_three_parts(warned, "添加失败", extra="bookmarks.json")
    assert "未写入" in str(warned[0][2])


def test_edit_failure_states_next_step(panel, warned, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    def boom(self, *a, **k):
        raise OSError("boom")

    _seed(panel)
    monkeypatch.setattr(type(panel._mgr), "update", boom)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("新名", True)))

    panel._on_edit()

    _assert_three_parts(warned, "编辑失败", extra="bookmarks.json")
    assert "原名保持不变" in str(warned[0][2])


def test_delete_failure_states_next_step(panel, warned, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def boom(self, *a, **k):
        raise OSError("boom")

    _seed(panel)
    monkeypatch.setattr(type(panel._mgr), "remove", boom)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))

    panel._on_delete()

    _assert_three_parts(warned, "删除失败", extra="bookmarks.json")
    assert "仍在" in str(warned[0][2])


def test_import_failure_states_next_step(panel, warned, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    def boom(self, text):
        raise ValueError("boom")

    csv = tmp_path / "in.csv"
    csv.write_text("98.5,测试,FM\n", encoding="utf-8")
    monkeypatch.setattr(type(panel._mgr), "import_csv_text", boom)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(csv), "")))

    panel._on_import_csv()

    _assert_three_parts(warned, "导入失败", extra="UTF-8")
    assert "未导入任何书签" in str(warned[0][2])


def test_export_failure_states_next_step(panel, warned, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    def boom(self):
        raise OSError("boom")

    monkeypatch.setattr(type(panel._mgr), "export_csv_text", boom)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k:
                                     (str(tmp_path / "out.csv"), "")))

    panel._on_export_csv()

    _assert_three_parts(warned, "导出失败", extra="写权限")
    assert "原书签数据不受影响" in str(warned[0][2])
