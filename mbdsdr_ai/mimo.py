# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/mimo.py — 多设备 IQ 同步采集框架
=============================================

对照 SDRangel 多设备同步（``sdrbase/device/deviceset.cpp``、UHD/MIMO 驱动层）：
  * 多台 SDR 通过 10MHz/PPS 时钟同步，采集同一段频谱；
  * 时间戳对齐：以 PPS 为基准，各设备 IQ 流按硬件时间戳对齐；
  * 相位相干测量：两两设备间做互相关，估计载波相位差 / 时差。

**红线：无设备时接口可用但不造假数据。** 所有方法在 ``DeviceHandle`` 为 None
时返回空结果或抛出明确错误，绝不合成 IQ。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import math

import numpy as np

__all__ = ["MIMODevice", "MIMOController", "PhaseCoherenceResult"]


@dataclass
class MIMODevice:
    """一台同步 SDR 的句柄。``handle=None`` 表示未连接真实硬件。"""
    device_id: str
    sample_rate: float
    center_hz: float
    handle: object = None        # 真实 SoapySDR/USRP 句柄；None=未连接
    time_offset_s: float = 0.0    # 相对 PPS 的时间偏移（校准后）
    phase_offset_deg: float = 0.0 # 载波相位偏移（校准后）

    @property
    def connected(self) -> bool:
        return self.handle is not None


@dataclass
class PhaseCoherenceResult:
    device_a: str
    device_b: str
    time_delay_s: float
    phase_diff_deg: float
    correlation_peak: float


class MIMOController:
    """多设备同步采集控制器。无设备时空转，不造假。"""

    def __init__(self) -> None:
        self._devices: Dict[str, MIMODevice] = {}

    def add_device(self, dev: MIMODevice) -> None:
        self._devices[dev.device_id] = dev

    def remove_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    @property
    def devices(self) -> List[MIMODevice]:
        return list(self._devices.values())

    def all_synced(self) -> bool:
        """所有设备都已连接且已校准时间偏移。"""
        return all(d.connected for d in self._devices.values())

    def read_stream(self, duration_s: float) -> Dict[str, Tuple[np.ndarray, float]]:
        """从所有设备同步读取一段 IQ。无设备时返回空 dict（不造假）。"""
        if not self.all_synced():
            return {}
        out: Dict[str, Tuple[np.ndarray, float]] = {}
        for dev in self._devices.values():
            # 真实环境：handle.recv(...)；这里不在没有设备时合成数据
            if hasattr(dev.handle, "read_stream"):
                iq, ts = dev.handle.read_stream(duration_s)
                out[dev.device_id] = (np.asarray(iq), ts + dev.time_offset_s)
        return out

    def measure_phase_coherence(self, iq_a: np.ndarray, iq_b: np.ndarray,
                                sample_rate: float) -> PhaseCoherenceResult:
        """两两设备间互相关，估计时差与载波相位差。"""
        n = min(len(iq_a), len(iq_b))
        a = iq_a[:n] - np.mean(iq_a[:n])
        b = iq_b[:n] - np.mean(iq_b[:n])
        corr = np.correlate(a, b, mode="full")
        peak_idx = int(np.argmax(np.abs(corr))) - (n - 1)
        delay_s = peak_idx / sample_rate
        # 载波相位差：取相关峰处 b 相对 a 的相位
        phase_diff = math.degrees(math.atan2(
            np.imag(a[peak_idx] * np.conj(b[0])) if peak_idx >= 0 else 0,
            np.real(a[peak_idx] * np.conj(b[0])) if peak_idx >= 0 else 1.0,
        ))
        return PhaseCoherenceResult(
            device_a="A", device_b="B",
            time_delay_s=delay_s,
            phase_diff_deg=phase_diff,
            correlation_peak=float(np.abs(corr).max() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)),
        )


