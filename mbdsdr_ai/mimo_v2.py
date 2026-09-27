"""
mbdsdr_ai/mimo_v2.py — 多设备/MIMO 同步采集 v2
================================================

对照 SDRangel 多设备同步机制：
  - ``sdrbase/device/deviceset.cpp``  DeviceSet —— 多台设备共享一个中心频率 / 采样率
  - ``sdrbase/mainwindow.cpp``        setFrequencyOnDevices —— 一键同步调谐所有设备
  - USRP / SoapySDR 驱动层            ``get_hardware_time()`` —— 10MHz/PPS 同步下的硬件时间戳对齐

与旧版 :mod:`mbdsdr_ai.mimo` 的区别：
  * 旧版按 ``device_id: str`` 字典管理；这里按**整数索引**槽位管理（RTL-SDR #0, #1, ...），
    更贴合 SDRangel DeviceSet 的下标语义。
  * 新增 :class:`PhaseCoherence`：互相关测时差 + 载波相位差（SDRangel 只做同步采集，
    不做两两相位测量——这是我们的增强）。

红线（与项目 device_manager.py 一致）：
  * **无设备时所有接口可用但返回空 / 空结果，绝不合成假 IQ。**
  * 单台设备读失败（拔出）时不炸整个 MIMO，把它从槽位摘掉、其余继续。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

__all__ = ["DeviceSlot", "MultiDeviceManager", "PhaseCoherence"]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 槽位：一台 SDRBackend 的运行态
# --------------------------------------------------------------------------- #
@dataclass
class DeviceSlot:
    """一个设备槽位。``backend=None`` 表示空槽 / 已摘除。

    backend 是鸭型对象，只需暴露：
      - ``set_frequency(hz: float)``
      - ``set_sample_rate(rate_hz: float)``
      - ``set_gain(db: float)``          （可选）
      - ``read_samples(n: int) -> np.ndarray(complex)``
      - ``start() / stop()``             （可选）
      - ``get_hw_time() -> float``       （可选，硬件时间戳；缺省用软件时钟）
    """

    index: int
    backend: Any
    started: bool = False
    # 校准后：相对主设备的时间偏移(s) 与载波相位偏移(rad)
    time_offset_s: float = 0.0
    phase_offset_rad: float = 0.0

    @property
    def alive(self) -> bool:
        return self.backend is not None


# --------------------------------------------------------------------------- #
# MultiDeviceManager
# --------------------------------------------------------------------------- #
class MultiDeviceManager:
    """管理多个 SDRBackend 实例，支持同步调谐 / 同步启停 / 时间戳对齐读取。

    无设备时：``list_devices()==[]``、``read_synchronized(n)==[]``，
    ``set_frequency_all`` 等为空操作，全部不抛错、不造假。
    """

    def __init__(self) -> None:
        self._slots: List[DeviceSlot] = []
        self._lock = threading.Lock()

    # -- 增删查 ---------------------------------------------------------- #
    def add_device(self, backend: Any) -> int:
        """登记一台后端，返回它的槽位索引。"""
        with self._lock:
            idx = len(self._slots)
            self._slots.append(DeviceSlot(index=idx, backend=backend))
            logger.info("MIMO: added device #%d", idx)
            return idx

    def remove_device(self, index: int) -> bool:
        """摘除一个槽位。越界返回 False。"""
        with self._lock:
            if not (0 <= index < len(self._slots)):
                return False
            self._slots[index].backend = None
            # 不压缩列表，保持索引稳定；list_devices 只报 alive 的槽位
            return True

    def list_devices(self) -> List[Dict[str, Any]]:
        """当前存活设备快照。无设备 -> []。"""
        with self._lock:
            return [
                {
                    "index": s.index,
                    "started": s.started,
                    "has_backend": s.alive,
                }
                for s in self._slots
                if s.alive
            ]

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots if s.alive)

    # -- 同步调谐 -------------------------------------------------------- #
    def _alive_slots(self) -> List[DeviceSlot]:
        with self._lock:
            return [s for s in self._slots if s.alive]

    @staticmethod
    def _safe_call(slot: DeviceSlot, method: str, *args) -> bool:
        """调 backend.<method>(*args)；失败时摘设备并返回 False。"""
        fn = getattr(slot.backend, method, None)
        if fn is None:
            return False
        try:
            fn(*args)
            return True
        except Exception as e:  # noqa: BLE001 —— 单设备失败不拖垮全局
            logger.warning("device #%s.%s failed: %s", slot.index, method, e)
            slot.backend = None
            return False

    def set_frequency_all(self, hz: float) -> List[int]:
        """把所有设备中心频率设成同一 Hz。返回成功的槽位索引列表（无设备 -> []）。"""
        hz = float(hz)
        ok: List[int] = []
        for s in self._alive_slots():
            if self._safe_call(s, "set_frequency", hz):
                ok.append(s.index)
        return ok

    def set_sample_rate_all(self, rate_hz: float) -> List[int]:
        rate_hz = float(rate_hz)
        ok: List[int] = []
        for s in self._alive_slots():
            if self._safe_call(s, "set_sample_rate", rate_hz):
                ok.append(s.index)
        return ok

    # -- 同步启停 -------------------------------------------------------- #
    def start_all(self) -> List[int]:
        ok: List[int] = []
        for s in self._alive_slots():
            if getattr(s.backend, "start", None) is not None:
                if self._safe_call(s, "start"):
                    s.started = True
                    ok.append(s.index)
            else:
                # 无 start() 方法的后端（如 OpenDevice）视为读即采
                s.started = True
                ok.append(s.index)
        return ok

    def stop_all(self) -> List[int]:
        ok: List[int] = []
        for s in self._alive_slots():
            if getattr(s.backend, "stop", None) is not None:
                if self._safe_call(s, "stop"):
                    s.started = False
                    ok.append(s.index)
            else:
                s.started = False
                ok.append(s.index)
        return ok

    # -- 时间戳对齐读取 -------------------------------------------------- #
    def read_synchronized(self, n: int) -> List[Tuple[np.ndarray, float]]:
        """从所有存活设备各读 n 个复采样，返回 ``[(iq, timestamp_s), ...]``。

        timestamp 优先取硬件时间戳（``backend.get_hw_time()``，10MHz/PPS 同步），
        否则退化为软件 ``time.time()``。**无设备 -> []（不造假）。**
        某台设备读失败则摘除该设备，其余继续。
        """
        n = int(n)
        out: List[Tuple[np.ndarray, float]] = []
        for s in self._alive_slots():
            if not s.alive:
                continue
            try:
                iq = np.asarray(s.backend.read_samples(n), dtype=np.complex128)
            except Exception as e:  # noqa: BLE001
                logger.warning("device #%s read failed, dropping: %s", s.index, e)
                s.backend = None
                continue
            hw = getattr(s.backend, "get_hw_time", None)
            try:
                ts = float(hw()) if callable(hw) else time.time()
            except Exception:  # noqa: BLE001
                ts = time.time()
            ts += s.time_offset_s
            out.append((iq, ts))
        return out


# --------------------------------------------------------------------------- #
# PhaseCoherence —— 多设备相位差测量 / 校准（SDRangel 没有的增强）
# --------------------------------------------------------------------------- #
class PhaseCoherence:
    """两两设备间载波相位差测量（互相关法）。

    步骤（对照阵列测向里常用的互相关相位估计）：
      1. 去均值；
      2. 互相关找到时差 lag；
      3. 按 lag 对齐 b；
      4. ``angle(mean(b * conj(a)))`` 得 b 相对 a 的载波相位差（rad，[-pi, pi]）。

    无信号 / 无设备时不造假：:meth:`phase_calibrate` 不给数据就返回 ``{}``。
    """

    def __init__(self) -> None:
        # (slot_a, slot_b) -> 校准相位偏移(rad)
        self.calibration: Dict[Tuple[int, int], float] = {}

    @staticmethod
    def measure_phase_diff(iq1: np.ndarray, iq2: np.ndarray) -> float:
        """测 iq2 相对 iq1 的载波相位差（rad）。

        对 ``iq2 = iq1 * exp(j*phi)`` 应返回 ≈ phi（容差见测试）。
        """
        a = np.asarray(iq1, dtype=np.complex128)
        b = np.asarray(iq2, dtype=np.complex128)
        n = min(len(a), len(b))
        if n < 2:
            raise ValueError("need at least 2 samples per signal")
        a = a[:n] - np.mean(a[:n])
        b = b[:n] - np.mean(b[:n])

        # 互相关找时差 lag（np.correlate 等价于 fftconvolve 整模）
        corr = np.correlate(a, b, mode="full")
        lag = int(np.argmax(np.abs(corr))) - (n - 1)
        b_aligned = np.roll(b, -lag)

        # b 相对 a 的相位：b = a * exp(j*phi) -> mean(b*conj(a)) ∝ exp(j*phi)
        phase = np.angle(np.mean(b_aligned * np.conj(a)))
        return float(phase)

    def phase_calibrate(
        self,
        iq_ref: Optional[np.ndarray] = None,
        iq_other: Optional[np.ndarray] = None,
        pair_id: Tuple[int, int] = (0, 1),
    ) -> Dict[Tuple[int, int], float]:
        """用已知信号测一次相位差并记入校准表。

        不给信号 -> 返回 ``{}``（无设备 / 无参考信号时不造假）。
        """
        if iq_ref is None or iq_other is None:
            return {}
        phi = self.measure_phase_diff(iq_ref, iq_other)
        self.calibration[tuple(pair_id)] = phi
        return {tuple(pair_id): phi}

    def apply_calibration(
        self, iq: np.ndarray, pair_id: Tuple[int, int]
    ) -> np.ndarray:
        """按校准表把 iq 旋转掉已知相位偏移；没校准过就原样返回。"""
        phi = self.calibration.get(tuple(pair_id))
        if phi is None:
            return np.asarray(iq)
        return np.asarray(iq, dtype=np.complex128) * np.exp(-1j * phi)
