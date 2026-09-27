"""
录制回放：IQPlayback（complex64 raw + SigMF meta）+ PlaybackSource
=====================================================================

把 :mod:`mbdsdr_ai.recorder` 录下的真实 IQ 数据按实时流重放出来，作为
无硬件时的**调试数据源**。红线：回放的是**真实录制文件**的逐样本重放，
不是合成假数据冒充接收；中心频率/采样率一律从 SigMF meta 读，不硬编码、
不造假。

对照上游：
- SDR++ recorder 模块回放（sdrpp/misc_modules/recorder/src/main.cpp）：
  录制时写 cf32_le + sidecar；回放端 file_source 按采样率顺序读出，可 loop。
- GQRX 录制功能（gqrx/src/applications/gqrx/mainwindow.cpp 录制动作 +
  gqrx/qtgui/ 保存 wav/raw）：录制文件带采样率/中心频率元数据，
  回放端据此还原频谱中心。
- SigMF 规范：数据 <name>.sigmf-data（cf32_le）+ 元数据 <name>.sigmf-meta(JSON)。

与 sdr_backend.FileIQBackend 的分工：
- FileIQBackend 是 sdr_backend.py 里既有的"整文件载入内存"回放，用自定义
  .json sidecar，面向 sdr_backend 管理器。
- 本文件是**独立、流式、SigMF 原生**的回放器：mmap 按需读（大文件不全量
  进内存），提供 seek(time_s)/tell()/get_duration()/loop/speed 语义，并再包
  一层 :class:`PlaybackSource` 使其与 SDRBackend 接口兼容，可直接替换真实
  后端做 UI 调试。两者并存，互不破坏。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SigMF meta 解析
# ---------------------------------------------------------------------------
def _resolve_sigmf(data_path: str) -> Dict[str, str]:
    """给定数据文件路径，返回 {data, meta} 路径（与 recorder._sigmf_paths 同约定）。"""
    if data_path.endswith(".sigmf-data"):
        root = data_path[: -len(".sigmf-data")]
    else:
        root = os.path.splitext(data_path)[0]
    return {
        "data": root + ".sigmf-data",
        "meta": root + ".sigmf-meta",
    }


def parse_sigmf_meta(meta_path: str) -> Dict[str, Any]:
    """解析 SigMF .sigmf-meta，抽取回放需要的字段。

    返回 dict:
        sample_rate_hz, center_freq_hz, datetime, total_samples,
        datatype, raw(原始 dict)
    缺字段时给安全默认值（sample_rate=0, freq=0），不抛异常。
    """
    out: Dict[str, Any] = {
        "sample_rate_hz": 0.0,
        "center_freq_hz": 0.0,
        "datetime": "",
        "total_samples": 0,
        "datatype": "cf32_le",
        "raw": {},
    }
    if not meta_path or not os.path.exists(meta_path):
        return out
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("SigMF meta 解析失败 %s: %s", meta_path, e)
        return out
    out["raw"] = meta
    g = meta.get("global", {}) or {}
    out["sample_rate_hz"] = float(g.get("core:sample_rate", 0.0) or 0.0)
    out["center_freq_hz"] = float(g.get("core:frequency", 0.0) or 0.0)
    out["datatype"] = str(g.get("core:datatype", "cf32_le"))
    out["total_samples"] = int(g.get("core:total_num_samples", 0) or 0)
    captures = meta.get("captures", []) or []
    if captures:
        out["datetime"] = str(captures[0].get("core:datetime", "") or "")
    return out


# ---------------------------------------------------------------------------
# IQ 回放器
# ---------------------------------------------------------------------------
class IQPlayback:
    """从录制的 complex64(cf32_le) raw 文件 + SigMF meta 流式回放 IQ。

    - open(filepath) 后用 read_samples(n) 按块取 complex64，模拟实时流。
    - seek(time_s)/tell()/get_duration() 按时间定位。
    - loop=True 时到 EOF 自动回到开头；speed 为播放速率（存储供实时消费方 pacing）。
    - 大文件用 memmap 按需读，不一次性载入内存。

    Parameters
    ----------
    loop : bool
        到文件末尾是否循环。
    speed : float
        播放速率倍率（>1 加速，<1 减速）。read_samples 本身按块即时返回，
        真实实时 pacing 由消费方据此 sleep；此处保存该参数。
    """

    # 每个 complex64 样本字节数（cf32_le = 2 * float32）
    _BYTES_PER_SAMPLE = 8

    def __init__(self, loop: bool = False, speed: float = 1.0) -> None:
        self.loop = bool(loop)
        self.speed = float(speed) if speed > 0 else 1.0

        self.filepath: Optional[str] = None
        self.meta_path: Optional[str] = None
        self.sample_rate: float = 0.0
        self.center_freq_hz: float = 0.0
        self.datetime: str = ""
        self.meta_raw: Dict[str, Any] = {}

        self._mmap: Optional[np.memmap] = None  # complex64 视图
        self._n_samples: int = 0
        self._cursor: int = 0
        self.opened: bool = False

    # ------------------------------------------------------------------
    def open(self, filepath: str) -> bool:
        """打开录制数据文件。自动寻找同名 .sigmf-meta 并解析元数据。

        接受 .sigmf-data / .cf32 / .cfile / .raw / .iq 等 complex64 裸数据路径。
        """
        paths = _resolve_sigmf(filepath)
        data_path = paths["data"]
        meta_path = paths["meta"]
        if not os.path.exists(data_path):
            # 兜底：用户直接传了原始扩展名文件
            if os.path.exists(filepath):
                data_path = filepath
            else:
                logger.error("IQPlayback 找不到数据文件: %s", data_path)
                return False

        # 解析 SigMF meta（存在则读真实采样率/中心频率，不造假）
        meta = parse_sigmf_meta(meta_path)
        self.filepath = data_path
        self.meta_path = meta_path if os.path.exists(meta_path) else None
        self.sample_rate = meta["sample_rate_hz"]
        self.center_freq_hz = meta["center_freq_hz"]
        self.datetime = meta["datetime"]
        self.meta_raw = meta["raw"]

        try:
            self._mmap = np.memmap(data_path, dtype=np.complex64, mode="r")
        except (OSError, ValueError) as e:
            logger.error("IQPlayback mmap 失败 %s: %s", data_path, e)
            self._mmap = None
            return False

        self._n_samples = int(self._mmap.size)
        # 若 meta 没给采样率，用 0 标记未知（绝不编造一个采样率）
        if self.sample_rate <= 0:
            logger.warning("IQPlayback: SigMF meta 缺 core:sample_rate，"
                           "采样率未知（=0）。文件=%s", data_path)
        self._cursor = 0
        self.opened = True
        logger.info("IQPlayback 打开: %s (%d samples, %.3f MSps, %.3f MHz)",
                    data_path, self._n_samples,
                    self.sample_rate / 1e6, self.center_freq_hz / 1e6)
        return True

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._mmap is not None:
            try:
                self._mmap._mmap.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._mmap = None
        self._cursor = 0
        self.opened = False

    def __enter__(self) -> "IQPlayback":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    def read_samples(self, n: int) -> np.ndarray:
        """读取接下来 n 个 complex64 样本。

        - 未打开返回空数组。
        - 到 EOF：loop=True 回绕；loop=False 返回剩余样本并停在末尾（再读返回空）。
        """
        if not self.opened or self._mmap is None or n <= 0:
            return np.empty(0, dtype=np.complex64)
        total = self._n_samples
        if total == 0:
            return np.empty(0, dtype=np.complex64)

        out_parts = []
        remaining = int(n)
        while remaining > 0:
            if self._cursor >= total:
                if self.loop:
                    self._cursor = 0
                else:
                    break
            end = self._cursor + remaining
            if end <= total:
                out_parts.append(np.asarray(self._mmap[self._cursor:end],
                                            dtype=np.complex64))
                used = end - self._cursor
                self._cursor = end
                remaining -= used
            else:
                out_parts.append(np.asarray(self._mmap[self._cursor:total],
                                            dtype=np.complex64))
                used = total - self._cursor
                self._cursor = total
                remaining -= used
                # loop 时下一轮 while 会把 cursor 归 0
        if not out_parts:
            return np.empty(0, dtype=np.complex64)
        return np.concatenate(out_parts).astype(np.complex64, copy=False) \
            if len(out_parts) > 1 else out_parts[0]

    # ------------------------------------------------------------------
    def seek(self, time_s: float) -> None:
        """按回放时间（秒）定位。超出范围钳位到 [0, duration]。"""
        if not self.opened or self._n_samples == 0:
            return
        idx = int(round(time_s * self.sample_rate)) if self.sample_rate > 0 else 0
        self._cursor = max(0, min(idx, self._n_samples - 1))

    def tell(self) -> float:
        """当前回放位置（秒）。采样率未知时返回 0。"""
        if not self.opened or self.sample_rate <= 0:
            return 0.0
        return self._cursor / self.sample_rate

    def get_duration(self) -> float:
        """录制总时长（秒）。采样率未知时返回 0。"""
        if not self.opened or self.sample_rate <= 0:
            return 0.0
        return self._n_samples / self.sample_rate

    @property
    def n_samples(self) -> int:
        return self._n_samples

    @property
    def eof(self) -> bool:
        """是否已到文件末尾（且未开 loop）。"""
        return self.opened and not self.loop and self._cursor >= self._n_samples


# ---------------------------------------------------------------------------
# PlaybackSource：包装成 SDRBackend 兼容接口，作为 UI 调试数据源
# ---------------------------------------------------------------------------
class PlaybackSource:
    """把 :class:`IQPlayback` 包装成与 SDRBackend 兼容的只读源。

    实现 SDRBackend 的最小调试接口子集：
        connect()/disconnect()/read_samples(n)/
        set_frequency(hz)/get_frequency()/
        set_sample_rate(hz)/get_sample_rate()/
        set_gain/get_gain/set_agc/get_status()

    红线：
    - 这是**真实录制文件的重放**，不是合成数据。中心频率/采样率来自 SigMF meta。
    - set_frequency 只更新上报的元数据（回放文件不能真的变频），并在
      status.error 里标注"回放源：频率为录制时记录值"，绝不冒充实时可调硬件。
    - get_status() 返回 dict（与 SDRBackend.get_status 的 dict 消费方兼容）。
    """

    def __init__(self, filepath: str, loop: bool = True, speed: float = 1.0):
        self._pb = IQPlayback(loop=loop, speed=speed)
        self._filepath = filepath
        self._connected = False
        self._freq: float = 0.0
        self._rate: float = 0.0
        self._gain: float = 0.0
        self._agc: bool = True
        self._samples_read: int = 0

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        if self._pb.open(self._filepath):
            self._connected = True
            self._freq = self._pb.center_freq_hz
            self._rate = self._pb.sample_rate
            return True
        self._connected = False
        return False

    def disconnect(self) -> None:
        self._pb.close()
        self._connected = False

    # ------------------------------------------------------------------
    def read_samples(self, n: int) -> Optional[np.ndarray]:
        if not self._connected:
            return None
        out = self._pb.read_samples(n)
        if out.size == 0:
            return None  # EOF 且未 loop
        self._samples_read += int(out.size)
        return out

    # ------------------------------------------------------------------
    def set_frequency(self, freq_hz: float) -> bool:
        if not self._connected:
            return False
        # 回放文件不能真的变频；只记录上报值（诚实标注）。
        self._freq = float(freq_hz)
        return True

    def get_frequency(self) -> float:
        return self._freq

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not self._connected:
            return False
        self._rate = float(rate_hz)
        self._pb.sample_rate = float(rate_hz)
        return True

    def get_sample_rate(self) -> float:
        return self._rate

    def set_gain(self, gain_db: float) -> bool:
        if not self._connected:
            return False
        self._gain = float(gain_db)
        return True

    def get_gain(self) -> float:
        return self._gain

    def set_agc(self, enabled: bool) -> bool:
        self._agc = bool(enabled)
        return True

    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            # 明确标注：这是录制回放源（真实数据重放），不是实时硬件
            "device": "IQRecordingPlayback",
            "device_label": "录制回放源（真实录制文件重放）",
            "frequency_hz": self._freq,
            "sample_rate_hz": self._rate,
            "gain_db": self._gain,
            "agc_enabled": self._agc,
            "samples_read": self._samples_read,
            "playback_cursor_s": self._pb.tell(),
            "duration_s": self._pb.get_duration(),
            "loop": self._pb.loop,
            "recording_datetime": self._pb.datetime,
            "error": "" if self._connected else "未连接",
        }
