# SPDX-License-Identifier: MIT
"""scripts/sdr_diag.py 的行为测试（假后端，不碰硬件）。

钉住的是脚本的诚实契约：五段顺序、失败即停、退出码，以及 --json 的字段。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "sdr_diag.py")

SERIAL = "77771111153705700"


def _load():
    spec = importlib.util.spec_from_file_location("sdr_diag", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclass 在 Py3.14 会按 cls.__module__ 回查 sys.modules；先注册再执行。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _info():
    from mbdsdr_ai.device_manager import DeviceInfo
    from mbdsdr_ai.sdr_backend import rtlsdr_native_entry
    return DeviceInfo(
        name="RTL-SDR #0 (FC0012)",
        driver="rtlsdr",
        serial=SERIAL,
        sample_rates=(2_400_000.0,),
        gains=(0.0, 20.7, 49.6),
        enum_index=0,
        identity=rtlsdr_native_entry({"index": 0, "serial": SERIAL}),
    )


class FakeDevice:
    def __init__(self, samples=None, reason=None):
        self._samples = (np.full(256, 0.5, dtype=np.complex64)
                         if samples is None else samples)
        self.last_read_reason = reason
        self.closed = False
        self.sample_rate = None
        self.freq = None

    def set_sample_rate(self, rate):
        self.sample_rate = float(rate)

    def set_frequency(self, freq):
        self.freq = float(freq)

    def read_samples(self, n):
        return self._samples[:n]

    def close(self):
        self.closed = True


def _free_owner(_info):
    from mbdsdr_ai.device_owner import OWNER_STATE_FREE, OwnerState
    return OwnerState(OWNER_STATE_FREE, "rtlsdr|serial:" + SERIAL)


def _busy_owner(_info):
    from mbdsdr_ai.device_owner import OWNER_STATE_OWNED, OwnerState
    return OwnerState(
        OWNER_STATE_OWNED, "rtlsdr|serial:" + SERIAL,
        record={"owner": "desktop", "pid": 4242, "proc_start": 0.0})


def test_normal_five_stages_exit_0(capsys):
    m = _load()
    dev = FakeDevice()
    rc = m.main(
        ["--device", "rtlsdr:" + SERIAL],
        list_devices=lambda: [_info()],
        open_device=lambda info: dev,
        probe_owner=_free_owner,
    )
    assert rc == m.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "[1/5]" in out and "[5/5]" in out
    assert out.count("PASS") == 5
    assert "FAIL" not in out
    assert dev.closed is True
    assert dev.sample_rate == 2_400_000.0
    assert dev.freq == 100_100_000.0


def test_no_device_exit_1(capsys):
    m = _load()

    def _must_not_open(_info):
        raise AssertionError("无设备时不应进入打开阶段")

    rc = m.main(
        [],
        list_devices=lambda: [],
        open_device=_must_not_open,
        probe_owner=_free_owner,
    )
    assert rc == m.EXIT_NO_DEVICE == 1
    out = capsys.readouterr().out
    assert "[1/5]" in out and "[5/5]" not in out
    assert "未发现可用 SDR 设备" in out


def test_busy_device_exit_2(capsys):
    m = _load()

    def _must_not_open(_info):
        raise AssertionError("被占用时不应尝试打开设备")

    rc = m.main(
        [],
        list_devices=lambda: [_info()],
        open_device=_must_not_open,
        probe_owner=_busy_owner,
    )
    assert rc == m.EXIT_BUSY == 2
    out = capsys.readouterr().out
    assert "[2/5]" in out
    assert "占用" in out
    assert "[3/5]" not in out


def test_no_samples_exit_4(capsys):
    m = _load()
    reason = {
        "code": "read_timeout",
        "message": "超时未收到样本（2.0s 内读 512 字节无返回）",
        "device": "RTL-SDR #0 (FC0012)",
        "timeout_s": 2.0,
        "requested_samples": 256,
    }
    dev = FakeDevice(samples=np.empty(0, dtype=np.complex64), reason=reason)
    rc = m.main(
        [],
        list_devices=lambda: [_info()],
        open_device=lambda info: dev,
        probe_owner=_free_owner,
    )
    assert rc == m.EXIT_NO_SAMPLES == 4
    out = capsys.readouterr().out
    assert "[5/5]" in out and "FAIL" in out
    assert "512 字节" in out
    assert "rtl_test" in out
    assert dev.closed is True  # 失败路径也必须归还设备/占用登记


def test_json_fields_and_stage_order(capsys):
    m = _load()
    dev = FakeDevice()
    rc = m.main(
        ["--json"],
        list_devices=lambda: [_info()],
        open_device=lambda info: dev,
        probe_owner=_free_owner,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["device"].startswith("RTL-SDR #0")
    stages = payload["stages"]
    assert [s["name"] for s in stages] == [
        "enumerate", "claim", "open", "tune", "read"]
    for st in stages:
        assert set(st) == {"name", "ok", "detail", "elapsed_ms"}
        assert st["ok"] is True
        assert isinstance(st["detail"], str) and st["detail"]
        assert isinstance(st["elapsed_ms"], (int, float))
        assert st["elapsed_ms"] >= 0
