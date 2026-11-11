# SPDX-License-Identifier: MIT
"""UX-05：断连后控制面板必须回到明确的「未连接」态。

反证：当前主窗口断连只调 ``set_sdr_connected(False)``（只置灰），不动
``_current_mode`` / ``_freq_hz`` / 模式下拉；面板仍显示 FM 和旧频率，
录音状态仍写「未录音」，看起来像还在工作。
"""
import os
import sys
import types

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


@pytest.fixture(scope="module")
def window(qtapp, isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    # 拆连接会写跟踪/收听状态，必须钉到临时目录，避免污染用户真实配置。
    w.settings = mw.DesktopSettings.load(
        str(isolated_profile / "desktop_settings.json"))
    yield w
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


def test_disconnect_clears_stale_panel_state(window):
    cp = window.control_panel
    cp.set_connected(True)
    cp.set_mode("FM")
    cp.set_frequency_hz(98_500_000.0)
    assert cp.mode_combo.currentIndex() >= 0

    window._panels_set_sdr_connected(False)

    assert cp._current_mode is None, "断连后不得残留解调模式"
    assert cp._freq_hz is None, "断连后不得残留旧频率真值"
    assert cp.mode_combo.currentIndex() == -1, (
        f"断连后模式下拉必须回到未选择，实际: {cp.mode_combo.currentText()}")
    assert cp.freq_display.frequency() is None, "断连后频率显示必须是占位"
    assert "未连接" in cp.record_status.text(), (
        f"断连后录音状态必须写明未连接，实际: {cp.record_status.text()}")
    assert cp.record_button.isEnabled() is False, "断连后录音按钮必须置灰"
    # 面板写对还不够：状态栏不得仍宣称录音在进行或已完成（只盯面板会漏一半）。
    msg = window.statusBar().currentMessage()
    assert "录音中" not in msg, f"断连后状态栏仍报录音中: {msg!r}"
    assert "完成" not in msg, f"断连后状态栏仍报完成态: {msg!r}"


def test_reconnect_restores_neutral_panel_state(window):
    cp = window.control_panel
    window._panels_set_sdr_connected(True)
    assert "未录音" in cp.record_status.text(), (
        f"重连且未录音时应显示未录音，实际: {cp.record_status.text()}")
    msg = window.statusBar().currentMessage()
    assert "未连接" not in msg, f"重连后状态栏仍报未连接: {msg!r}"


class _StubBackend:
    """只满足 ``_disconnect`` / ``_teardown_connection`` 前置检查的最小后端。"""

    def __init__(self):
        self.status = types.SimpleNamespace(
            error="", gain_db=0.0, demod_mode="FM", connected=True)
        self.device = types.SimpleNamespace(name="Stub SDR")

    def get_sample_rate(self):
        return 2.048e6

    def get_frequency(self):
        return 100.0e6

    def read_samples(self, n):
        import numpy as np
        return np.zeros(int(n), dtype=np.complex64)

    def close(self):
        pass


def test_disconnect_clears_connected_status_message(window):
    """用户点「断开」后，状态栏消息区不得还挂着「已连接 …」。

    反证：``_disconnect()`` 只调 ``_teardown_connection()``，后者刷新的是底部
    状态标签，**不覆盖消息区**；连接成功时写的「已连接 <设备名>」会一直留着
    （2026-11-11 探针实测：断开后消息区仍是「已连接 Stub SDR」，
    ``status_conn`` 已是「未连接」——面板说真话、状态栏不说）。
    """
    from PySide6.QtWidgets import QLabel

    window._active_sdr_backend = _StubBackend()
    window._panels_set_sdr_connected(True)
    window.statusBar().showMessage("已连接 Stub SDR", 8000)

    window._disconnect()

    msg = window.statusBar().currentMessage()
    labels = [lab.text() for lab in window.statusBar().findChildren(QLabel)]
    assert "已连接" not in msg, f"断开后消息区仍是已连接: {msg!r}"
    assert all("已连接" not in t for t in labels), \
        f"断开后状态栏标签仍含已连接: {labels!r}"
    assert ("断开" in msg) or ("未连接" in msg), \
        f"断开后缺少收口文案: {msg!r}"
