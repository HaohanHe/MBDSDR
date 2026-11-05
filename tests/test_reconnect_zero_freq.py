# SPDX-License-Identifier: MIT
"""UX-16（重连路径）：后端报 0 Hz 时不许显示 0.000 MHz。

反证：修前 ``_update_status_bar`` 直接 ``f_hz / 1e6`` 渲染，
``get_frequency() == 0`` 会在状态栏/顶栏/底坞写出 0.000 MHz 假频率。
"""
import os
import sys
from types import SimpleNamespace

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
    # MainWindow() 会读/写桌面配置与 QSettings；必须走临时 profile，
    # 否则会话级守卫会判定「改动了用户真实配置」（本次实测 mtime 被改）。
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("MainWindow teardown 失败")


class _ZeroBackend:
    """模拟重连后驱动尚未上报真实频点（get_frequency 返回 0）。"""

    device = SimpleNamespace(name="RTL-SDR")

    def get_status(self):
        return SimpleNamespace(gain_db=10.0, demod_mode=None, mode=None)

    def get_frequency(self):
        return 0.0

    def get_sample_rate(self):
        return 2_400_000.0


def test_zero_frequency_is_placeholder_not_fake_mhz(window):
    w = window
    w._active_sdr_backend = _ZeroBackend()

    w._update_status_bar()

    assert "0.000" not in w.status_freq.text(), (
        f"状态栏不得出现 0.000 MHz，实际: {w.status_freq.text()}")
    # 产品口径：缺值统一全角破折号 —（main_window.py:2596 `频率: —`）；
    # 旧断言写半角 "--"，属测试期望陈旧，不是产品回归。
    assert "—" in w.status_freq.text()
    assert "0.000" not in w.toolbar_freq_label.text(), (
        f"顶栏不得出现 0.000 MHz，实际: {w.toolbar_freq_label.text()}")
    assert "—" in w.toolbar_freq_label.text()
    assert "0.000" not in w.dock_freq_label.text(), (
        f"底坞不得出现 0.000 MHz，实际: {w.dock_freq_label.text()}")
    # 同一函数里三处读数同源，占位也必须同源（修复前顶栏/底坞吐半角 "-- MHz"）。
    assert "—" in w.dock_freq_label.text()
