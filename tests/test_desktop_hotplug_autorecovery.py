# SPDX-License-Identifier: MIT
"""桌面热插拔自动恢复（期望行为，当前未接线）。

背景：`mbdsdr_ai/reconnection.py` / `device_manager.py` 在库层可用，但
`desktop/` 下对 `ReconnectionManager`、`DeviceManager.start_monitor` /
`stop_monitor` 的引用为 **0 处**。主窗口掉线路径只做 `_teardown_connection()`
+ 状态栏提示，重连靠用户手动点「连接」。

本用例按「设备拔掉→再插回→主窗口自动恢复」写期望。当前必红，红即结论：
桌面没有自动重连/热插拔接入。若未来接线，去掉 xfail 即转为回归保护。
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
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass


class _FakeBackend:
    """可被「拔出/插回」的假后端。PRESENT 模拟设备是否存在。"""

    PRESENT = True

    def __init__(self):
        self.device = SimpleNamespace(name="RTL-SDR", serial="audit-0001")

    def get_status(self):
        if not type(self).PRESENT:
            return SimpleNamespace(connected=False, error="USB 设备已拔出",
                                   last_data_age_s=None)
        return SimpleNamespace(connected=True, error="", last_data_age_s=0.0)

    def get_frequency(self):
        return 100_000_000.0

    def get_sample_rate(self):
        return 2_400_000.0


@pytest.mark.xfail(
    strict=True,
    reason="桌面未接入自动重连/热插拔：库层可用，UI 只做手动重连"
           "（docs/limitations-register.md）",
)
def test_main_window_auto_recovers_after_replug(window):
    from PySide6.QtTest import QTest

    w = window
    backend = _FakeBackend()
    type(backend).PRESENT = True
    w._active_sdr_backend = backend

    # 设备消失：走产品自己的掉线收尾路径。
    type(backend).PRESENT = False
    w._handle_backend_lost("USB 设备已拔出")
    assert w._active_sdr_backend is None

    # 设备重新出现：给产品 2 个健康检查周期（_health_timer 1s/拍）机会自动恢复。
    type(backend).PRESENT = True
    QTest.qWait(2200)

    assert w._active_sdr_backend is not None, (
        "设备插回后主窗口未自动恢复连接（桌面未接入热插拔/自动重连）")
    assert w._active_sdr_backend.get_status().connected is True
    # 依赖控件应随恢复解除置灰。
    assert w.connect_btn.isEnabled() is True
