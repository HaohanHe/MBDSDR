# SPDX-License-Identifier: MIT
"""sdr_tools 静默失败清理的反证测试（2026-11-05）。

覆盖三类原本会被吞掉、用户/模型无从察觉的失败：
1. 临时调谐恢复失败仍宣称"已回到原频率"；
2. sidecar 损坏后按默认参数分析，却不说参数是默认值；
3. 书签跳转的部分设置失败被静默跳过。
"""
from __future__ import annotations

import json
import types

import numpy as np
import pytest

from mbdsdr_ai import hackrf_params, sdr_tools as st


class _OkBackend:
    """最小后端桩：记录调用，可按需让某个方法抛错或显式返回 False。"""

    def __init__(self, fail: str | None = None, refuse: set | None = None):
        self.fail = fail
        self.refuse = set(refuse or ())
        self.status = types.SimpleNamespace(connected=True, demod_mode="WFM", error="")
        self.device = types.SimpleNamespace(device_type="hackrf")
        self._freq = 100e6
        self.calls = []
        self.reads = 0

    def _maybe_fail(self, name):
        if self.fail == name:
            raise RuntimeError(f"{name} 失败（模拟驱动错误）")

    def _maybe_refuse(self, name):
        if name in self.refuse:
            self.status.error = f"{name} 未落到硬件（模拟拒绝）"
            return False
        return None

    def get_frequency(self):
        self._maybe_fail("get_frequency")
        return self._freq

    def get_sample_rate(self):
        self._maybe_fail("get_sample_rate")
        return 2e6

    def set_frequency(self, f):
        self._maybe_fail("set_frequency")
        if self._maybe_refuse("set_frequency") is False:
            return False
        self._freq = f
        self.calls.append(("set_frequency", f))
        return True

    def set_sample_rate(self, sr):
        self._maybe_fail("set_sample_rate")
        if self._maybe_refuse("set_sample_rate") is False:
            return False
        self.calls.append(("set_sample_rate", sr))
        return True

    def set_demod(self, mode):
        self._maybe_fail("set_demod")
        if self._maybe_refuse("set_demod") is False:
            return False
        self.calls.append(("set_demod", mode))
        return True

    def set_bandwidth(self, hz):
        self._maybe_fail("set_bandwidth")
        if self._maybe_refuse("set_bandwidth") is False:
            return False
        self.calls.append(("set_bandwidth", hz))
        return True

    def read_samples(self, n):
        self._maybe_fail("read_samples")
        self.reads += 1
        return np.zeros(n, dtype=np.complex64)


class _Mgr:
    def __init__(self, backend):
        self._backend = backend

    def get_active(self):
        return self._backend


def test_restore_tuning_failure_is_reported():
    """修复前：`except Exception: pass`，输出仍写"已自动回到扫描前频率"。"""
    backend = _OkBackend(fail="set_frequency")
    warning = st._restore_tuning(backend, (100e6, 2e6, "WFM"), "WFM")
    assert "恢复原调谐状态失败" in warning
    assert "set_frequency 失败" in warning


def test_restore_tuning_success_is_silent():
    backend = _OkBackend()
    assert st._restore_tuning(backend, (100e6, 2e6, "WFM"), "WFM") == ""


def test_restore_tuning_explicit_refusal_is_reported():
    """设备显式返回 False（不抛异常）也是恢复失败，不得当成已恢复。"""
    backend = _OkBackend(refuse={"set_frequency"})
    warning = st._restore_tuning(backend, (100e6, 2e6, "WFM"), "WFM")
    assert "恢复原调谐状态失败" in warning
    assert "未落到硬件" in warning


def test_recordings_list_reports_corrupt_sidecar(tmp_path, monkeypatch):
    """修复前：坏 sidecar 被 pass，列表里 frequency_mhz=None 看起来像没记录。"""
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    (rec_dir / "a.cf32").write_bytes(b"\x00" * 32)
    (rec_dir / "a.cf32.json").write_text("{损坏的 json", encoding="utf-8")

    monkeypatch.setattr(st.os.path, "expanduser", lambda p: str(rec_dir))
    files = json.loads(st._recordings_list({}))
    assert files[0]["meta_error"].startswith("sidecar 无法解析")


def test_analyze_recording_reports_corrupt_sidecar(tmp_path):
    """修复前：按默认 2.4MHz/中心 0 分析，报告里没有任何提示。"""
    path = tmp_path / "capture.cf32"
    np.arange(4096, dtype=np.float32).tofile(path)
    (tmp_path / "capture.cf32.json").write_text("not json", encoding="utf-8")

    report = st._analyze_recording({"file_path": str(path), "correct_iq": False})
    assert "警告: sidecar 无法解析" in report
    assert "以下采样率/中心频率为默认值" in report


def test_bookmark_goto_reports_partial_failure():
    """修复前：模式切换失败被 pass，输出只说"已跳转到"并列出模式。"""
    backend = _OkBackend(fail="set_demod")
    fm = types.SimpleNamespace(find=lambda target: types.SimpleNamespace(
        name="测试台", center_hz=100e6, mode="WFM", bandwidth_hz=200e3, note="",
        is_range=False))
    res = st._bookmark_goto(_Mgr(backend), fm, {"target": "测试台"})

    assert res.success is True
    assert "未生效" in res.content
    assert "模式 WFM 未生效" in res.content


def test_fm_scan_cancels_when_current_tuning_unreadable():
    """修复前：读不到原调谐仍继续扫，扫完设备被留在扫描频段且无人知道。"""
    backend = _OkBackend(fail="get_frequency")
    backend.device.device_type = "hackrf"
    res = st._fm_scan_tool(_Mgr(backend), {"f_start_mhz": 87.0, "f_stop_mhz": 108.0})

    assert res.success is False
    assert "已取消扫描" in res.content


def test_fm_acquire_sample_rate_failure_is_explicit():
    backend = _OkBackend(fail="set_sample_rate")
    with pytest.raises(RuntimeError, match="采样率"):
        st._fm_make_acquire(_Mgr(backend), 1e6)


def test_fm_acquire_counts_read_failures():
    """单点采集失败不炸整次扫频，但必须留下计数与原因。"""
    backend = _OkBackend(fail="read_samples")
    acquire = st._fm_make_acquire(_Mgr(backend), 1e6)
    assert acquire(100e6, 1e6, 1024) is None
    assert acquire.state["failures"] == 1
    assert "read_samples 失败" in acquire.state["last_error"]


def test_bookmark_goto_aborts_on_explicit_refusal():
    """后端显式返回 False（不是抛异常）时也只能失败，不得报"已跳转"。"""
    backend = _OkBackend(refuse={"set_frequency"})
    fm = types.SimpleNamespace(find=lambda target: types.SimpleNamespace(
        name="测试台", center_hz=145e6, mode="FM", bandwidth_hz=200e3, note="",
        is_range=False))
    res = st._bookmark_goto(_Mgr(backend), fm, {"target": "测试台"})

    assert res.success is False
    assert "未落到硬件" in res.content
    assert "已跳转到" not in res.content


def test_adsb_aborts_before_sampling_when_tuning_refused():
    """FC0012 类设备请求 1090：必须中止，不得在旧频采样后报"0 帧"。"""
    backend = _OkBackend(refuse={"set_frequency"})
    backend._freq = 98e6
    res = st._adsb_decode_tool(_Mgr(backend), {"duration_s": 0.1})

    assert res.success is False
    assert "ADS-B 采集未开始" in res.content
    assert "未在旧频率" in res.content
    assert backend.reads == 0, "失败后不得继续读 IQ"


def test_adsb_aborts_when_sample_rate_or_demod_refused():
    for name in ("set_sample_rate", "set_demod"):
        backend = _OkBackend(refuse={name})
        res = st._adsb_decode_tool(_Mgr(backend), {"duration_s": 0.1})
        assert res.success is False
        assert backend.reads == 0


def test_watch_aborts_when_tuning_refused():
    backend = _OkBackend(refuse={"set_frequency"})
    fm = types.SimpleNamespace(find=lambda target: None)
    res = st._watch_capture_tool(_Mgr(backend), fm, {"freq_mhz": 100.0})

    assert res.success is False
    assert "守听频率失败" in res.content
    assert backend.reads == 0


def test_fm_listener_does_not_start_when_tuning_refused(monkeypatch):
    """失败时既不能起音频线程，也不能让播放器开始"放旧频"。"""

    class _Player:
        def __init__(self):
            self.started = False
            self.written = 0

        def start(self):
            self.started = True

        def stop(self):
            pass

        def write(self, data):
            self.written += len(data)
            return len(data)

    players = []

    def _make_player(**kw):
        p = _Player()
        players.append(p)
        return p

    monkeypatch.setattr(st, "AudioPlayer", _make_player)
    backend = _OkBackend(refuse={"set_frequency"})
    listener = st._FMListener(backend, sample_rate_hz=1e6)

    assert listener.start(100.0) is False
    assert "未落到硬件" in (listener.error or "")
    assert listener._thread is None
    assert players and players[0].started is False
    assert backend.reads == 0


def test_hackrf_write_tx_invalid_input_records_reason():
    """修复前：`except Exception: return False`，调用方只知道"失败"不知道原因。"""
    h = hackrf_params.HackRFBackend()
    h.connected = True          # 跳过"未连接"分支
    h._lib = object()           # 只要非 None；本用例在触达 C API 前就失败
    assert h.write_tx("不是 IQ 样本") is False
    assert h.last_tx_error
    assert h.tx_error_count == 1
