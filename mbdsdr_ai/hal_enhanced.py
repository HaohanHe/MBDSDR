# SPDX-License-Identifier: MIT
"""
MBDSDR AI - HAL 增强：统一设备健康检查
==========================================

SDR++/GQRX 没有统一的"这台设备现在到底健不健康"的自检入口。本模块在不改动
既有 :mod:`mbdsdr_ai.hal` 的前提下，新增一个只读的 :class:`DeviceHealthChecker`：

- ``check_device(backend)``：读 1024 个样点，判断
  - alive：设备能否返回数据；
  - has_signal：数据是否全零（天线可能断开 / 没接）；
  - 是否含 NaN（前端溢出/数值异常）；
  - sample_rate_correct：实际采样率是否与期望一致（容差 5%）。
- ``check_audio()``：AudioPlayer 是否在跑、音频队列是否堆积（延迟爆掉前兆）。
- ``get_system_info()``：CPU / 内存 / 音频缓冲延迟（psutil 可选，缺失则给 None）。

设计原则（与全项目一致）：
- 无后端 / 无音频设备时返回结构化结果并带 ``error`` 说明，**不造假数据**；
- 所有探测包 try/except，健康检查本身绝不崩主程序。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

#: 健康检查一次读取的样点数
PROBE_SAMPLES = 1024
#: 采样率一致判定容差（相对误差）
SR_TOLERANCE = 0.05


class DeviceHealthChecker:
    """只读设备健康检查器。

    Parameters
    ----------
    backend : object, optional
        默认后端（鸭子类型，需有 ``read_rx(n)``，可选 ``get_status()``）。
    audio_player : AudioPlayer, optional
        关联的音频播放器（用于队列堆积/延迟检查）。
    expected_sample_rate : float, optional
        期望采样率 Hz；提供后 ``check_device`` 会比对实际采样率。
    """

    def __init__(
        self,
        backend: Any = None,
        audio_player: Any = None,
        expected_sample_rate: Optional[float] = None,
    ) -> None:
        self._backend = backend
        self._player = audio_player
        self.expected_sample_rate = expected_sample_rate

    # ------------------------------------------------------------------
    # 设备健康
    # ------------------------------------------------------------------
    def check_device(self, backend: Any = None) -> Dict[str, Any]:
        """探测后端设备健康。返回结构化 dict，绝不抛异常。

        Returns
        -------
        dict
            ``{alive: bool, has_signal: bool, sample_rate_correct: bool|None,
               nan_detected: bool, error: str}``。
        """
        result: Dict[str, Any] = {
            "alive": False,
            "has_signal": False,
            "sample_rate_correct": None,
            "nan_detected": False,
            "error": "",
        }
        backend = backend or self._backend
        if backend is None:
            result["error"] = "no backend attached"
            return result

        # 1) 读样点
        try:
            samples = backend.read_rx(PROBE_SAMPLES)
        except Exception as exc:
            result["error"] = f"read_rx failed: {exc}"
            return result

        if samples is None or len(samples) == 0:
            result["error"] = "read_rx returned no samples"
            return result

        result["alive"] = True
        arr = np.asarray(samples)

        # 2) NaN 检测
        try:
            result["nan_detected"] = bool(np.any(np.isnan(arr)))
        except Exception:
            result["nan_detected"] = False

        # 3) 全零检测（天线断开 / 未接天线时 IQ 全为 0）
        try:
            mag = np.abs(arr.astype(np.complex64))
            result["has_signal"] = bool(np.any(mag > 0))
        except Exception:
            # 非数值数组无法判断时，保守认为有信号（不报错、不造假）
            result["has_signal"] = True

        # 4) 采样率一致性
        try:
            actual_sr = self._read_sample_rate(backend)
            if actual_sr is not None and self.expected_sample_rate:
                exp = float(self.expected_sample_rate)
                if exp > 0:
                    rel = abs(float(actual_sr) - exp) / exp
                    result["sample_rate_correct"] = bool(rel <= SR_TOLERANCE)
        except Exception as exc:
            logger.debug("采样率检查失败: %s", exc)
            result["sample_rate_correct"] = None

        return result

    @staticmethod
    def _read_sample_rate(backend: Any) -> Optional[float]:
        """鸭子类型读取当前采样率：hal 风格 ``_sample_rate`` 或 sdr_backend 风格 status。"""
        sr = getattr(backend, "_sample_rate", None)
        if sr is not None:
            try:
                return float(sr)
            except Exception:
                pass
        try:
            status = backend.get_status()
            sr = getattr(status, "sample_rate_hz", None)
            if sr is not None:
                return float(sr)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # 音频健康
    # ------------------------------------------------------------------
    def check_audio(self, audio_player: Any = None) -> Dict[str, Any]:
        """检查音频播放器是否正常、队列是否堆积。"""
        res: Dict[str, Any] = {
            "running": False,
            "queue_depth_samples": 0,
            "queue_fraction": 0.0,
            "backlog": False,
            "error": "",
        }
        player = audio_player or self._player
        if player is None:
            res["error"] = "no audio player attached"
            return res
        try:
            res["running"] = bool(getattr(player, "is_playing", False))
            queued = int(getattr(player, "_queued_samples", 0) or 0)
            cap = int(getattr(player, "_MAX_QUEUED_SAMPLES", 1) or 1)
            res["queue_depth_samples"] = queued
            res["queue_fraction"] = queued / float(cap)
            # 队列占用超过 90% 视为堆积（上游喂太快 / 输出设备卡顿）
            res["backlog"] = queued > cap * 0.9
        except Exception as exc:
            res["error"] = f"audio check failed: {exc}"
        return res

    # ------------------------------------------------------------------
    # 系统信息
    # ------------------------------------------------------------------
    def get_system_info(self) -> Dict[str, Any]:
        """CPU / 内存 / 音频缓冲延迟。psutil 缺失时对应项为 None（不造假）。"""
        info: Dict[str, Any] = {
            "cpu_percent": None,
            "memory_percent": None,
            "audio_latency_ms": None,
        }
        try:
            import psutil  # type: ignore
            info["cpu_percent"] = float(psutil.cpu_percent(interval=0.05))
            info["memory_percent"] = float(psutil.virtual_memory().percent)
        except Exception:
            # psutil 未装或采样失败：保持 None，不抛
            pass

        # 音频延迟 ≈ 已排队样本数 / 采样率
        try:
            p = self._player
            if p is not None:
                sr = float(getattr(p, "sample_rate", 0) or 0)
                queued = int(getattr(p, "_queued_samples", 0) or 0)
                if sr > 0:
                    info["audio_latency_ms"] = (queued / sr) * 1000.0
        except Exception:
            pass
        return info


__all__ = [
    "DeviceHealthChecker",
    "PROBE_SAMPLES",
]
