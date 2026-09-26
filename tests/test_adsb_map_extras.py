#!/usr/bin/env python3
"""
ADSB 地图/基带附加能力测试
===========================

覆盖两个新增模块：
  1. mbdsdr_ai.recording_meta —— SigMF 标准 .sigmf-meta 元数据读写往返。
  2. mbdsdr_ai.device_watchdog —— 独立线程看门狗在后端抖动/异常时不崩溃、
     能自动重连并对外上报状态信号。
"""

import os
import sys
import time
from types import SimpleNamespace

import pytest

# offscreen 平台必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai.recording_meta import (  # noqa: E402
    sigmf_meta_path,
    build_sigmf_meta,
    write_sigmf_meta,
    read_sigmf_meta,
    metadata_for_display,
    SigMFRecordingSession,
)
from mbdsdr_ai.device_watchdog import DeviceWatchdog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# SigMF 元数据读写往返
# ---------------------------------------------------------------------------
def test_sigmf_roundtrip(tmp_path):
    iq_path = str(tmp_path / "rec_adsb.cf32")

    # meta 路径推导
    assert sigmf_meta_path(iq_path) == str(tmp_path / "rec_adsb.sigmf-meta")

    # 写（不写 IQ 数据本身，只测 sidecar meta）
    meta_path = write_sigmf_meta(
        iq_path,
        sample_rate=2.4e6,
        center_freq_hz=1090e6,
        gain_db=38.0,
        start_time_iso="2026-09-26T00:00:00+00:00",
        hardware="RTL-SDR",
        datatype="cu8",
    )
    assert os.path.exists(meta_path)

    # 读回
    meta = read_sigmf_meta(iq_path)
    assert meta is not None
    assert meta["global"]["core:sample_rate"] == 2.4e6
    assert meta["global"]["core:frequency"] == 1090e6
    assert meta["global"]["core:datatype"] == "cu8"
    assert meta["global"]["core:version"] == "1.0.0"
    assert meta["captures"][0]["mbdsdr:gain_db"] == 38.0

    # UI 友好拍平字段
    disp = metadata_for_display(meta)
    assert disp["sample_rate_hz"] == 2.4e6
    assert disp["center_freq_hz"] == 1090e6
    assert disp["gain_db"] == 38.0
    assert disp["hardware"] == "RTL-SDR"
    assert disp["start_time"] == "2026-09-26T00:00:00+00:00"


def test_sigmf_missing_returns_none(tmp_path):
    assert read_sigmf_meta(str(tmp_path / "nope.cf32")) is None
    assert metadata_for_display(None)["duration_s"] is None


def test_sigmf_recording_session_finalize(tmp_path):
    iq_path = str(tmp_path / "sess.cf32")
    with SigMFRecordingSession(
        iq_path,
        sample_rate=2.0e6,
        center_freq_hz=1090e6,
        gain_db=30.0,
        start_time_iso="2026-09-26T00:00:00+00:00",
        hardware="RTL-SDR",
    ) as sess:
        # start() 已由 __enter__ 调用；录制中途刷新样本数
        sess.update(1024)
        mid = read_sigmf_meta(iq_path)
        assert mid["global"]["core:num_samples"] == 1024
        # 退出 with 前记录最终样本数，finalize 时补全
        sess._meta_kwargs["num_samples"] = 2048

    final = read_sigmf_meta(iq_path)
    assert final["global"]["core:num_samples"] == 2048

    # 时长可由样本数/采样率算出
    disp = metadata_for_display(final)
    assert disp["num_samples"] == 2048
    assert disp["duration_s"] == pytest.approx(2048 / 2.0e6)


# ---------------------------------------------------------------------------
# 看门狗：后端抖动/异常不崩、自动重连
# ---------------------------------------------------------------------------
class FlakyBackend:
    """测试用后端：首次 get_status 抛 USB 异常，connect 后恢复 connected。"""

    def __init__(self):
        self._get_calls = 0
        self._connected = False

    def get_status(self):
        self._get_calls += 1
        if self._get_calls == 1:
            raise RuntimeError("USB断开")
        return SimpleNamespace(connected=self._connected)

    def connect(self):
        self._connected = True
        return True


def _pump(qapp, until, timeout=5.0, step=0.02):
    """泵事件循环直到 until() 为真或超时。"""
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if until():
            return True
        time.sleep(step)
    qapp.processEvents()
    return until()


def test_watchdog_flaky_backend_reconnects(qapp):
    backend = FlakyBackend()
    wd = DeviceWatchdog(backend, ping_interval_ms=50, reconnect_interval_ms=100)

    states = []
    wd.status_changed.connect(lambda s: states.append(s))
    disconnected_seen = []
    connected_seen = []
    errors = []
    wd.disconnected.connect(lambda: disconnected_seen.append(True))
    wd.connected.connect(lambda: connected_seen.append(True))
    wd.error.connect(lambda e: errors.append(e))

    wd.start()
    try:
        # 等到至少看到一次 disconnected（首次 ping 抛异常 -> 上报断开）
        ok_disc = _pump(qapp, lambda: len(disconnected_seen) > 0, timeout=3.0)
        assert ok_disc, "应收到 disconnected 信号"
        # 线程仍在运行、未崩
        assert wd.isRunning(), "看门狗线程应仍在运行"

        # 随后重连成功 -> 收到 connected
        ok_conn = _pump(qapp, lambda: len(connected_seen) > 0, timeout=3.0)
        assert ok_conn, "自动重连后应收到 connected 信号"
        assert wd.is_connected(), "看门狗应判定为已连接"
        assert wd.current_state() == "connected"
    finally:
        wd.stop()

    assert wd.wait(2000), "看门狗应优雅退出"
    # 状态序列里应同时出现过 disconnected 与 connected
    assert "disconnected" in states
    assert "connected" in states


def test_watchdog_backend_none(qapp):
    wd = DeviceWatchdog(None, ping_interval_ms=20, reconnect_interval_ms=50)
    states = []
    wd.status_changed.connect(lambda s: states.append(s))
    wd.start()
    try:
        seen = _pump(qapp, lambda: wd.current_state() == "disconnected", timeout=2.0)
        assert seen, "backend=None 应稳定在 disconnected"
        assert wd.isRunning(), "看门狗不应因 backend=None 崩溃退出"
    finally:
        wd.stop()
    assert wd.wait(2000), "backend=None 看门狗应正常 stop"
    assert wd.current_state() == "disconnected"
