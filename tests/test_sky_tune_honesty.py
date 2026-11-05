# SPDX-License-Identifier: MIT
"""UX-01：天空点卫星时“已调谐”只许在真实成功后出现。

反证：当前 `_on_sky_object_clicked` 在没有 SDR 后端时也会把频率标签写成
145.8 MHz 并提示“已自动调谐”；本文件第一条断言即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import logging  # noqa: E402

import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    """整模块复用同一个窗口：MainWindow 构造很贵。"""
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        # 测试夹具不静默：断开失败要留日志，便于定位残留状态。
        _LOGGER.exception("MainWindow teardown 失败")


def _sat():
    from rf_sky_view import SkyObject
    return SkyObject("ISS (ZARYA)", 45.0, 30.0, frequency_hz=145.8e6)


class _GoodBackend:
    def __init__(self):
        self.freqs = []

    def set_frequency(self, hz):
        self.freqs.append(float(hz))


class _FailBackend:
    def set_frequency(self, hz):
        raise RuntimeError("device busy")


def test_no_backend_does_not_fake_tune(window):
    w = window
    w._active_sdr_backend = None
    w._worker = None
    w.freq_label.setText("-- MHz")
    before = w.control_panel.freq_display.frequency()

    w._on_sky_object_clicked(_sat())

    msg = w.statusBar().currentMessage()
    assert "已自动调谐" not in msg, f"无设备时不许说已调谐，实际: {msg}"
    assert "未调谐" in msg, f"必须说明没调，实际: {msg}"
    assert "下一步" in msg, f"必须给下一步，实际: {msg}"
    assert w.freq_label.text() == "-- MHz", (
        f"无设备时不许写频率，实际: {w.freq_label.text()}")
    assert w.control_panel.freq_display.frequency() == before, (
        "无设备时不许把卫星频率灌进控制面板")


def test_real_backend_success_claims_tuned(window):
    w = window
    backend = _GoodBackend()
    w._active_sdr_backend = backend
    w._worker = None

    w._on_sky_object_clicked(_sat())

    assert backend.freqs == [145_800_000.0], "必须真的把频率下发到后端"
    assert "已调谐到 145.800 MHz" in w.statusBar().currentMessage()
    assert w.freq_label.text() == "145.8 MHz"


def test_backend_failure_says_not_tuned(window):
    w = window
    w._active_sdr_backend = _FailBackend()
    w._worker = None
    w.freq_label.setText("-- MHz")
    before = w.control_panel.freq_display.frequency()

    w._on_sky_object_clicked(_sat())

    msg = w.statusBar().currentMessage()
    assert "未调谐" in msg, f"下发失败也不许说已调谐，实际: {msg}"
    assert "device busy" in msg, f"要带出失败原因，实际: {msg}"
    assert "下一步" in msg
    assert w.freq_label.text() == "-- MHz"
    assert w.control_panel.freq_display.frequency() == before
