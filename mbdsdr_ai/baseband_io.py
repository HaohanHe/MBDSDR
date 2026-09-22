"""
MBDSDR Baseband 录制 / 回放（纯 numpy + 二进制）
================================================
IQ 块存盘为紧凑二进制，读回。SDR++ 的录制功能。
"""
from __future__ import annotations
import numpy as np
import os
import time
from typing import Dict


def save_iq(iq: list, path: str, sample_rate: float, center_freq_hz: float = 0.0,
            note: str = "") -> Dict:
    """把复数 IQ 存成 .iq 二进制（float32 交错 I/Q）。"""
    arr = np.array(iq, dtype=np.complex128)
    interleaved = np.empty(len(arr) * 2, dtype=np.float32)
    interleaved[0::2] = arr.real.astype(np.float32)
    interleaved[1::2] = arr.imag.astype(np.float32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    interleaved.tofile(path)
    return {
        "path": path,
        "sample_rate": sample_rate,
        "center_freq_hz": center_freq_hz,
        "samples": int(len(arr)),
        "duration_s": round(len(arr) / (sample_rate or 1), 3),
        "size_bytes": int(os.path.getsize(path)),
        "note": note,
    }


def load_iq(path: str, sample_rate: float) -> Dict:
    """读回 .iq 二进制为复数 IQ 列表。"""
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}
    raw = np.fromfile(path, dtype=np.float32)
    iq = raw[0::2] + 1j * raw[1::2]
    return {
        "path": path,
        "iq": iq.tolist(),
        "sample_rate": sample_rate,
        "samples": int(len(iq)),
        "duration_s": round(len(iq) / (sample_rate or 1), 3),
    }


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(1000) + 1j * rng.standard_normal(1000)).tolist()
    info = save_iq(iq, "/tmp/test.iq", 2.4e6, center_freq_hz=100e6, note="test")
    print("存:", info)
    back = load_iq("/tmp/test.iq", 2.4e6)
    print("读回样本数:", back["samples"], "时长:", back["duration_s"], "s")
