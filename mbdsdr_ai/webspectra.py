# SPDX-License-Identifier: MIT
"""WebSpectra 瀑布流编码/解码（OpenWebRX 风格）。

上游对照（docs/learn/porting_2026_09_27.md §2）：
  - ``csdr/chain/fft.py:25-49``  FFT 链 = Fft → LogPower(+avg) → FftSwap → (FftAdpcm)
  - ``owrx/connection.py:372-376`` 二进制帧首字节：
        ``0x01``=频谱，``0x02``=音频，``0x03``=副 FFT，``0x04``=HD 音频
  - ``owrx/connection.py:373``   ``write_spectrum_data`` = ``bytes([0x01]) + data``

本模块做独立可测的编/解码（不依赖 Web/线程）：
  * :class:`WebSpectraEncoder` — FFT→dB→uint8 量化→可选 IMA ADPCM→二进制帧
  * :class:`WebSpectraDecoder` — 二进制帧→dB 数组
  * 峰值保留降采样（块内 max），窄带信号不被平均稀释。

帧格式（大端）::
    >B    0x01 频谱标记
    >I    center_freq_hz
    >I    sample_rate_hz
    >f    db_min
    >f    db_max
    >B    compression (0=none, 1=adpcm)
    payload   uint8[] (未压缩) 或 ADPCM 字节流
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np
SPECTRUM_MARKER = 0x01

# ">B I I f f B": marker, center_hz, samp_hz, db_min, db_max, compression
_HEADER = struct.Struct(">B I I f f B")

# ---------------------------------------------------------------------------
# IMA/DVI 4-bit ADPCM 步长与索引表（标准表，确定性）
# ---------------------------------------------------------------------------
_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 21, 23, 25, 28,
    31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
    544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
    16818, 18500, 20350, 22385, 24623, 27086, 29794, 32774, 36054, 39659,
    43625, 47988, 52787, 58067, 63874, 70261, 77287, 85016, 93518, 102870,
    113157, 124473, 136920, 150612, 165673, 182241, 200465, 220512, 242564,
    266820, 293502, 322852, 355137, 390651, 429716, 472688, 519957, 571953,
    629145,
]

_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8] * 16  # 简化：deltas 0..7 映射


def _adpcm_encode(samples: np.ndarray) -> bytes:
    """IMA 4-bit ADPCM 编码。首样本存为 raw int16，后续 4-bit 差分打包。"""
    samples = np.asarray(samples, dtype=np.int16).flatten()
    if len(samples) == 0:
        return b""
    out = bytearray()
    out += struct.pack("<h", int(samples[0]))
    pred = int(samples[0])
    step_idx = 0
    nibbles: list = []
    for s in samples[1:]:
        step = _STEP_TABLE[step_idx]
        diff = int(s) - pred
        sign = 1 if diff < 0 else 0
        delta = abs(diff)
        mag = 0
        adjusted = step >> 3
        if delta >= step:
            mag |= 4
            delta -= step
            adjusted += step
        if delta >= (step >> 1):
            mag |= 2
            adjusted += step >> 1
        if delta >= (step >> 2):
            mag |= 1
            adjusted += step >> 2
        code = (sign << 3) | mag
        pred += -adjusted if sign else adjusted
        pred = int(np.clip(pred, -32768, 32767))
        step_idx += _INDEX_TABLE[code & 0x07]
        step_idx = int(np.clip(step_idx, 0, len(_STEP_TABLE) - 1))
        nibbles.append(code)
    for i in range(0, len(nibbles), 2):
        hi = nibbles[i]
        lo = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        out.append((hi << 4) | lo)
    return bytes(out)


def _adpcm_decode(data: bytes) -> np.ndarray:
    """IMA 4-bit ADPCM 解码。"""
    if len(data) < 2:
        return np.array([], dtype=np.int16)
    pred = struct.unpack_from("<h", data, 0)[0]
    out = [pred]
    step_idx = 0
    payload = data[2:]
    nibbles = []
    for byte in payload:
        nibbles.append((byte >> 4) & 0x0F)
        nibbles.append(byte & 0x0F)
    for code in nibbles:
        sign = (code >> 3) & 1
        mag = code & 0x07
        step = _STEP_TABLE[step_idx]
        delta = step >> 3
        if mag & 4:
            delta += step
        if mag & 2:
            delta += step >> 1
        if mag & 1:
            delta += step >> 2
        pred += -delta if sign else delta
        pred = int(np.clip(pred, -32768, 32767))
        step_idx += _INDEX_TABLE[code & 0x07]
        step_idx = int(np.clip(step_idx, 0, len(_STEP_TABLE) - 1))
        out.append(pred)
    return np.array(out, dtype=np.int16)


# ---------------------------------------------------------------------------
# 编码器
# ---------------------------------------------------------------------------

@dataclass
class WebSpectraResult:
    frame: bytes               # 二进制帧
    db: np.ndarray             # 量化前的 dB 数组（target_bins 长度）
    bins_u8: np.ndarray         # uint8 量化值（未压缩时与 payload 一致）


class WebSpectraEncoder:
    """FFT→dB→uint8→(可选 ADPCM)→二进制帧。

    参数
    ----------
    fft_size : int
        FFT 点数。
    target_bins : int
        降采样后的 bins 数。
    db_min, db_max : float
        量化范围。
    compression : str
        ``'none'`` 或 ``'adpcm'``。
    """

    def __init__(
        self,
        fft_size: int = 1024,
        target_bins: int = 256,
        db_min: float = -120.0,
        db_max: float = -20.0,
        compression: str = "none",
    ):
        self.fft_size = int(fft_size)
        self.target_bins = int(target_bins)
        self.db_min = float(db_min)
        self.db_max = float(db_max)
        self.compression = compression
        self._win = np.hanning(self.fft_size).astype(np.float64)
        self._win_norm = float(np.sum(self._win)) or 1.0

    # ------------------------------------------------------------------
    def compute_db(self, iq: np.ndarray) -> np.ndarray:
        """加窗 FFT → fftshift → dB。"""
        iq = np.asarray(iq, dtype=np.complex128)
        if len(iq) < self.fft_size:
            iq = np.pad(iq, (0, self.fft_size - len(iq)))
        else:
            iq = iq[-self.fft_size:]
        spec = np.fft.fftshift(np.fft.fft(iq * self._win))
        mag = np.abs(spec) / self._win_norm
        return 20.0 * np.log10(mag + 1e-12)

    # ------------------------------------------------------------------
    def peak_downsample(self, db: np.ndarray) -> np.ndarray:
        """块内 max 降采样到 target_bins（峰值保留）。"""
        n = len(db)
        block = max(1, n // self.target_bins)
        usable = block * self.target_bins
        trimmed = db[:usable].reshape(self.target_bins, block)
        return trimmed.max(axis=1)

    # ------------------------------------------------------------------
    def quantize(self, db: np.ndarray) -> np.ndarray:
        """线性映射到 0..255 uint8。"""
        scaled = np.clip((db - self.db_min) / (self.db_max - self.db_min), 0.0, 1.0)
        return (scaled * 255.0).round().astype(np.uint8)

    # ------------------------------------------------------------------
    def encode(self, iq: np.ndarray, center_freq_hz: float,
               sample_rate_hz: float) -> WebSpectraResult:
        db_full = self.compute_db(iq)
        db = self.peak_downsample(db_full)
        bins = self.quantize(db)

        if self.compression == "adpcm":
            # uint8→int16 做 IMA ADPCM；负载前两字节存样本数（小端 uint16）
            adpcm = _adpcm_encode(bins.astype(np.int16))
            payload = struct.pack("<H", len(bins)) + adpcm
            comp_flag = 1
        else:
            payload = bins.tobytes()
            comp_flag = 0

        frame = _HEADER.pack(
            SPECTRUM_MARKER,
            int(center_freq_hz),
            int(sample_rate_hz),
            float(self.db_min),
            float(self.db_max),
            comp_flag,
        ) + payload
        return WebSpectraResult(frame=frame, db=db, bins_u8=bins)


# ---------------------------------------------------------------------------
# 解码器
# ---------------------------------------------------------------------------

@dataclass
class WebSpectraFrame:
    center_freq_hz: int
    sample_rate_hz: int
    db_min: float
    db_max: float
    compression: int
    bins_u8: np.ndarray        # 还原出的 uint8 值
    db: np.ndarray             # 还原出的 dB 值


class WebSpectraDecoder:
    """二进制帧 → dB 数组。"""

    @staticmethod
    def decode(frame: bytes) -> WebSpectraFrame:
        if len(frame) < _HEADER.size:
            raise ValueError("帧太短")
        marker, center, sr, db_min, db_max, comp = _HEADER.unpack_from(frame, 0)
        if marker != SPECTRUM_MARKER:
            raise ValueError(f"不是频谱帧（marker=0x{marker:02x}）")
        payload = frame[_HEADER.size:]
        if comp == 1:
            n_samples = struct.unpack_from("<H", payload, 0)[0]
            adpcm_data = payload[2:]
            bins16 = _adpcm_decode(adpcm_data)[:n_samples]
            bins = bins16.astype(np.uint8)
        else:
            bins = np.frombuffer(payload, dtype=np.uint8)
        # 还原 dB：bins/255*(db_max-db_min)+db_min
        db = bins.astype(np.float32) / 255.0 * (db_max - db_min) + db_min
        return WebSpectraFrame(
            center_freq_hz=int(center),
            sample_rate_hz=int(sr),
            db_min=float(db_min),
            db_max=float(db_max),
            compression=int(comp),
            bins_u8=bins,
            db=db,
        )


__all__ = [
    "SPECTRUM_MARKER",
    "WebSpectraEncoder",
    "WebSpectraDecoder",
    "WebSpectraResult",
    "WebSpectraFrame",
]
