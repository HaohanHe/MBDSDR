# SPDX-License-Identifier: MIT
"""气象卫星解码器（依据 NOAA APT 公开格式独立实现）。

公开格式要点（NOAA POES APT 广播格式）：
- APT 副载波 2400 Hz，子载波频移键控；解调后取包络即图像幅度。
- 行结构：39 样本同步方波 + 间隔 + 图像区；APT_IMG_WIDTH=2080 样本/行（双路各 1040）。
- 行同步用 39 样本同步方波的滑动互相关定位行首。
- 通道 A/B 各取 909 像素图像区。

本模块提供薄封装：
  * NOAAAPTDecoder — WAV/原始音频入 → A/B 通道灰度图 + PNG 出
  * load_wav_mono  — 读 16-bit PCM WAV
  * auto_detect_decoder — 按频率选解码器

SatDump、noaa-apt 等开源项目仅作技术参考与致谢，本仓未包含其源代码。
"""
from __future__ import annotations

import os
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# 复用 noaa_apt_lite 的常量与全链解码（依据 NOAA APT 公开格式独立实现）
from .. import noaa_apt_lite as _apt


# --------------------------------------------------------------------------- #
# WAV 读取（16-bit PCM mono/stereo）
# --------------------------------------------------------------------------- #
def load_wav_mono(path: str) -> Tuple[np.ndarray, float]:
    """读 16-bit PCM WAV → (float64 采样 [-1,1], 采样率)。立体声取左声道。"""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        fr = w.getframerate()
        nframes = w.getnframes()
        if sw != 2:
            raise ValueError(f"仅支持 16-bit PCM WAV（当前 sampwidth={sw}）")
        raw = w.readframes(nframes)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch == 2:
        samples = samples[0::2]
    elif nch != 1:
        raise ValueError(f"不支持 {nch} 声道")
    return samples, float(fr)


# --------------------------------------------------------------------------- #
# 解码结果
# --------------------------------------------------------------------------- #
@dataclass
class APTDecodeResult:
    present: bool
    channel_a: Optional[np.ndarray] = None   # uint8 (H,909)
    channel_b: Optional[np.ndarray] = None
    lines: int = 0
    lock_ratio: float = 0.0
    duration_s: float = 0.0
    reason: str = ""
    satellite: str = ""

    def combo(self) -> Optional[np.ndarray]:
        if self.channel_a is None or self.channel_b is None:
            return None
        h = min(len(self.channel_a), len(self.channel_b))
        return np.concatenate([self.channel_a[:h], self.channel_b[:h]], axis=1)


class NOAAAPTDecoder:
    """NOAA APT 解码器。

    用法:
        dec = NOAAAPTDecoder()
        res = dec.decode_wav("noaa19.wav", satellite="NOAA-19")
        res.save_png("out/noaa19")
    """

    #: APT 下行频率表（Hz），与 tracker.DOWNLINK_FREQUENCIES 一致
    KNOWN_FREQUENCIES: Dict[str, float] = {
        "NOAA-15": 137.6200e6,
        "NOAA-18": 137.9125e6,
        "NOAA-19": 137.1000e6,
    }

    def __init__(self, polarity: int = 1, min_lines: int = 4):
        self.polarity = polarity
        self.min_lines = min_lines

    def decode_audio(self, audio: np.ndarray, sample_rate: float,
                     satellite: str = "") -> APTDecodeResult:
        """把 FM 鉴频后的 APT 音频（2400Hz 副载波）解码成 A/B 通道图。"""
        out = _apt.decode_apt(audio, sample_rate,
                              polarity=self.polarity,
                              min_lines=self.min_lines)
        if not out.get("apt_present"):
            return APTDecodeResult(present=False, reason=out.get("reason", "unknown"),
                                   lines=int(out.get("lines_aligned", 0)),
                                   lock_ratio=float(out.get("lock_ratio", 0.0)),
                                   duration_s=float(out.get("duration_s", 0.0)),
                                   satellite=satellite)
        return APTDecodeResult(
            present=True,
            channel_a=out["image_a"],
            channel_b=out["image_b"],
            lines=int(out["lines_aligned"]),
            lock_ratio=float(out["lock_ratio"]),
            duration_s=float(out["duration_s"]),
            satellite=satellite,
        )

    def decode_wav(self, path: str, satellite: str = "") -> APTDecodeResult:
        """从 16-bit PCM WAV 文件解码。"""
        audio, fr = load_wav_mono(path)
        if not satellite:
            satellite = self._guess_satellite(path)
        return self.decode_audio(audio, fr, satellite=satellite)

    # 从文件名/频率猜卫星
    def _guess_satellite(self, path: str) -> str:
        base = os.path.basename(path).upper()
        for name in ("NOAA-15", "NOAA-18", "NOAA-19",
                     "NOAA15", "NOAA18", "NOAA19"):
            if name in base:
                return name.replace("NOAA1", "NOAA-1")
        return ""

    @staticmethod
    def save_png(result: APTDecodeResult, prefix: str) -> Dict[str, str]:
        """A/B/combo 三张 PNG，返回路径字典。"""
        from PIL import Image
        os.makedirs(os.path.dirname(os.path.abspath(prefix)), exist_ok=True)
        paths: Dict[str, str] = {}
        if result.combo() is not None:
            p = f"{prefix}_apt.png"
            Image.fromarray(result.combo(), mode="L").save(p)
            paths["combo"] = os.path.abspath(p)
        if result.channel_a is not None:
            p = f"{prefix}_apt_a.png"
            Image.fromarray(result.channel_a, mode="L").save(p)
            paths["a"] = os.path.abspath(p)
        if result.channel_b is not None:
            p = f"{prefix}_apt_b.png"
            Image.fromarray(result.channel_b, mode="L").save(p)
            paths["b"] = os.path.abspath(p)
        return paths


# --------------------------------------------------------------------------- #
# 增强：AI 自动识别卫星过境类型 → 选解码器
# --------------------------------------------------------------------------- #
def auto_detect_decoder(freq_hz: float, tol_hz: float = 15e3) -> Dict[str, Any]:
    """按下行频率返回应使用的解码器与卫星元数据。

    返回 {decoder, satellite, norad, freq_hz} 或 {decoder: None, reason}。
    """
    table = [
        ("NOAA-15", 25338, "noaa_apt", 137.6200e6),
        ("NOAA-18", 28654, "noaa_apt", 137.9125e6),
        ("NOAA-19", 33591, "noaa_apt", 137.1000e6),
        ("METOP-A", 38771, "metop_ahrpt", 1701.300e6),
        ("METOP-B", 49998, "metop_ahrpt", 1707.000e6),
        ("METOP-C", 76109, "metop_ahrpt", 1707.000e6),
        ("FENGYUN-3D", 54234, "fengyun_mpt", 1704.500e6),
    ]
    for name, norad, decoder, f in table:
        if abs(freq_hz - f) <= tol_hz:
            return {"decoder": decoder, "satellite": name,
                    "norad": norad, "freq_hz": f}
    return {"decoder": None, "reason": f"无已知下行频率 {freq_hz/1e6:.4f} MHz"}
