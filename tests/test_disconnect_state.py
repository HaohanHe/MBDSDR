# SPDX-License-Identifier: MIT
"""UX-05：断连后控制面板必须回到明确的「未连接」态。

反证：当前主窗口断连只调 ``set_sdr_connected(False)``（只置灰），不动
``_current_mode`` / ``_freq_hz`` / 模式下拉；面板仍显示 FM 和旧频率，
录音状态仍写「未录音」，看起来像还在工作。
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
