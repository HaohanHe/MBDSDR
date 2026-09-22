"""
MBDSDR 模拟音频解调（纯 numpy）
================================
IQ -> AM(包络) / FM(相位差分鉴频) / SSB(混频低通) 音频。
这是 SDR++/GNU Radio 的核心：听广播/听通话。
"""
from __future__ import annotations
import numpy as np
from typing import Dict


def _lowpass(x: np.ndarray, sr: float, cutoff: float, taps: int = 63) -> np.ndarray:
    n = np.arange(taps) - taps // 2
    h = 2 * cutoff / sr * np.sinc(2 * cutoff / sr * n)
    h *= np.hanning(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


def demod_analog(iq: np.ndarray, sample_rate: float, mode: str = "fm",
                 max_dev: float = 5000.0, audio_bw: float = 3000.0) -> Dict:
    """IQ -> 模拟音频。

    mode: am(包络检波) / fm(相位差分鉴频) / usb / lsb(边带)。
    返回 {audio, sample_rate, mode, rms_db}。
    """
    x = np.asarray(iq, dtype=np.complex128)
    sr = float(sample_rate)
    mode = mode.lower()

    if mode == "am":
        audio = np.abs(x)
        audio = audio - np.mean(audio)
        audio = _lowpass(audio, sr, audio_bw)
    elif mode == "fm":
        # 相位差分鉴频：arg(conj(x[n-1])*x[n]) / 2pi
        phase = np.angle(x[1:] * np.conj(x[:-1]))
        audio = phase / (2 * np.pi * max_dev / sr)
        audio = np.concatenate([audio, audio[-1:]])
        audio = _lowpass(audio, sr, audio_bw)
    elif mode in ("usb", "lsb"):
        # 边带：直接取实部（已在 VFO 中心），低通到音频带宽
        audio = np.real(x)
        audio = _lowpass(audio, sr, audio_bw)
    else:
        raise ValueError(f"未知模式: {mode}（am/fm/usb/lsb）")

    audio = audio / (np.max(np.abs(audio)) + 1e-9)
    rms = float(np.sqrt(np.mean(audio ** 2)))
    return {
        "audio": audio.astype(np.float32).tolist(),
        "sample_rate": sr,
        "mode": mode,
        "rms_db": round(20 * np.log10(rms + 1e-9), 1),
    }


if __name__ == "__main__":
    # 自测：合成 FM 信号（音调 400Hz 调制 5kHz 频偏）
    sr = 480000
    t = np.arange(sr) / sr
    # 调制：400Hz 音调
    mod = 0.4 * np.sin(2 * np.pi * 400 * t)
    phase = 2 * np.pi * 5000 * np.cumsum(mod) / sr
    iq = np.exp(1j * phase)
    r = demod_analog(iq, sr, mode="fm", max_dev=5000)
    a = np.array(r["audio"])
    # 算解调后音频主频（应≈400Hz）
    sp = np.abs(np.fft.rfft(a))
    fr = np.fft.rfftfreq(len(a), 1 / sr)
    peak = fr[np.argmax(sp[10:]) + 10]
    print(f"FM 解调: 模式={r['mode']} 解出音调≈{peak:.0f}Hz (目标400)")
