"""
MBDSDR Baseband 录制 / 回放（纯 numpy + 二进制）
================================================
IQ 块存盘为紧凑二进制 + sidecar 元数据，读回。SDR++ 的录制功能。

支持两种输入：
- complex 列表（本地 Python）
- 交错实数列表 [re0, im0, re1, im1, ...]（LLM/JSON 唯一可行格式）
"""
from __future__ import annotations
import numpy as np
import os
import json
from typing import Dict


def _to_complex(iq) -> np.ndarray:
    """把输入统一成 complex128 数组，自动识别交错实数格式。"""
    arr = np.array(iq)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    # 实数列表：偶数长度，当成交错 [re, im, re, im]
    flat = arr.astype(np.float64).ravel()
    if len(flat) % 2 != 0:
        raise ValueError("IQ 实数列表长度必须为偶数（交错 re/im）")
    return flat[0::2] + 1j * flat[1::2]


def save_iq(iq, path: str, sample_rate: float, center_freq_hz: float = 0.0,
            note: str = "") -> Dict:
    """把复数 IQ 存成 .iq 二进制（float32 交错 I/Q），并写 .json sidecar。"""
    arr = _to_complex(iq)
    interleaved = np.empty(len(arr) * 2, dtype=np.float32)
    interleaved[0::2] = arr.real.astype(np.float32)
    interleaved[1::2] = arr.imag.astype(np.float32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    interleaved.tofile(path)
    meta = {
        "sample_rate": sample_rate,
        "center_freq_hz": center_freq_hz,
        "samples": int(len(arr)),
        "duration_s": round(len(arr) / (sample_rate or 1), 3),
        "note": note,
        "format": "float32_interleaved",
    }
    with open(path + ".json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return {**meta, "path": path,
            "sidecar": path + ".json",
            "size_bytes": int(os.path.getsize(path))}


def load_iq(path: str, sample_rate: float = None) -> Dict:
    """读回 .iq 二进制为复数 IQ。优先读 sidecar 拿采样率。"""
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}
    meta = {}
    if os.path.exists(path + ".json"):
        with open(path + ".json") as f:
            meta = json.load(f)
    if sample_rate is None:
        sample_rate = meta.get("sample_rate", 2.4e6)
    raw = np.fromfile(path, dtype=np.float32)
    iq = raw[0::2] + 1j * raw[1::2]
    return {
        "path": path,
        "iq": iq.tolist(),
        "sample_rate": sample_rate,
        "center_freq_hz": meta.get("center_freq_hz", 0),
        "samples": int(len(iq)),
        "duration_s": round(len(iq) / (sample_rate or 1), 3),
    }


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(1000) + 1j * rng.standard_normal(1000))
    # 模拟 LLM 发交错格式
    inter = np.empty(2000)
    inter[0::2] = iq.real
    inter[1::2] = iq.imag
    info = save_iq(inter.tolist(), "/tmp/test.iq", 2.4e6, center_freq_hz=100e6, note="test")
    print("存:", info["samples"], "samples, sidecar:", info["sidecar"])
    back = load_iq("/tmp/test.iq")
    print("读回:", back["samples"], "samples, sr:", back["sample_rate"])
    # 验证虚部没丢
    print("虚部非零:", bool(np.any(np.imag(back["iq"][:5]))))
