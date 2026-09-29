# SPDX-License-Identifier: MIT
"""
录制器：IQ（complex64 raw + SigMF sidecar）+ 音频（WAV 48k/16bit）
================================================================

流式录制：边收边写，关闭时落 sidecar 元数据。对照上游：
- sdrpp/misc_modules/recorder/src/main.cpp:166-210
    start() 时开文件、配置采样率/位深/声道；handler_sink 边收边写；
    stop() 关文件。
- SigMF 规范 (https://en.wikipedia.org/wiki/Signal_Metadata_Format)：
    - 数据文件：<name>.sigmf-data（裸 complex64 = cf32_le）
    - 元数据文件：<name>.sigmf-meta（JSON）
        {
          "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 4800000.0,
            "core:frequency": 100000000.0,
            "core:author": "...",
            "core:description": "..."
          },
          "captures": [
            {"core:sample_start": 0, "core:datetime": "2026-09-26T...Z"}
          ],
          "annotations": []
        }

与 mbdsdr_ai/baseband_io.py 的关系：
- baseband_io.save_iq() 是一次性（整段数组存盘）+ 自定义字段 sidecar，
  供 LLM/工具调用。
- recorder.py 是**流式**录制器（recorder 类），边收边写、可关断时落盘，
  且 sidecar 严格 SigMF 兼容（可被 siglib/SigDigger/Inspectrum 直接读）。
两者并存，互不破坏。
"""
from __future__ import annotations

import json
import logging
import os
import wave as _wave
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 文件名/路径约定
# ---------------------------------------------------------------------------
def _sigmf_paths(data_path: str) -> Dict[str, str]:
    """给定数据文件路径，返回 {data, meta} 两个 SigMF 文件路径。

    - data_path 以 .sigmf-data 结尾 → 直接用，meta 换扩展名 .sigmf-meta
    - data_path 以 .cf32/.iq/.raw 结尾 → 替换为 .sigmf-data / .sigmf-meta
    - 其他（无扩展名）→ 加 .sigmf-data / .sigmf-meta
    """
    if data_path.endswith(".sigmf-data"):
        root = data_path[: -len(".sigmf-data")]
    else:
        root = os.path.splitext(data_path)[0]
    return {
        "data": root + ".sigmf-data",
        "meta": root + ".sigmf-meta",
    }


# ---------------------------------------------------------------------------
# IQ 录制器（complex64 raw + SigMF sidecar）
# ---------------------------------------------------------------------------
class IQRecorder:
    """流式 IQ 录制：complex64 裸数据 + SigMF meta。

    Usage::

        rec = IQRecorder("/tmp/iq", sample_rate=2.4e6,
                          center_freq_hz=100e6, gain_db=20.0,
                          device="RTL-SDR", driver="rtlsdr")
        rec.open()
        for iq_block in rx_stream:        # 每块 complex64 (N,)
            rec.write(iq_block)
        rec.close()                        # 写 SigMF meta

    红线：
    - 真实写文件，不造假。open() 失败立即抛错/标 failed。
    - 不硬编码增益范围；gain_db 只是记进 sidecar 的数值。
    - 录制格式可被 SigMF 兼容工具读回（cf32_le）。
    """

    def __init__(self, data_path: str,
                 sample_rate: float,
                 center_freq_hz: float = 0.0,
                 gain_db: float = 0.0,
                 device: str = "",
                 driver: str = "",
                 author: str = "MBDSDR",
                 description: str = ""):
        paths = _sigmf_paths(data_path)
        self.data_path = paths["data"]
        self.meta_path = paths["meta"]
        self.sample_rate = float(sample_rate)
        self.center_freq_hz = float(center_freq_hz)
        self.gain_db = float(gain_db)
        self.device = str(device)
        self.driver = str(driver)
        self.author = str(author)
        self.description = str(description)

        self._fp = None            # 数据文件句柄
        self._n_samples: int = 0   # 已写 complex 样本数
        self._start_time: Optional[str] = None
        self.closed = True

    # ------------------------------------------------------------------
    def open(self) -> bool:
        """打开数据文件准备写。已打开则幂等返回 True。"""
        if self._fp is not None:
            return True
        os.makedirs(os.path.dirname(self.data_path) or ".", exist_ok=True)
        try:
            self._fp = open(self.data_path, "wb")
        except OSError as e:
            logger.error("IQRecorder 打开 %s 失败: %s", self.data_path, e)
            self._fp = None
            return False
        self._start_time = datetime.now(timezone.utc).isoformat()
        self._n_samples = 0
        self.closed = False
        logger.info("IQRecorder 开始录制: %s (%.3f MSps, %.3f MHz)",
                    self.data_path, self.sample_rate / 1e6,
                    self.center_freq_hz / 1e6)
        return True

    # ------------------------------------------------------------------
    def write(self, iq: np.ndarray) -> int:
        """写入一块 complex IQ。返回写入的 complex 样本数。

        自动把任意 complex 数组归一为 complex64 裸交织 I/Q 写出。
        未 open() 时返回 0（不崩）。
        """
        if self._fp is None:
            return 0
        arr = np.asarray(iq)
        if not np.iscomplexobj(arr):
            # 实数输入：当成已交织 [re,im,re,im,...]，重塑成 complex
            flat = arr.astype(np.float32).ravel()
            if flat.size % 2 != 0:
                return 0
            arr = flat[0::2] + 1j * flat[1::2]
        arr64 = arr.astype(np.complex64, copy=False)
        try:
            self._fp.write(arr64.tobytes())
            self._n_samples += int(arr64.size)
            return int(arr64.size)
        except OSError as e:
            logger.error("IQRecorder 写失败: %s", e)
            return 0

    # ------------------------------------------------------------------
    def close(self) -> Optional[str]:
        """关闭数据文件并写 SigMF meta。返回 meta 路径（失败 None）。"""
        if self._fp is not None:
            try:
                self._fp.flush()
                os.fsync(self._fp.fileno())
            except OSError:
                pass
            try:
                self._fp.close()
            except OSError:
                pass
            self._fp = None
        if self.closed:
            return None
        self.closed = True

        # ── 构造 SigMF meta（严格按规范）──
        meta: Dict[str, Any] = {
            "global": {
                "core:datatype": "cf32_le",     # complex float32 little-endian
                "core:sample_rate": self.sample_rate,
                "core:frequency": self.center_freq_hz,
                "core:author": self.author,
                "core:description": self.description,
                "core:total_num_samples": self._n_samples,
                "mbdsdr:gain_db": self.gain_db,
                "mbdsdr:device": self.device,
                "mbdsdr:driver": self.driver,
            },
            "captures": [
                {
                    "core:sample_start": 0,
                    "core:datetime": self._start_time or "",
                }
            ],
            "annotations": [],
        }
        try:
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.meta_path)
            logger.info("IQRecorder 结束: %d samples, meta=%s",
                        self._n_samples, self.meta_path)
            return self.meta_path
        except OSError as e:
            logger.error("IQRecorder 写 meta 失败: %s", e)
            return None

    # ------------------------------------------------------------------
    def __enter__(self) -> "IQRecorder":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def n_samples(self) -> int:
        return self._n_samples

    @property
    def duration_s(self) -> float:
        return self._n_samples / max(self.sample_rate, 1e-9)


# ---------------------------------------------------------------------------
# 音频录制器（WAV, 48kHz, 16bit PCM）
# ---------------------------------------------------------------------------
class AudioRecorder:
    """流式音频录制：WAV 48kHz 16bit PCM（单声道或立体声）。

    对照 sdrpp/misc_modules/recorder/src/main.cpp:178-181
        writer.setFormat(WAV); writer.setSampleType(Int16);
        writer.setSamplerate(samplerate);

    Usage::

        arec = AudioRecorder("/tmp/voice.wav", sample_rate=48000)
        arec.open()
        for audio_block in demod_stream:    # float32 (N,) 或 (N,2)
            arec.write(audio_block)
        arec.close()
    """

    def __init__(self, path: str, sample_rate: int = 48000,
                 channels: int = 1):
        self.path = str(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._wf = None        # wave.Wave_write 句柄
        self._n_frames: int = 0
        self.closed = True

    def open(self) -> bool:
        if self._wf is not None:
            return True
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        try:
            self._wf = _wave.open(self.path, "wb")
            self._wf.setnchannels(self.channels)
            self._wf.setsampwidth(2)           # 16bit
            self._wf.setframerate(self.sample_rate)
        except (OSError, _wave.Error) as e:
            logger.error("AudioRecorder 打开 %s 失败: %s", self.path, e)
            self._wf = None
            return False
        self._n_frames = 0
        self.closed = False
        return True

    def write(self, audio: np.ndarray) -> int:
        """写入一块 float32 音频。自动 clip 到 [-1,1] 并量化到 int16。

        返回写入的帧数（单声道样本数；立体声按帧计）。
        """
        if self._wf is None:
            return 0
        x = np.asarray(audio, dtype=np.float32)
        if x.size == 0:
            return 0
        # 归一化到 int16 满幅（不自动缩放，clip 即可——录音电平由 AGC/手动增益控制）
        clipped = np.clip(x, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        # wave.writeframes 要交错 bytes
        try:
            self._wf.writeframes(pcm.tobytes())
            # 帧数：立体声时 pcm 长度 = frames*channels
            frames = pcm.size // self.channels
            self._n_frames += int(frames)
            return int(frames)
        except (OSError, _wave.Error) as e:
            logger.error("AudioRecorder 写失败: %s", e)
            return 0

    def close(self) -> None:
        if self._wf is not None:
            try:
                self._wf.close()
            except (OSError, _wave.Error):
                pass
            self._wf = None
        self.closed = True

    def __enter__(self) -> "AudioRecorder":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def duration_s(self) -> float:
        return self._n_frames / max(self.sample_rate, 1e-9)
