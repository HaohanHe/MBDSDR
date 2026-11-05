# SPDX-License-Identifier: MIT
"""UX-02：调谐/增益/模式失败时，不提交 UI、不落配置、状态栏留原因。

反证：当前五个槽函数把后端异常 `except: pass` 掉，随后照样写频率标签、
改模式、`settings.set(...)`；本文件的配置与标签断言在修前全红。
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


class _BadBackend:
    """模拟 device is busy：所有硬件写入口都抛错。"""

    def set_frequency(self, hz):
        raise RuntimeError("device is busy")

    def set_demod(self, mode):
        raise RuntimeError("device is busy")

    def set_gain(self, db):
        raise RuntimeError("device is busy")


def _arm_bad(w):
    w._active_sdr_backend = _BadBackend()
    w._worker = None
    w._backend_center_hz = 100e6
    w._vfo_center_hz = 100e6
    w._demod_cfg.mode = "AM"
    w.control_panel.set_mode("AM")
    w.control_panel.set_frequency_hz(100e6)
    w.freq_label.setText("100.000 MHz")
    w.status_freq.setText("频率: 100.000 MHz")
    w.settings.set("frequency_hz", 100e6)
    w.settings.set("demod_mode", "AM")
    w.settings.set("gain_db", 20.0)


def test_tune_sdr_failure_does_not_commit_or_persist(window):
    w = window
    _arm_bad(w)

    w._on_tune_sdr(145.8e6, "FM")

    assert w.freq_label.text() == "100.000 MHz", (
        f"失败不许写假频率，实际: {w.freq_label.text()}")
    assert w.control_panel._freq_hz == 100e6, "失败要把控制面板频率拉回上次成功值"
    assert w.control_panel.mode_combo.currentText() == "AM", "失败不许改面板模式"
    assert w._demod_cfg.mode == "AM", "失败不许改解调真值"
    assert w.settings.get("frequency_hz") == 100e6, "失败不许持久化频率"
    assert w.settings.get("demod_mode") == "AM", "失败不许持久化解调模式"
    msg = w.statusBar().currentMessage()
    assert "调谐未生效" in msg, f"状态栏必须留原因，实际: {msg}"
    assert "device is busy" in msg
    assert "下一步" in msg


def test_tune_fm_failure_keeps_last_good(window):
    w = window
    _arm_bad(w)

    w._on_tune_fm(145.8)

    assert w.freq_label.text() == "100.000 MHz"
    assert w.control_panel._freq_hz == 100e6


def test_gain_failure_does_not_persist(window):
    w = window
    _arm_bad(w)

    w._on_gain_changed(49)

    assert w.settings.get("gain_db") == 20.0, "增益失败不许持久化"
    msg = w.statusBar().currentMessage()
    assert "设置增益未生效" in msg, f"状态栏必须留原因，实际: {msg}"
    assert "device is busy" in msg


def test_mode_failure_reverts_combo_and_config(window):
    w = window
    _arm_bad(w)

    w._on_mode_changed("USB")

    assert w.settings.get("demod_mode") == "AM", "模式失败不许持久化"
    assert w._demod_cfg.mode == "AM"
    assert w.control_panel.mode_combo.currentText() == "AM", "失败要把下拉拉回"
    msg = w.statusBar().currentMessage()
    assert "切换解调模式未生效" in msg, f"状态栏必须留原因，实际: {msg}"
    assert "device is busy" in msg
