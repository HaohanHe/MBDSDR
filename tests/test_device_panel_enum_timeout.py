# SPDX-License-Identifier: MIT
"""设备枚举看门狗：枚举卡死不许让热插拔检测静默死亡。

历史缺陷：``_DeviceEnumTask`` 在 ``enumerate_all_sdr_devices()`` 里卡住
（USB open 无超时）时 ``finished`` 永不发射，``_enum_inflight`` 永久为 True，
之后每一拍轮询都在开头被跳过 —— 热插拔检测整体死亡，且日志/状态里没有任何
痕迹。

本文件锁死三件事：
  (a) 超过上限（``ENUM_TIMEOUT_MS``）后 ``_enum_inflight`` 必须复位；
  (b) 下一拍轮询确实又发起了枚举（本次失败可重试，不是一次就永久停摆）；
  (c) 状态行与日志里能看到「枚举超时」，不是静默。
另加两条边界：超时后迟到的旧结果不许清掉新一轮 inflight；正常返回要停表，
不能在之后误报超时。刷新路径（``_refresh_inflight``）同样有看门狗，因为它
卡住同样会挡住热插拔轮询。
"""
from __future__ import annotations

import logging
import sys
import threading
import time

import pytest

_ROOT_DIR = __file__.rsplit("tests", 1)[0]
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


@pytest.fixture(autouse=True)
def _isolated(isolated_profile):
    yield


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _pump(ms: int) -> None:
    """跑一段事件循环（让 QTimer 到点触发），不 sleep 整段。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


class _NeverFinishingPool:
    """模拟「任务永不返回」：只记录派发，从不执行。"""

    def __init__(self):
        self.started = []

    def start(self, runnable):
        self.started.append(runnable)


@pytest.fixture
def panel(monkeypatch):
    _app()
    import desktop.device_panel as dp

    # 首次刷新由 singleShot(400ms) 派发；本文件每次收盘都在它们之前，
    # 因此不需要额外抑制（个别用例拉长事件循环时自行 monkeypatch）。
    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "ENUM_TIMEOUT_MS", 100)
    pool = _NeverFinishingPool()
    monkeypatch.setattr(
        dp, "QThreadPool",
        type("_QP", (), {"globalInstance": staticmethod(lambda: pool)}))
    widget = dp.DevicePanel()
    widget._hotplug_timer.stop()   # 全部由用例手动驱动，结果可复现
    yield widget, dp, pool
    widget.close()


def test_enum_watchdog_resets_inflight_and_retries(panel, caplog):
    p, dp, pool = panel
    with caplog.at_level(logging.WARNING, logger=dp.__name__):
        p._on_hotplug_poll()
        assert p._enum_inflight is True
        assert len(pool.started) == 1
        stale_token = pool.started[0]._token

        _pump(300)                     # > 100ms 上限，且不到 2s 轮询间隔
        assert p._enum_inflight is False, "(a) 超时后 inflight 必须复位"
        assert "枚举超时" in p.status_label.text(), "(c) 状态行必须可见"
        assert any("枚举超时" in r.getMessage() for r in caplog.records), \
            "(c) 日志必须留下证据，不许静默"

        p._on_hotplug_poll()           # (b) 下一拍重新派发
        assert len(pool.started) == 2, "(b) 超时后必须能重试枚举"
        assert p._enum_inflight is True
        assert pool.started[1]._token != stale_token

        # 旧任务迟到：不许把新一轮的 inflight 清掉（否则下一拍重复派发）
        p._enum_signals.finished.emit([], "", stale_token)
        assert p._enum_inflight is True

        # 当前任务正常返回：收尾
        p._enum_signals.finished.emit([], "", pool.started[1]._token)
        assert p._enum_inflight is False


def test_normal_result_disarms_watchdog(panel):
    p, dp, pool = panel
    p._on_hotplug_poll()
    p._enum_signals.finished.emit([], "", pool.started[0]._token)
    assert p._enum_inflight is False
    _pump(300)
    assert "枚举超时" not in p.status_label.text(), "正常返回后不许再报超时"


def test_refresh_watchdog_resets_inflight_and_retries(panel, caplog):
    p, dp, pool = panel
    with caplog.at_level(logging.WARNING, logger=dp.__name__):
        p.refresh_devices_async()
        assert p._refresh_inflight is True
        assert len(pool.started) == 1

        _pump(300)
        assert p._refresh_inflight is False
        assert "枚举超时" in p.status_label.text()
        assert any("枚举超时" in r.getMessage() for r in caplog.records)

        # 刷新卡死曾长期挡住热插拔轮询：复位后轮询必须能照常派发
        p.refresh_devices_async()
        assert len(pool.started) == 2


def test_real_blocking_enumeration_times_out(monkeypatch):
    """原样反证：枚举函数真的永久阻塞（不是假池子不执行任务）。"""
    _app()
    import desktop.device_panel as dp
    from PySide6.QtCore import QThreadPool

    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async", lambda self: None)
    monkeypatch.setattr(dp, "_ENUM_OK", True)
    monkeypatch.setattr(dp, "ENUM_TIMEOUT_MS", 150)

    gate = threading.Event()
    calls = []

    def _blocking_enum():
        calls.append(1)
        gate.wait(10.0)     # 无超时的 USB open 形状：不返回
        return []

    monkeypatch.setattr(dp, "enumerate_all_sdr_devices", _blocking_enum)

    global_pool = QThreadPool.globalInstance()
    old_max = global_pool.maxThreadCount()
    global_pool.setMaxThreadCount(max(2, old_max))   # 两个阻断任务要能各自起线程
    widget = dp.DevicePanel()
    widget._hotplug_timer.stop()
    try:
        widget._on_hotplug_poll()
        _pump(700)                      # 150ms 超时 + 余量
        assert widget._enum_inflight is False
        assert "枚举超时" in widget.status_label.text()

        widget._on_hotplug_poll()
        _pump(200)
        assert len(calls) >= 2, "下一拍必须真的再次调用枚举函数"
    finally:
        gate.set()
        widget.close()
        global_pool.waitForDone(3000)
        global_pool.setMaxThreadCount(old_max)
