# SPDX-License-Identifier: MIT
"""三个解码工具的失败信封（工具面 BROKEN=0 的钉扎）。

2026-11-18 `scripts/tool_availability_audit.py` 报 3 个 BROKEN：
  - `meteor_lrpt_demod`：采样率不合契约时 ValueError 直接外抛；
  - `sdr_decode_cw` / `sdr_decode_ft8`：`wave.Error` 未被 catch 外抛，
    且旧 handler 把任何返回串都包成 `success=True`——解不出来也报成功。

本文件只钉两件事：
1. 坏输入**不抛异常**，回 `success=False` 的结构化信封（带 error_code）；
2. 正常输入仍 `success=True`（不把失败信封变成新的假失败）。
"""
from __future__ import annotations

import base64
import math
import os
import struct
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sdr_tools import (  # noqa: E402
    _decode_cw, _decode_ft8, _meteor_lrpt_demod_fn_tool,
)


def _write_wav(path, seconds=1.0, rate=8000, freq=600.0, amp=0.5):
    n = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def _iq_b64(n=4096, rate=72000.0, freq=1000.0):
    t = np.arange(n, dtype=np.float64)
    iq = (0.3 * np.exp(2j * np.pi * freq * t / rate)).astype(np.complex64)
    inter = np.empty(n * 2, dtype=np.float32)
    inter[0::2] = iq.real
    inter[1::2] = iq.imag
    return base64.b64encode(inter.tobytes()).decode("ascii")


# --------------------------------------------------------------------------
# 1. 坏输入：不抛、回失败信封
# --------------------------------------------------------------------------
def test_cw_rejects_non_wav_without_raising(tmp_path):
    bad = tmp_path / "not_a_wav.wav"
    bad.write_bytes(b"this is not RIFF at all")

    res = _decode_cw({"input_path": str(bad)})

    assert res.success is False
    assert res.error_code == "invalid_wav"
    assert "CW 解码失败" in res.content


def test_ft8_rejects_non_wav_without_raising(tmp_path):
    bad = tmp_path / "not_a_wav.wav"
    bad.write_bytes(b"this is not RIFF at all")

    res = _decode_ft8({"input_path": str(bad)})

    assert res.success is False
    assert res.error_code == "invalid_wav"


def test_meteor_lrpt_rejects_bad_sample_rate_without_raising():
    """1.024 MS/s 不是 72k 符号率整数倍：应回 sample_rate_mismatch，不是异常。"""
    res = _meteor_lrpt_demod_fn_tool({
        "iq_base64": _iq_b64(),
        "sample_rate": 1_024_000,
    })

    assert res.success is False
    assert res.error_code == "sample_rate_mismatch"
    assert res.data["sym_rate"] == 72000
    assert "fs" in res.content or "采样" in res.content


def test_meteor_lrpt_missing_iq_still_envelope():
    res = _meteor_lrpt_demod_fn_tool({"sample_rate": 72000})
    assert res.success is False
    assert res.data["error"] == "missing_iq"


# --------------------------------------------------------------------------
# 2. 正常输入：仍然成功（防止把信封改成假失败）
# --------------------------------------------------------------------------
def test_cw_valid_wav_succeeds(tmp_path):
    good = tmp_path / "cw.wav"
    _write_wav(good, seconds=0.6, freq=700.0)

    res = _decode_cw({"input_path": str(good)})

    assert res.success is True
    assert res.error_code == ""
    assert "CW 解码完成" in res.content


def test_ft8_valid_wav_succeeds_and_reports_detection(tmp_path):
    good = tmp_path / "tone.wav"
    _write_wav(good, seconds=1.0, freq=1000.0)

    res = _decode_ft8({"input_path": str(good)})

    assert res.success is True
    assert res.data["input_path"] == str(good)
    assert isinstance(res.data["detected"], bool)


def test_meteor_lrpt_valid_rate_succeeds():
    res = _meteor_lrpt_demod_fn_tool({
        "iq_base64": _iq_b64(rate=288000.0),
        "sample_rate": 288000,
    })

    assert res.success is True
    assert "symbols" in res.data or "n_symbols" in res.data
