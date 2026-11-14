# SPDX-License-Identifier: MIT
"""A3：快捷键「全部恢复默认」必须先确认，默认按钮是「否」。

判据：
  * 点「全部恢复默认」时 ``QMessageBox.question`` 被调用，按钮含 Yes|No，
    **第 4 个实参（默认按钮）必须是 No**——误触回车不会清掉键位；
  * 正文写明影响条数与「不能撤销」；
  * 选「否」时覆写原样保留、列表不刷新；选「是」才清空并刷新。

反证：去掉 ``_on_reset_all`` 里的确认（直接 ``clear_all_overrides()``），
第一条用例的 ``seen == []`` 与「选否仍保留」同时变红。
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import shortcuts  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """QSettings 指到临时 ini；清空覆写缓存保证用例互不影响。"""
    from PySide6.QtCore import QSettings

    ini = str(tmp_path / "shortcuts.ini")
    monkeypatch.setattr(shortcuts, "_store",
                        lambda: QSettings(ini, QSettings.IniFormat))
    monkeypatch.setattr(shortcuts, "_OVERRIDES", None, raising=False)
    shortcuts.clear_all_overrides()
    yield ini
    shortcuts.clear_all_overrides()
    monkeypatch.setattr(shortcuts, "_OVERRIDES", None, raising=False)


def _panel():
    from shortcuts_panel import ShortcutsPanel
    return ShortcutsPanel()


def test_reset_all_asks_with_default_no(qtapp, isolated_store, monkeypatch):
    shortcuts.set_override("file.connect", "Ctrl+Alt+9")
    shortcuts.set_override("tools.record", "Ctrl+Alt+8")
    panel = _panel()
    seen = []

    def fake_question(parent, title, text, buttons, default):
        seen.append((title, text, buttons, default))
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    try:
        panel._on_reset_all()
    finally:
        panel.deleteLater()

    assert seen, "「全部恢复默认」必须先确认，不能直接清空"
    _title, text, buttons, default = seen[0]
    assert buttons & QMessageBox.Yes and buttons & QMessageBox.No
    assert default == QMessageBox.No, "默认按钮必须是「否」，回车不能清键位"
    assert "2 条" in text, "必须写明会影响多少条自定义键位"
    assert "不能撤销" in text, "必须写明后果"
    assert shortcuts.is_overridden("file.connect"), "选「否」必须保留原键位"
    assert shortcuts.is_overridden("tools.record")


def test_reset_all_yes_actually_clears(qtapp, isolated_store, monkeypatch):
    shortcuts.set_override("file.connect", "Ctrl+Alt+9")
    panel = _panel()
    refreshed = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(panel, "_after_change",
                        lambda: refreshed.append(True))
    try:
        panel._on_reset_all()
    finally:
        panel.deleteLater()

    assert not shortcuts.is_overridden("file.connect")
    assert refreshed == [True], "确认后必须立刻刷新列表/热更新键位"
