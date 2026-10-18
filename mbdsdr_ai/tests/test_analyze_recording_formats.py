# SPDX-License-Identifier: MIT
"""sdr_analyze_recording 格式覆盖测试（doc04 §8 四/五格式读回缺口销账）。

覆盖：cf32/cs16/cu8/wav/csv 五格式读回均出分析报告，不支持格式诚实报错。
"""
import os
import sys
import wave

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.sdr_tools import _analyze_recording


def _tone(n=4096, sr=48000.0, f=1000.0):
    t = np.arange(n) / sr
    return (0.5 * np.exp(2j * np.pi * f * t)).astype(np.complex64)


def _write(fmt, path, iq):
    if fmt == "cf32":
        np.column_stack([iq.real, iq.imag]).astype(np.float32).tofile(path)
    elif fmt == "cs16":
        ci = np.clip(iq.real, -1, 1) * 32767
        cq = np.clip(iq.imag, -1, 1) * 32767
        np.column_stack([ci, cq]).astype(np.int16).tofile(path)
    elif fmt == "cu8":
        ui = np.clip(iq.real, -1, 1) * 127.5 + 127.5
        uq = np.clip(iq.imag, -1, 1) * 127.5 + 127.5
        np.column_stack([ui, uq]).astype(np.uint8).tofile(path)
    elif fmt == "wav":
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            ci = np.clip(iq.real, -1, 1) * 32767
            cq = np.clip(iq.imag, -1, 1) * 32767
            wf.writeframes(np.column_stack([ci, cq]).astype(np.int16).tobytes())
    elif fmt == "csv":
        with open(path, "w", encoding="utf-8") as f:
            f.write("I,Q\n")
            for a, b in zip(iq.real, iq.imag):
                f.write(f"{a:.8f},{b:.8f}\n")


@pytest.mark.parametrize("fmt,ext", [("cf32", ".cf32"), ("cs16", ".cs16"),
                                     ("cu8", ".cu8"), ("wav", ".wav"),
                                     ("csv", ".csv")])
def test_all_formats_analyze(tmp_path, fmt, ext):
    iq = _tone()
    path = str(tmp_path / f"t{ext}")
    _write(fmt, path, iq)
    out = _analyze_recording({"file_path": path, "correct_iq": False, "fft_size": 512})
    assert "错误" not in out.split("\n")[0], out[:200]
    assert f"格式: {ext}" in out
    assert f"样本数: {len(iq)}" in out


def test_unsupported_format_honest_error(tmp_path):
    path = str(tmp_path / "t.xyz")
    with open(path, "wb") as f:
        f.write(b"\x00" * 16)
    out = _analyze_recording({"file_path": path})
    assert "不支持的格式" in out
    assert ".csv" in out  # 报错里列出全部支持格式
