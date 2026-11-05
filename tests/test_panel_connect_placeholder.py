# SPDX-License-Identifier: MIT
"""UX-16：连接后不得用 0 Hz 顶替“未知频率”。

反证：当前 `_panels_set_sdr_connected(True)` 在频率仍为占位时调用
`fd.set_frequency(0)`，`frequency()` 返回 0；本文件第一条断言即红。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        _LOGGER.exception("MainWindow teardown 失败")


def test_connect_with_no_frequency_keeps_placeholder(window):
    w = window
    w.control_panel._freq_hz = None
    w.control_panel.freq_display.set_frequency(None)

    w._panels_set_sdr_connected(True)

    assert w.control_panel.freq_display.frequency() is None, (
        "没有真实频率时必须保持占位，不许回填 0 Hz")


def test_connect_restores_real_frequency(window):
    w = window
    w.control_panel.set_frequency_hz(145.8e6)

    w._panels_set_sdr_connected(True)

    assert w.control_panel.freq_display.frequency() == 145.8e6, (
        "真实保存/调谐过的频率在连接后要恢复显示")
