# SPDX-License-Identifier: MIT
"""进程内「最新一帧 IQ」总线：把频谱面板收到的基带数据广播给需要它的面板。

============================================================================
为什么需要它
============================================================================
主窗口每收到一帧 IQ 就会调 ``SpectrumPanel.set_iq_data(iq, sr)``（无条件，
不受页签可见性影响）。其他面板想用同一块 IQ（中栏 ADS-B 地图、卫星云图、
新时空识别）时，有两条路：

1. 让主窗口逐个面板 ``feed_iq`` —— 每加一个消费者就要改一次主窗口，而主窗口
   是最拥挤的接线点（一万行、多会话并行修改），极易冲突；
2. 发到一个有界总线，谁需要谁自己取 —— 主窗口只保留一行 ``set_iq_data``。

这里选 2。总线**只保存最新一帧的引用**，不做队列、不 copy、不落盘：

* 消费者慢了一拍 → 拿到的仍是"此刻最新的 IQ"，不会看到陈旧积压；
* 没有消费者 → 内存里最多多一个数组引用，下一帧就被覆盖；
* 时间是单调序号（``seq``），消费者据此判「这一帧我处理过没有」。

红线：本模块不生成、不修改、不外推任何 IQ 数据；没有发布者时
``latest()`` 返回 ``None``，消费者必须自己处理空态，绝不合成波形。
"""
from __future__ import annotations

import threading
import time
from typing import Any, NamedTuple, Optional


class IqFrame(NamedTuple):
    """一帧 IQ 快照的描述（数组本身按引用传递，不做拷贝）。"""

    iq: Any                 # np.ndarray[complex]，调用方保证只读
    sample_rate: float      # Hz
    center_hz: float        # Hz（基带中心，不是 0 中频）
    seq: int                # 单调递增序号，从 1 开始
    timestamp: float        # time.monotonic()


class IqBus:
    """单槽广播总线（线程安全，只保留最新一帧）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[IqFrame] = None
        self._seq = 0

    # ------------------------------------------------------------------
    def publish(self, iq: Any, sample_rate: float, center_hz: float) -> None:
        """发布一帧 IQ。空数组/非有限采样率直接忽略（不污染总线）。"""
        if iq is None:
            return
        try:
            n = len(iq)
        except TypeError:
            return
        if n <= 0:
            return
        try:
            sr = float(sample_rate)
            cf = float(center_hz)
        except (TypeError, ValueError):
            return
        if not (sr > 0) or sr != sr:      # NaN 也挡掉
            return
        with self._lock:
            self._seq += 1
            self._frame = IqFrame(iq, sr, cf, self._seq, time.monotonic())

    def latest(self) -> Optional[IqFrame]:
        """最新一帧；从未发布过返回 None。"""
        with self._lock:
            return self._frame

    def latest_since(self, seq: int) -> Optional[IqFrame]:
        """只在序号比 ``seq`` 新时返回（消费者用它判"这帧处理过没有"）。"""
        with self._lock:
            frame = self._frame
            if frame is None or frame.seq <= int(seq):
                return None
            return frame

    def seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        """丢弃当前帧（换设备/换频段时避免消费者用到上一台设备的 IQ）。"""
        with self._lock:
            self._frame = None
            self._seq += 1


_BUS = IqBus()


def get_iq_bus() -> IqBus:
    """进程级单例（与 tx_guard / time_engine 同一约定）。"""
    return _BUS


def reset_iq_bus() -> IqBus:
    """测试用：换一个干净实例。"""
    global _BUS
    _BUS = IqBus()
    return _BUS
