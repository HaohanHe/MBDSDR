# SPDX-License-Identifier: MIT
"""设备切换路径回归（无硬件，offscreen）。

钉住 docs/interaction-flows.md 里的 B1 / S3 / S15：

- B1：切到新设备必须先拆旧连接（停读线程 + 释放硬件），否则旧后端继续供数、
  "界面换了设备、数据还是旧的"。
- B1 回滚：新设备连不上时必须恢复旧连接，不能停在"未连接"却假装切成功。
- S3：流水线重建（换采样率/换设备）不得删用户 VFO，也不得用后端
  status.demod_mode 覆盖用户的解调模式。
- S15：断开设备必须停卫星跟踪定时器。

反证方式：删掉 main_window 里的 `_teardown_connection` 前置拆除、VFO 复用、
或 `_stop_satellite_tracking` 调用，本文件断言即红。
"""
import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


class _StubDevice:
    def __init__(self, name: str):
        self.name = name
        self.max_gain = 49.0


class _StubBackend:
    """主窗口依赖的最小后端接口；read 有真实调用计数。"""

    def __init__(self, name: str, fail_connect: bool = False):
        self.device = _StubDevice(name)
        self.status = SimpleNamespace(error="", gain_db=20.0, demod_mode="FM")
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.read_calls = 0
        self.freq = 98.0e6
        self.sr = 2.048e6
        self.dead = False
        self._fail_connect = fail_connect

    def connect(self):
        self.connect_calls += 1
        if self._fail_connect:
            self.status.error = "connect 失败: 故意"
            self.connected = False
            return False
        self.connected = True
        return True

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def get_status(self):
        return SimpleNamespace(connected=self.connected, error=self.status.error,
                               gain_db=self.status.gain_db,
                               demod_mode=self.status.demod_mode)

    def get_sample_rate(self):
        return self.sr

    def set_sample_rate(self, r):
        self.sr = float(r)

    def get_frequency(self):
        return self.freq

    def set_frequency(self, f):
        self.freq = float(f)

    def read_samples(self, n):
        self.read_calls += 1
        if self.dead:
            return None
        time.sleep(0.001)
        return np.zeros(int(n), dtype=np.complex64)

    def subscribe_iq(self, _name):
        return SimpleNamespace(read=self.read_samples)

    def set_demod(self, _m):
        pass

    def set_bandwidth(self, _b):
        pass

    def set_ppm(self, _p):
        pass

    def set_offset_tuning(self, _b):
        pass

    def set_agc(self, _b):
        pass

    def set_gain(self, _g):
        pass


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    """整模块复用同一个窗口：MainWindow 构造很贵，逐测重建会把本文件拖成分钟级。"""
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(30)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass


@pytest.mark.usefixtures("qtapp", "window")
class TestDeviceSwitchFlow:
    def _window(self, window):
        from PySide6.QtTest import QTest
        # 上一个用例若失败可能留下在途连接，先幂等拆除
        window._teardown_connection()
        QTest.qWait(20)
        return window

    def test_switch_releases_old_backend_then_reads_new(self, window):
        from PySide6.QtTest import QTest
        w = self._window(window)
        a = _StubBackend("A")
        assert w._connect_backend(a) is True
        QTest.qWait(150)
        assert a.read_calls > 0, "旧设备连接后应真实读样"

        b = _StubBackend("B")
        assert w._connect_backend(b) is True
        QTest.qWait(150)
        assert a.disconnect_calls >= 1, "切换设备必须释放旧后端"
        assert a.connected is False
        assert w._active_sdr_backend is b
        a_reads_at_switch = a.read_calls
        QTest.qWait(150)
        assert b.read_calls > 0, "新连接必须在真读样"
        assert a.read_calls == a_reads_at_switch, "旧后端不得继续被读"
        w._teardown_connection()

    def test_failed_switch_rolls_back_to_old_backend(self, window):
        from PySide6.QtTest import QTest
        w = self._window(window)
        a = _StubBackend("A")
        assert w._connect_backend(a) is True
        QTest.qWait(100)
        bad = _StubBackend("bad", fail_connect=True)
        assert w._connect_backend(bad) is False
        QTest.qWait(150)
        assert w._active_sdr_backend is a, "切换失败必须回滚旧连接"
        assert a.connected is True
        assert bad.connected is False
        assert "已恢复原设备" in w.statusBar().currentMessage()
        w._teardown_connection()

    def test_rebuild_keeps_user_vfo_and_user_mode(self, window):
        from PySide6.QtTest import QTest
        w = self._window(window)
        a = _StubBackend("A")
        assert w._connect_backend(a) is True
        QTest.qWait(100)
        w._on_new_vfo_button()
        vfo_count = len(w._vfo_mgr.list_all())
        assert vfo_count >= 2

        w._on_mode_changed("USB")
        assert w._demod_cfg.mode == "USB"
        # 后端自报 FM：只读，不得覆盖用户设置
        a.status.demod_mode = "FM"

        w._on_sample_rate_changed(1_000_000.0)
        QTest.qWait(150)
        assert len(w._vfo_mgr.list_all()) == vfo_count, "重建流水线不得删用户 VFO"
        assert w._demod_cfg.mode == "USB", "后端 demod_mode 不得覆盖用户模式"
        w._on_mode_changed("FM")
        w._teardown_connection()

    def test_disconnect_stops_satellite_tracking(self, window):
        from PySide6.QtCore import QTimer
        from PySide6.QtTest import QTest
        w = self._window(window)
        a = _StubBackend("A")
        assert w._connect_backend(a) is True
        t = QTimer(w)
        t.setInterval(500)
        t.timeout.connect(lambda: None)
        t.start()
        w._sat_track_timer = t
        w._disconnect()
        QTest.qWait(50)
        assert t.isActive() is False, "断开设备必须停跟踪定时器"
        assert w._active_sdr_backend is None

    def test_read_stall_marks_ui_disconnected(self, window):
        """rtl_tcp 断线：读线程持续空读 + 后端 status.connected=False → UI 如实断开。"""
        from PySide6.QtTest import QTest
        w = self._window(window)
        a = _StubBackend("A")
        assert w._connect_backend(a) is True
        QTest.qWait(100)
        a.dead = True                 # 之后 read_samples 恒为 None
        a.connected = False
        a.status.error = "读取 IQ 失败：连接已断开"
        QTest.qWait(2500)             # 阈值 200×2ms ≈0.4s，留足余量
        assert w._active_sdr_backend is None, "掉线后不得仍显示已连接"
        assert "设备已断开" in w.statusBar().currentMessage()
        assert w.disconnect_btn.isEnabled() is False

    def test_ai_write_requires_human_approval(self, window, tmp_path):
        """S13：模型自带 arm_manual=true 不构成确认；真人拒绝＝不下发。"""
        import json
        w = self._window(window)
        calls = []

        class _Worker:
            def call_tool_sync(self, name, args):
                calls.append((name, dict(args)))
                return {"success": True}

            def is_hardware_tool(self, name):
                return True

        w._worker = _Worker()
        w._approval_audit_path = str(tmp_path / "audit.jsonl")

        w._approval_handler = lambda tool, args: False
        res = w._mcp_call_handler("tune_fm", {"freq_mhz": 100.0, "arm_manual": True})
        assert res["error"] == "awaiting_human_approval"
        assert calls == [], "未获真人确认不得下发硬件"
        rec = json.loads(open(w._approval_audit_path, encoding="utf-8").read().splitlines()[-1])
        assert rec["approved"] is False and rec["tool"] == "tune_fm"

        w._approval_handler = lambda tool, args: True
        res = w._mcp_call_handler("tune_fm", {"freq_mhz": 100.0, "arm_manual": True})
        assert res.get("success") is True
        assert calls and "arm_manual" not in calls[0][1], "模型自报确认必须被剥离"

