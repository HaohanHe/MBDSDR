# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/web/streamer.py — 频谱 / 音频流式推送
================================================

频谱 / 音频流式推送设计（OpenWebRX 仅作技术参考，本仓未包含其源代码）：
  - 一份 FFT 全局只算一次，再扇出给所有订阅者
  - 每连接一个出站队列，慢客户端背压被踢
  - 二进制帧首字节区分流类型（0x01=频谱, 0x02=音频）

设计原则（红线）：
  * 没有后端 / 没有数据时，`latest()` 返回 None，绝不造假频谱。
  * 一份 FFT 扇出给所有订阅者（多客户端共享同一个 SDR 后端）。
  * 压缩用「块最大值降采样 + uint8 量化」：峰值频点不丢
    （用确定性 numpy 实现，便于单测）。

二进制帧格式（小端/大端）：
  频谱帧: struct ">B I I f f" + uint8[target_bins]
      B  = 0x01
      I  = center_freq_hz (Hz)
      I  = samp_rate_hz (Hz)
      f  = db_min
      f  = db_max
  音频帧: b"\\x02" + int16_le PCM bytes
"""

from __future__ import annotations

import logging
import math
import queue
import struct
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

SPECTRUM_MARKER = 0x01
AUDIO_MARKER = 0x02

# ">B I I f f": marker, center_hz, samp_rate_hz, db_min, db_max
_SPEC_HEADER = struct.Struct(">B I I f f")


def decode_spectrum_frame(data: bytes) -> Dict[str, Any]:
    """把 _SpectrumStreamer 发出的二进制帧解回 dict（供前端 / 测试用）。"""
    if len(data) < _SPEC_HEADER.size + 1 or data[0] != SPECTRUM_MARKER:
        raise ValueError("not a spectrum frame")
    marker, center_hz, samp_hz, db_min, db_max = _SPEC_HEADER.unpack_from(data, 0)
    bins = np.frombuffer(data[_SPEC_HEADER.size:], dtype=np.uint8).astype(np.float32)
    return {
        "center_freq_hz": int(center_hz),
        "samp_rate_hz": int(samp_hz),
        "db_min": float(db_min),
        "db_max": float(db_max),
        "bins": bins,
    }


class _Broadcaster:
    """订阅者扇出基类：维护订阅集合，push() 广播到每个订阅者的出站队列。

    订阅者必须提供 `.out: queue.Queue` 和 `.close()`。队列满说明该客户端读不动，
    直接摘掉（队列满即关闭该客户端连接）。
    """

    def __init__(self, max_queue: int = 64):
        self._subs = set()
        self._lock = threading.Lock()
        self._max_queue = max_queue

    def add_subscriber(self, sub) -> None:
        with self._lock:
            self._subs.add(sub)
        logger.debug("subscriber added: total=%d", self.subscriber_count())

    def remove_subscriber(self, sub) -> None:
        with self._lock:
            self._subs.discard(sub)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def broadcast(self, frame: bytes) -> None:
        with self._lock:
            subs = list(self._subs)
        dead = []
        for sub in subs:
            try:
                sub.out.put_nowait(frame)
            except queue.Full:
                dead.append(sub)
            except Exception:  # pragma: no cover - 防御性
                dead.append(sub)
        for sub in dead:
            self.remove_subscriber(sub)
            try:
                sub.close()
            except Exception:
                pass


class SpectrumStreamer(_Broadcaster):
    """FFT → dB → 峰值保留降采样 → uint8 量化 → WebSocket 广播。

    后端线程周期调用 :meth:`push_iq`；HTTP 层通过 :meth:`latest` 取最近一帧供
    ``GET /api/spectrum``。无数据时 :meth:`latest` 返回 None。
    """

    def __init__(
        self,
        fft_size: int = 1024,
        target_bins: int = 256,
        fps: float = 10.0,
        db_min: float = -120.0,
        db_max: float = -20.0,
    ):
        super().__init__()
        self.fft_size = int(fft_size)
        self.target_bins = int(target_bins)
        self.fps = float(fps)
        self.db_min = float(db_min)
        self.db_max = float(db_max)

        self._latest: Optional[Dict[str, Any]] = None
        self._annotations: List[Dict[str, Any]] = []
        self._last_emit = 0.0
        self._win = np.hanning(self.fft_size).astype(np.float64)
        self._win_norm = float(np.sum(self._win)) or 1.0

    # ---- AI 标注（我们的增强） -------------------------------------------
    def set_annotations(self, anns: List[Dict[str, Any]]) -> None:
        """外部 AI/分类器注入信号标注：[{"freq_hz","label","suggested_mode","confidence"}]。"""
        self._annotations = list(anns or [])

    def annotations(self) -> List[Dict[str, Any]]:
        return list(self._annotations)

    # ---- FFT 计算 --------------------------------------------------------
    def compute_spectrum_db(self, iq: np.ndarray) -> np.ndarray:
        """对一段复采样做加窗 FFT → fftshift → dB（直流居中）。"""
        iq = np.asarray(iq)
        if iq.ndim != 1 or len(iq) == 0:
            raise ValueError("iq must be a non-empty 1-D complex array")
        if len(iq) < self.fft_size:
            iq = np.pad(iq, (0, self.fft_size - len(iq)))
        elif len(iq) > self.fft_size:
            iq = iq[-self.fft_size:]
        windowed = iq.astype(np.complex128) * self._win
        spec = np.fft.fftshift(np.fft.fft(windowed))
        mag = np.abs(spec) / self._win_norm
        return 20.0 * np.log10(mag + 1e-12)

    def compress(self, db: np.ndarray) -> np.ndarray:
        """峰值保留降采样 + uint8 量化。

        块内取 max 而非 mean：窄带信号（关键频点）不会被平均稀释掉。
        """
        n = len(db)
        target = self.target_bins
        block = max(1, n // target)
        usable = block * target
        trimmed = db[:usable].reshape(target, block)
        peak = trimmed.max(axis=1)
        scaled = np.clip((peak - self.db_min) / (self.db_max - self.db_min), 0.0, 1.0)
        return (scaled * 255.0).round().astype(np.uint8)

    # ---- 后端入口 --------------------------------------------------------
    def push_iq(
        self,
        iq: np.ndarray,
        center_freq_hz: float,
        samp_rate_hz: float,
        force: bool = False,
    ) -> Optional[bytes]:
        """后端推送一段 IQ；按 fps 限速，返回广播的二进制帧（或 None=被限速）。"""
        now = time.monotonic()
        if not force and self.fps > 0 and (now - self._last_emit) < 1.0 / self.fps:
            return None

        db = self.compute_spectrum_db(iq)
        bins = self.compress(db)

        # 峰值频点（Hz）——供 /api/spectrum 报告
        peak_idx = int(np.argmax(bins))
        bin_span = samp_rate_hz / self.target_bins
        peak_freq = center_freq_hz - samp_rate_hz / 2.0 + (peak_idx + 0.5) * bin_span

        frame = _SPEC_HEADER.pack(
            SPECTRUM_MARKER,
            int(center_freq_hz),
            int(samp_rate_hz),
            float(self.db_min),
            float(self.db_max),
        ) + bins.tobytes()

        self._latest = {
            "center_freq_hz": float(center_freq_hz),
            "samp_rate_hz": float(samp_rate_hz),
            "db_min": self.db_min,
            "db_max": self.db_max,
            "bins": bins,
            "peak_bin": peak_idx,
            "peak_freq_hz": float(peak_freq),
        }
        self._last_emit = now
        self.broadcast(frame)
        return frame

    def latest(self) -> Optional[Dict[str, Any]]:
        return self._latest

    def has_frame(self) -> bool:
        return self._latest is not None

    def clear(self) -> None:
        """后端断开时调用：清空缓存，恢复 connected=false。"""
        self._latest = None
        self._annotations = []


class AudioStreamer(_Broadcaster):
    """音频块 → int16 PCM → WebSocket 二进制帧广播。

    （二进制音频帧首字节为 0x02。）
    """

    def __init__(self, sample_rate: int = 48000, max_queue: int = 64):
        super().__init__(max_queue=max_queue)
        self.sample_rate = int(sample_rate)

    def push_audio(self, pcm: np.ndarray) -> bytes:
        """pcm: int16 mono ndarray（或会被转换）。返回广播帧。"""
        arr = np.asarray(pcm)
        if arr.dtype != np.int16:
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)
        frame = bytes([AUDIO_MARKER]) + arr.astype("<i2").tobytes()
        self.broadcast(frame)
        return frame
