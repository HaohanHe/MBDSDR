# SPDX-License-Identifier: MIT
"""
录制增强：命名模板 + 电平表 + 时长跟踪（包装 AudioRecorder）
============================================================

对照 GQRX 录制动作（gqrx/src/applications/gqrx/mainwindow.cpp）：
- GQRX 录音文件按 ``<freq>_<mode>_<timestamp>.wav`` 命名，便于事后按频率/
  模式/时间检索。
- GQRX 录音时电平表（qtgui/dockrxopt / 录音状态条）实时显示 dBFS。

本项目 recorder.AudioRecorder 已是 WAV 48k/16bit 流式写入，且已有
``duration_s`` 属性；但缺：
  1. 自动命名模板；
  2. 录音电平表（get_record_level -> dBFS）；
  3. get_record_duration() 显式接口。

本文件**不改 recorder.py**，而是子类化 AudioRecorder 补齐这三点，二者并存。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np

from .recorder import AudioRecorder

logger = logging.getLogger(__name__)


def make_audio_filename(freq_hz: float, mode: str,
                        out_dir: str = ".",
                        timestamp: Optional[datetime] = None) -> str:
    """按 GQRX 模板生成录音路径: ``{freq}_{mode}_{timestamp}.wav``。

    - freq_hz: 中心频率(Hz)，格式化为 kHz 整数（如 145000000 → "145000000"）。
    - mode:    解调模式（FM/AM/USB...），大写。
    - timestamp: 默认 now(本地时区)；格式 ``%Y%m%d_%H%M%S``。
    """
    ts = timestamp or datetime.now()
    freq_str = f"{int(round(freq_hz))}"
    mode_str = (mode or "RAW").upper().replace("/", "_")
    fname = f"{freq_str}_{mode_str}_{ts.strftime('%Y%m%d_%H%M%S')}.wav"
    return os.path.join(out_dir, fname)


class EnhancedAudioRecorder(AudioRecorder):
    """AudioRecorder + 自动命名 + 电平表 + 时长接口。

    Usage::

        rec = EnhancedAudioRecorder.from_params(
            freq_hz=145e6, mode="FM", out_dir="/tmp/rec")
        rec.open()
        for block in audio_stream:
            rec.write(block)
            db = rec.get_record_level()   # 实时电平 dBFS
        dur = rec.get_record_duration()
        rec.close()
    """

    def __init__(self, path: str, sample_rate: int = 48000, channels: int = 1):
        super().__init__(path, sample_rate=sample_rate, channels=channels)
        self._last_rms: float = 0.0       # 最近一块 RMS（线性幅度）
        self._peak_dbfs: float = -np.inf

    @classmethod
    def from_params(cls, freq_hz: float, mode: str, out_dir: str = ".",
                    sample_rate: int = 48000, channels: int = 1,
                    timestamp: Optional[datetime] = None) -> "EnhancedAudioRecorder":
        """按 GQRX 命名模板构造一个录音器（文件未 open，仅算好路径）。"""
        path = make_audio_filename(freq_hz, mode, out_dir, timestamp)
        return cls(path, sample_rate=sample_rate, channels=channels)

    # ------------------------------------------------------------------
    def write(self, audio: np.ndarray) -> int:
        """写入一块音频，并更新电平表（RMS dBFS）。"""
        x = np.asarray(audio, dtype=np.float32).ravel()
        if x.size > 0:
            rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
            self._last_rms = rms
        return super().write(audio)

    # ------------------------------------------------------------------
    def get_record_level(self) -> float:
        """最近一块的录音电平（dBFS，满幅 1.0 = 0 dBFS）。

        静音返回 -inf；未写入任何样本返回 -inf。
        """
        if self._last_rms <= 0.0:
            return -np.inf
        return float(20.0 * np.log10(self._last_rms))

    def get_record_duration(self) -> float:
        """已录制时长（秒）。"""
        return self.duration_s
