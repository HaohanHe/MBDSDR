# SPDX-License-Identifier: MIT
"""AI 工具转发 → UI 同步护栏（S11 / S12，offscreen）。

钉住 docs/interaction-flows.md 的两条红线：

- S11：工具失败或被人工门拦下时，界面**不得预先**跳到模型想要的频率/音量。
        只有硬件回执成功后才同步 UI。
- S12：模型省略 ``freq_mhz`` 时**直接拒绝转发**，绝不把演示频率 98.5MHz
        灌进控件；MCP 契约层同样拒绝（missing_argument）。

反证方式：把 ``_on_ai_tool_call`` 的预同步改回「转发前 set_freq」、或删掉
``_mcp_call_handler``/``_on_ai_tool_call`` 的缺参拒绝，本文件断言即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

log = logging.getLogger(__name__)


class _FakeSignal:
    """只记录 emit 的轻量信号替身（不依赖 Qt 事件循环投递）。"""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)

    def connect(self, *_a, **_k):
        pass


class _FakeWorker:
    def __init__(self):
        self.request_tool = _FakeSignal()
        self.synced = []

    def is_hardware_tool(self, _name):
        return True

    def call_tool_sync(self, tool, args):
        self.synced.append((tool, dict(args)))
        return {"ok": True}


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    from PySide6.QtCore import QTimer
    from main_window import MainWindow

    w = MainWindow()
    w.show()
    QTimer.singleShot(150, qtapp.quit)
    qtapp.exec()
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_ai_ui_sync_guards.py fixture 收尾失败"
                    "（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
    w.close()


def _install(w):
    fake = _FakeWorker()
    w._worker = fake
    # 写操作走真人门：测试里等价于「用户点了确认」
    w._approval_handler = lambda *_a: True
    w.control_panel._freq_hz = None
    w._pending_ui_sync.clear()
    return fake


def test_s12_missing_freq_not_forwarded(window):
    fake = _install(window)
    window._on_ai_tool_call("tune_fm", {})
    assert fake.request_tool.calls == [], "缺 freq_mhz 仍被转发到硬件"
    assert window.control_panel._freq_hz is None, "缺 freq_mhz 时 UI 被演示频率污染"


def test_s12_mcp_boundary_rejects_missing_freq(window):
    fake = _install(window)
    res = window._mcp_call_handler("tune_fm", {})
    assert res["success"] is False
    assert res["error"] == "missing_argument"
    assert fake.synced == [], "缺 freq_mhz 仍下发了硬件调用"


def test_s11_ui_not_presynced_before_result(window):
    fake = _install(window)
    window._on_ai_tool_call("tune_fm", {"freq_mhz": 120.0})
    assert fake.request_tool.calls == [("tune_fm", {"freq_mhz": 120.0})]
    assert window.control_panel._freq_hz is None, \
        "工具尚未执行，UI 已先跳到目标频率（S11 预改）"


def test_s11_failure_keeps_ui_unchanged(window):
    fake = _install(window)
    window._on_ai_tool_call("tune_fm", {"freq_mhz": 120.0})
    window._on_worker_tool_result("tune_fm", {"error": "timeout"})
    assert window.control_panel._freq_hz is None, \
        "硬件失败后 UI 仍停在目标频率（S11 假状态）"


def test_s11_success_syncs_ui(window):
    fake = _install(window)
    window._on_ai_tool_call("tune_fm", {"freq_mhz": 121.0})
    assert window.control_panel._freq_hz is None
    window._on_worker_tool_result("tune_fm", {"ok": True, "freq_mhz": 121.0})
    assert window.control_panel._freq_hz == pytest.approx(121.0e6), \
        "硬件成功后 UI 未跟随真实频率"


def test_s3_mcp_result_without_error_key_still_fails(window):
    """只有 success=False、没有 error 键时也必须判失败（同源判据）。

    旧式判读只看 ``"error" not in raw``，这种载荷会被当成成功——
    反证：把 ``_mcp_call_handler`` 的 ``tool_success`` 换成恒 None
    （即退回旧实现）本用例即红。
    """
    fake = _install(window)
    fake.call_tool_sync = lambda tool, args: {"success": False, "reason": "device busy"}
    res = window._mcp_call_handler("tune_fm", {"freq_mhz": 120.0})
    assert res["success"] is False, \
        "success=False 但无 error 键时被旧式判读当成了成功"
    assert window.control_panel._freq_hz is None, \
        "失败结果仍把 UI 同步到了目标频率"


def test_s3_worker_result_without_error_key_blocks_ui(window):
    """结果只有 success=False、没有 error 键时不得同步 UI（同源判据）。"""
    fake = _install(window)
    window._on_ai_tool_call("tune_fm", {"freq_mhz": 120.0})
    window._on_worker_tool_result("tune_fm", {"success": False, "reason": "timeout"})
    assert window.control_panel._freq_hz is None, \
        "结果只有 success=False 时 UI 仍被同步（同源判据未生效）"
