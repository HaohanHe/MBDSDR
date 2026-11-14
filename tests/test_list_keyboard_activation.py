# SPDX-License-Identifier: MIT
"""D1–D4：列表必须能用键盘回车激活，而不是只认鼠标双击/单击。

判据：给列表设当前项 → ``QTest.keyClick(view, Qt.Key_Return)``，
目标动作必须发生（发调谐请求 / 发活动段 / 发解调链 / 预览历史图）。

反证：把 ``itemActivated`` 换回 ``itemDoubleClicked``（或删掉新增的
``itemActivated`` 连接），对应用例全部红——回车什么都不会发生。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _press_enter(view, item):
    view.setCurrentItem(item)
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Return)


def test_bookmark_enter_tunes(qtapp, tmp_path):
    from bookmark_panel import BookmarkPanel
    from mbdsdr_ai.bookmark_manager import Bookmark

    panel = BookmarkPanel(config_dir=str(tmp_path))
    got = []
    panel.tune_requested.connect(lambda *a: got.append(a))
    try:
        panel._mgr.add(Bookmark(frequency_hz=98_500_000, name="本地台",
                                modulation="FM", bandwidth_hz=200_000))
        panel.refresh_list()
        item = panel.tree.topLevelItem(0)
        assert item is not None
        _press_enter(panel.tree, item)
    finally:
        panel.close()

    assert got == [(98_500_000.0, "FM", 200_000.0)], \
        "书签列表回车必须等于双击（发调谐请求）"


def test_scanner_enter_selects_segment(qtapp):
    from scanner_panel import ScannerPanel

    panel = ScannerPanel()
    got = []
    panel.segment_chosen.connect(got.append)
    seg = {"start_hz": 145_500_000.0, "end_hz": 145_600_000.0,
           "peak_hz": 145_550_000.0, "snr_db": 12.0}
    try:
        it = QListWidgetItem("145.550 MHz")
        it.setData(Qt.UserRole, seg)
        panel.result_list.addItem(it)
        _press_enter(panel.result_list, it)
    finally:
        panel.close()

    assert got == [seg], "扫频结果列表回车必须选中该活动段"


def test_modulation_enter_applies_suggestion(qtapp):
    from modulation_panel import ModulationPanel

    panel = ModulationPanel()
    got = []
    panel.apply_demod_requested.connect(lambda *a: got.append(a))
    try:
        panel._last = SimpleNamespace(modulation="FM", bandwidth=12_500.0)
        it = QListWidgetItem("NFM 12.5 kHz")
        panel.suggest_list.addItem(it)
        _press_enter(panel.suggest_list, it)
    finally:
        panel.close()

    assert got == [("NFM", 12_500.0)], "建议解调链回车必须等于双击应用"


def test_satellite_history_enter_previews(qtapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QPixmap
    import satellite_image_panel as sip

    monkeypatch.setattr(sip, "_IMG_DIR", str(tmp_path))
    png = tmp_path / "NOAA19_20261114_lrpt.png"
    pm = QPixmap(4, 4)
    pm.fill(Qt.red)
    assert pm.save(str(png))

    panel = sip.SatelliteImagePanel()
    try:
        panel._load_history()
        item = panel.history.item(0)
        assert item is not None
        _press_enter(panel.history, item)
    finally:
        panel.close()

    assert panel._current_png == str(png), \
        "历史图像列表回车必须打开该图（与鼠标单击一致）"
    assert panel.save_btn.isEnabled()


def test_weather_history_enter_previews(qtapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QPixmap
    import weather_panel as wp

    monkeypatch.setattr(wp, "_OUT_DIR", str(tmp_path))
    png = tmp_path / "weather_20261114_1200.png"
    pm = QPixmap(4, 4)
    pm.fill(Qt.blue)
    assert pm.save(str(png))

    panel = wp.WeatherPanel()
    try:
        panel._load_history()
        item = panel.history.item(0)
        assert item is not None
        _press_enter(panel.history, item)
    finally:
        panel.close()

    assert panel._current_png == str(png), \
        "气象历史产物列表回车必须打开该图（与鼠标单击一致）"
    assert panel.save_btn.isEnabled()
