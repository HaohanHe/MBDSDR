"""
MBDSDR Baseband 录制 / 回放（纯 numpy + 二进制）
================================================
IQ 块存盘为紧凑二进制 + sidecar 元数据，读回。SDR++ 的录制功能。

支持格式（按文件扩展名自动识别）：
- .cf32 / .iq：裸 float32 交织 I/Q（无损，round-trip 精确）
- .wav：立体声 int16 WAV（左=I，右=Q；幅度归一化到 int16 满幅，
        sidecar 里存 wav_scale 以便 load_iq 反归一化）

支持两种输入：
- complex 数组（本地 Python）
- 交错实数列表 [re0, im0, re1, im1, ...]（LLM/JSON 唯一可行格式）

sidecar（<path>.json）字段：
    center_freq_hz, sample_rate, samples, duration_s, format,
    start_time（ISO 8601）, gain_db, device, driver, note
"""
from __future__ import annotations
import numpy as np
import os
import json
import wave as _wave
from datetime import datetime, timezone
from typing import Dict, Optional


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


def _format_from_path(path: str) -> str:
    """按扩展名判定录制格式：.wav -> wav；其余（.cf32/.iq/无扩展名）-> cf32。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return "wav"
    return "cf32"


def write_sidecar(path: str, metadata: Dict) -> str:
    """把录制元数据写到 <path>.json（与 IQ 文件同目录同名，仅扩展名不同）。

    Parameters
    ----------
    path : str
        IQ 数据文件路径。
    metadata : dict
        要落盘的元数据字段（中心频率、采样率、开始时间、时长、样本数、
        格式、增益、设备名、驱动等）。

    Returns
    -------
    str
        sidecar JSON 文件路径。
    """
    spath = path + ".json"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return spath


def _write_cf32(path: str, arr: np.ndarray) -> int:
    """写裸 float32 交织 I/Q。返回文件字节数。"""
    interleaved = np.empty(len(arr) * 2, dtype=np.float32)
    interleaved[0::2] = arr.real.astype(np.float32)
    interleaved[1::2] = arr.imag.astype(np.float32)
    interleaved.tofile(path)
    return int(os.path.getsize(path))


def _write_wav(path: str, arr: np.ndarray, sample_rate: float) -> tuple:
    """写立体声 int16 WAV（左=I，右=Q），归一化到 int16 满幅。

    Returns (size_bytes, wav_scale)。wav_scale 用于 load_iq 反归一化。
    """
    peak = float(max(np.max(np.abs(arr.real)), np.max(np.abs(arr.imag)), 1e-12))
    scale = peak / 32767.0
    pcm_i = np.clip(arr.real / scale, -32767.0, 32767.0).astype(np.int16)
    pcm_q = np.clip(arr.imag / scale, -32767.0, 32767.0).astype(np.int16)
    stereo = np.empty(len(arr) * 2, dtype=np.int16)
    stereo[0::2] = pcm_i
    stereo[1::2] = pcm_q
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(stereo.tobytes())
    return int(os.path.getsize(path)), scale


def save_iq(iq, path: str, sample_rate: float, center_freq_hz: float = 0.0,
            note: str = "",
            start_time: Optional[str] = None,
            gain_db: float = 0.0,
            device: str = "",
            driver: str = "") -> Dict:
    """把复数 IQ 存盘，并写 .json sidecar 元数据。

    Parameters
    ----------
    iq : complex 数组或交错实数列表
        真实 SDR IQ 样本。
    path : str
        输出文件路径。扩展名决定格式：.wav -> 立体声 int16 WAV；
        .cf32/.iq/其他 -> 裸 float32 交织 I/Q。
    sample_rate : float
        采样率 Hz。
    center_freq_hz : float
        中心频率 Hz。
    note : str
        备注。
    start_time : str, optional
        录制开始时间（ISO 8601）。缺省用当前 UTC 时间。
    gain_db : float
        录制时的射频增益 dB。
    device : str
        设备名称。
    driver : str
        驱动名称（如 SoapySDR/RTL-SDR/HackRF）。

    Returns
    -------
    dict
        落盘信息（含 sidecar 路径与字节数）。
    """
    arr = _to_complex(iq)
    fmt = _format_from_path(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    wav_scale: Optional[float] = None
    if fmt == "wav":
        size_bytes, wav_scale = _write_wav(path, arr, sample_rate)
    else:
        size_bytes = _write_cf32(path, arr)
        fmt = "cf32"

    if not start_time:
        start_time = datetime.now(timezone.utc).isoformat()

    meta: Dict = {
        "center_freq_hz": float(center_freq_hz),
        "sample_rate": float(sample_rate),
        "start_time": start_time,
        "duration_s": round(len(arr) / (sample_rate or 1), 3),
        "samples": int(len(arr)),
        "format": fmt,
        "gain_db": float(gain_db),
        "device": str(device),
        "driver": str(driver),
        "note": note,
    }
    if wav_scale is not None:
        meta["wav_scale"] = wav_scale
    sidecar = write_sidecar(path, meta)
    return {**meta, "path": path, "sidecar": sidecar, "size_bytes": size_bytes}


def load_iq(path: str, sample_rate: float = None) -> Dict:
    """读回 IQ 文件为复数 IQ。优先读 sidecar 拿采样率/格式。"""
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}
    meta: Dict = {}
    if os.path.exists(path + ".json"):
        with open(path + ".json", encoding="utf-8") as f:
            meta = json.load(f)
    if sample_rate is None:
        sample_rate = meta.get("sample_rate", 2.4e6)

    fmt = meta.get("format") or _format_from_path(path)
    if fmt == "wav":
        with _wave.open(path, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
        stereo = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        iq = stereo[0::2] + 1j * stereo[1::2]
        scale = float(meta.get("wav_scale", 1.0))
        iq = iq * scale
    else:
        raw = np.fromfile(path, dtype=np.float32).astype(np.float64)
        iq = raw[0::2] + 1j * raw[1::2]

    return {
        "path": path,
        "iq": iq.tolist(),
        "sample_rate": float(sample_rate),
        "center_freq_hz": meta.get("center_freq_hz", 0),
        "start_time": meta.get("start_time", ""),
        "gain_db": meta.get("gain_db", 0.0),
        "device": meta.get("device", ""),
        "driver": meta.get("driver", ""),
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
    info = save_iq(inter.tolist(), "/tmp/test.iq", 2.4e6,
                   center_freq_hz=100e6, note="test",
                   gain_db=20.5, device="RTL-SDR #0", driver="rtlsdr")
    print("存:", info["samples"], "samples, sidecar:", info["sidecar"])
    back = load_iq("/tmp/test.iq")
    print("读回:", back["samples"], "samples, sr:", back["sample_rate"],
          "gain:", back["gain_db"], "dev:", back["device"])
    # 验证虚部没丢
    print("虚部非零:", bool(np.any(np.imag(back["iq"][:5]))))
