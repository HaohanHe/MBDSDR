# SPDX-License-Identifier: MIT
"""读样本必须有界：设备卡死时返回空数组 + 原因，绝不挂死 CLI/录制。

真机证据（2026-11-18）：RTL-SDR 已连接、已调谐（100.1 MHz / 2.4 MS/s / WFM）
后，pyrtlsdr ``read_bytes(16384)`` 12 秒无返回；``--spectrum/--record`` 永久
挂起，录制 4 秒后 ``.sigmf-data`` 是 0 字节，而 meta 仍写
``mbdsdr:complete=true``（自相矛盾）。

反证：把 ``OpenDevice.read_samples`` 改回直接 ``backend.read_bytes``，本文件
第一个用例会卡到超时上限之外；把 ``mbdsdr:complete`` 改回
``state != "failed"``，0 样本用例立即红。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import iq_sigmf  # noqa: E402
from mbdsdr_ai.core import cli as cli_mod  # noqa: E402
from mbdsdr_ai.core.cli import run  # noqa: E402
from mbdsdr_ai.device_manager import DeviceInfo, OpenDevice  # noqa: E402
from mbdsdr_ai.recorder import IQRecorder  # noqa: E402
from mbdsdr_ai.recording_meta import read_sigmf_meta  # noqa: E402


def _info() -> DeviceInfo:
    return DeviceInfo(name="Fake RTL-SDR", driver="rtlsdr", serial="FAKE")


class _HangingBackend:
    """永远不返回的驱动读（模拟 librtlsdr 的 rtlsdr_read_sync 卡死）。"""

    def __init__(self):
        self.release = threading.Event()
        self.reads = 0

    def read_bytes(self, n):
        self.reads += 1
        self.release.wait()
        return b"\x80" * n

    def close(self):
        pass


class _NormalBackend:
    """正常驱动：8 个 cu8 复样本（128=零）。"""

    def read_bytes(self, n):
        assert n == 16
        return bytes([128, 128] * 8)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 后端有界读
# ---------------------------------------------------------------------------
def test_blocked_read_returns_empty_within_timeout():
    backend = _HangingBackend()
    dev = OpenDevice(_info(), backend, read_timeout_s=0.2)
    try:
        t0 = time.monotonic()
        iq = dev.read_samples(8192)
        elapsed = time.monotonic() - t0
    finally:
        backend.release.set()
        dev.close()
    assert elapsed < 1.5, f"有界读没有生效，耗时 {elapsed:.2f}s"
    assert iq.size == 0, "超时时不能吐假样本"
    reason = dev.last_read_reason
    assert reason["code"] == "read_timeout"
    assert "超时未收到样本" in reason["message"]
    assert reason["device"] == "Fake RTL-SDR"
    assert reason["timeout_s"] == pytest.approx(0.2)
    assert reason["requested_samples"] == 8192


def test_normal_backend_unaffected():
    dev = OpenDevice(_info(), _NormalBackend(), read_timeout_s=1.0)
    try:
        iq = dev.read_samples(8)
    finally:
        dev.close()
    assert iq.size == 8
    assert np.allclose(iq, 0.0)
    assert dev.last_read_reason is None


# ---------------------------------------------------------------------------
# CLI：超时退出码与文案
# ---------------------------------------------------------------------------
class _Status:
    device = "Fake RTL-SDR"


class _TimeoutController:
    """只实现 CLI 用到的面：模拟后端读超时。"""

    last_read_reason = {
        "code": "read_timeout",
        "message": "超时未收到样本（2.0s 内读 512 字节无返回）",
        "device": "Fake RTL-SDR",
        "timeout_s": 2.0,
        "requested_samples": 256,
    }

    def connect(self, device_id):
        return True

    def get_status(self):
        return _Status()

    def read_spectrum(self, nfft):
        return None

    def start_recording(self, path):
        return {"recording": True, "path": path}

    def stop_recording(self):
        return {"recording": False, "samples": 0, "meta_path": None}

    def shutdown(self):
        pass


def test_cli_spectrum_timeout_exit_code_and_message(capsys):
    rc = run(["--connect", "debug", "--spectrum", "256"],
             controller=_TimeoutController())
    assert rc == 3, "频谱无数据/读超时必须用既有 EXIT_NO_DATA"
    err = capsys.readouterr().err
    assert "超时未收到样本" in err
    assert "Fake RTL-SDR" in err


def test_cli_record_no_samples_exit_code_and_message(capsys, monkeypatch,
                                                     tmp_path):
    # 录制 CLI 固定睡 10 秒；测试里把等待变成 0，只验证超时后的退出路径。
    monkeypatch.setattr(cli_mod.time, "sleep", lambda _s: None)
    rc = run(["--connect", "debug", "--record", str(tmp_path / "run.sigmf-data")],
             controller=_TimeoutController())
    assert rc == 3
    err = capsys.readouterr().err
    assert "超时未收到样本" in err
    assert "Fake RTL-SDR" in err
    assert "complete=false" in err


# ---------------------------------------------------------------------------
# 0 样本录制不得标 complete
# ---------------------------------------------------------------------------
def _meta_global(meta_path: str) -> dict:
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)["global"]


def test_zero_sample_recording_meta_is_incomplete(tmp_path):
    rec = IQRecorder(str(tmp_path / "zero"), sample_rate=2.4e6,
                     center_freq_hz=100.1e6, device="RTL-SDR")
    assert rec.open() is True
    meta_path = rec.close()
    assert meta_path and os.path.isfile(meta_path)
    assert os.path.getsize(rec.data_path) == 0, "0 样本必须真的是 0 字节"

    g = _meta_global(meta_path)
    assert g["mbdsdr:complete"] is False
    assert "未收到样本" in g["mbdsdr:incomplete_reason"]
    assert g["core:total_num_samples"] == 0

    # 读回侧（录制库/回放使用的两个入口）必须能看到同一判定。
    want = str(tmp_path / "zero")
    assert read_sigmf_meta(want)["global"]["mbdsdr:complete"] is False
    loaded = iq_sigmf.read_meta(meta_path)
    assert loaded["meta"]["global"]["mbdsdr:complete"] is False
    assert "未收到样本" in loaded["meta"]["global"]["mbdsdr:incomplete_reason"]


def test_nonzero_recording_meta_stays_complete(tmp_path):
    rec = IQRecorder(str(tmp_path / "ok"), sample_rate=1e6,
                     center_freq_hz=100e6, device="RTL-SDR")
    assert rec.open() is True
    assert rec.write(np.ones(16, dtype=np.complex64)) == 16
    meta_path = rec.close()
    g = _meta_global(meta_path)
    assert g["mbdsdr:complete"] is True
    assert "mbdsdr:incomplete_reason" not in g
    assert g["core:total_num_samples"] == 16
