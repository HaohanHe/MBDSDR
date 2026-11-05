# SPDX-License-Identifier: MIT
"""S8 断源感知回归（无硬件，offscreen）。

钉住 docs/interaction-flows.md S8 / docs/PRD.md AC-06.7：

- 读线程报"连续无数据"后，后端 status 报错/未连接 → 如实断开：禁用依赖控件、
  清频谱、状态栏写明「设备已断开：<原因>」。
- 读线程卡死（read_failed 永远发不出来）时，UI 每秒按 last_data_age_s 兜底判掉线。
- 首帧宽限期内不误报；后端不报新鲜度（None）时不擅自断开，交给信号路径。
- 短暂空读（<3 个周期）只提示"正在重试"，不拆连接。

反证方式：把 `_on_health_tick` 删掉或去掉 `_panels_set_sdr_connected(False)`
路径，本文件断言即红。
"""
import os
import sys
import time
import logging
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

log = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _StubBackend:
    """主窗口判掉线所需的最小后端接口（只读 status，不读样）。"""

    def __init__(self, connected=True, error="", age=None):
        self.connected = connected
        self.error = error
        self.age = age
        self.disconnect_calls = 0

    def get_status(self):
        return SimpleNamespace(connected=self.connected, error=self.error,
                               last_data_age_s=self.age)

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


class _FakePipeline:
    """只要能被 shutdown，证明掉线收尾真的拆了流水线。"""

    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    """整模块复用同一窗口：MainWindow 构造很贵。

    isolated_profile 见 tests/conftest.py：MainWindow 会 load/save 真实
    desktop_settings.json，必须先把 HOME/APPDATA 重定向到临时目录。
    """
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(30)
    yield w
    try:
        w._teardown_connection()
    except Exception as exc:  # noqa: BLE001 - 收尾失败不该掩盖用例结果，但要留证
        log.warning("S8 用例收尾拆连接失败：%s", exc)


def _prepare(w, backend, pipeline=None):
    """把窗口置成"已连接且在跑流水线"，清掉上一条用例的残留计数与状态栏。"""
    w._teardown_connection()
    w.statusBar().clearMessage()
    w._read_fail_signals = 0
    w._backend_lost_handled = False
    w._active_sdr_backend = backend
    w._pipeline = pipeline if pipeline is not None else _FakePipeline()
    w._health_grace_until = 0.0
    w._health_has_data = True
    return w


class TestS8BackendLoss:
    def test_backend_error_is_reported_as_disconnected(self, window):
        b = _StubBackend(connected=True, error="读取 IQ 失败: 连接已被对端关闭")
        w = _prepare(window, b)
        w._on_health_tick()
        assert w._active_sdr_backend is None, "status 报错必须按掉线收尾"
        msg = w.statusBar().currentMessage()
        assert msg.startswith("设备已断开："), msg
        assert "连接已被对端关闭" in msg, msg

    def test_stale_data_marks_lost_without_any_signal(self, window):
        """读线程卡死时没有任何 read_failed：只能靠 1s 轮询兜底。"""
        b = _StubBackend(connected=True, error="", age=5.0)
        w = _prepare(window, b)
        w._on_health_tick()
        assert w._active_sdr_backend is None, "5s 没数据必须判掉线"
        msg = w.statusBar().currentMessage()
        assert msg.startswith("设备已断开："), msg
        assert "未收到 IQ 数据" in msg, msg
        # AC-06.7：依赖控件禁用、状态"未连接"、不留旧帧
        assert w.control_panel.freq_input.isEnabled() is False
        assert w.status_conn.text() == "未连接"

    def test_not_connected_status_marks_lost(self, window):
        b = _StubBackend(connected=False, error="", age=0.1)
        w = _prepare(window, b)
        w._on_health_tick()
        assert w._active_sdr_backend is None
        assert "后端报告未连接" in w.statusBar().currentMessage()

    def test_fresh_data_keeps_connection(self, window):
        b = _StubBackend(connected=True, error="", age=0.3)
        w = _prepare(window, b)
        w._on_health_tick()
        assert w._active_sdr_backend is b, "数据新鲜时绝不能误判掉线"
        assert not w.statusBar().currentMessage().startswith("设备已断开")

    def test_grace_window_suppresses_false_alarm(self, window):
        b = _StubBackend(connected=True, error="", age=30.0)
        w = _prepare(window, b)
        w._health_grace_until = time.monotonic() + 2.0
        w._on_health_tick()
        assert w._active_sdr_backend is b, "首帧宽限期内不得据此断开"

    def test_unknown_freshness_is_left_to_signal_path(self, window):
        b = _StubBackend(connected=True, error="", age=None)
        w = _prepare(window, b)
        w._on_health_tick()
        assert w._active_sdr_backend is b, "后端不报新鲜度时不得猜掉线"

    def test_no_frame_yet_is_not_called_lost(self, window):
        """刚连上还没出数据：轮询不得抢在 read_failed/宽限期之前判掉线。"""
        b = _StubBackend(connected=True, error="", age=9.0)
        w = _prepare(window, b)
        w._health_has_data = False
        w._on_health_tick()
        assert w._active_sdr_backend is b

    def test_short_stall_only_warns_then_third_strike_disconnects(self, window):
        b = _StubBackend(connected=True, error="", age=None)
        w = _prepare(window, b)
        w._read_fail_signals = 1
        w._on_pipeline_read_failed("read_samples 连续无数据")
        assert w._active_sdr_backend is b, "单次空读只告警"
        assert "正在重试" in w.statusBar().currentMessage()

        w._read_fail_signals = 2
        w._on_pipeline_read_failed("read_samples 连续无数据")
        assert w._active_sdr_backend is None, "连续三个周期仍无数据必须断开"
        assert w.statusBar().currentMessage().startswith("设备已断开：")
