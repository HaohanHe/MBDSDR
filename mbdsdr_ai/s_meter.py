# SPDX-License-Identifier: MIT
"""
S 表 / 信号强度计（S-meter）
===============================

对照 GQRX ``src/dsp/rx_meter_c.cc`` + ``src/receivers/nbrx.cpp:131-133``：

    meter = make_rx_meter_c(quad_rate);
    connect(filter, 0, meter, 0);
    float nbrx::get_signal_level() { return meter->get_level_db(); }

GQRX 本身只给 dBFS；S-unit 映射是业余无线电惯例：
- S9  = -73 dBm（HF 标准）
- 每 S 单位 6 dB
- S0  = -73 - 9*6 = -127 dBm
- S9 以上：每 10 dB 一档（S9+10 = -63 dBm，S9+20 = -53 dBm ...）

本模块：
- 输入复基带块，算 RMS 功率 → dBm（需要一个满量程参考电平 ``ref_dbfs``，
  即 0 dBFS 对应多少 dBm；默认 -10 dBFS = 0 dBm 之类，由上层校准）。
- :meth:`s_unit` 把 dBm 映射成 S 单位（含 S9+X 后缀）。
- 峰值保持（对照 SDR++ waterfall 峰值衰减思路）。
- AI 增强：:meth:`push` 同时做异常检测——信号突然增强/减弱超阈值则告警。

红线：本模块只测量喂进来的块；不接真实硬件时读数是合成信号的功率，
不当作真实接收结果上报。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np


# S9 = -73 dBm，每 S 单位 6 dB
_S9_DBM: float = -73.0
_DB_PER_S: float = 6.0
_S0_DBM: float = _S9_DBM - 9.0 * _DB_PER_S   # = -127 dBm


def rms_dbm(block: np.ndarray, ref_dbfs_per_dbm: float = -10.0) -> float:
    """把一块复/实信号算成 dBm。

    ``ref_dbfs_per_dbm``：0 dBFS 对应的 dBm（即 +dBFS_ref dBFS = 0 dBm）。
    例如 ref = -10 表示满量程 0 dBFS = +10 dBm，于是
    power_dbm = power_dbfs - ref_dbfs_per_dbm。
    """
    block = np.asarray(block)
    if block.size == 0:
        return -200.0
    if np.iscomplexobj(block):
        b = block.astype(np.complex128).ravel()
        p = float(np.mean(b.real * b.real + b.imag * b.imag))
    else:
        b = block.astype(np.float64).ravel()
        p = float(np.mean(b * b))
    if p <= 0:
        return -200.0
    dbfs = 10.0 * math.log10(p)
    return dbfs - ref_dbfs_per_dbm


def dbm_to_s_unit(dbm: float) -> Tuple[float, str]:
    """把 dBm 映射成 (数值 S, 显示字符串)。

    - < S0 → (0.0, "S0")
    - S0..S9 → (0..9, "S0".."S9")
    - > S9 → (9 + (dbm-S9)/10, "S9+X") 其中 X 是超过 S9 的 dB 数
    """
    if dbm <= _S0_DBM:
        return 0.0, "S0"
    if dbm < _S9_DBM:
        s = (dbm - _S0_DBM) / _DB_PER_S     # 0..9
        return s, f"S{int(round(s))}"
    # >= S9
    above = dbm - _S9_DBM                    # 0, 10, 20 ...
    s_num = 9.0 + above / 10.0
    if above < 0.05:
        return s_num, "S9"
    return s_num, f"S9+{int(round(above))}"


@dataclass
class SMeterState:
    """S 表内部状态。"""

    smoothed_dbm: float = -200.0
    peak_dbm: float = -200.0
    peak_hold_decay_db_per_push: float = 1.0
    # 异常检测
    history: list = field(default_factory=list)
    alarm: str = ""               # "" | "rise" | "drop"


class SMeter:
    """实时 RMS/dBm/S-unit + 峰值保持 + AI 异常检测。

    Parameters
    ----------
    ref_dbfs_per_dbm : float
        0 dBFS 对应的 dBm 偏移（见 :func:`rms_dbm`）。
    peak_decay_db : float
        每次 push 峰值衰减 dB。
    anomaly_threshold_db : float
        相对历史均值跳变多少 dB 触发告警。
    anomaly_window : int
        历史滑动窗口长度。
    """

    def __init__(self,
                 ref_dbfs_per_dbm: float = -10.0,
                 peak_decay_db: float = 1.0,
                 anomaly_threshold_db: float = 12.0,
                 anomaly_window: int = 16) -> None:
        self.ref = float(ref_dbfs_per_dbm)
        self.st = SMeterState()
        self.st.peak_hold_decay_db_per_push = float(peak_decay_db)
        self.anomaly_thr = float(anomaly_threshold_db)
        self.anomaly_window = int(anomaly_window)

    def reset(self) -> None:
        self.st = SMeterState()

    # ------------------------------------------------------------------
    def push(self, block: np.ndarray) -> float:
        """喂一块复/实信号，返回当前 dBm。

        同时更新平滑值、峰值保持、异常检测。
        """
        dbm = rms_dbm(block, self.ref)

        # 一阶平滑（跟随快）
        alpha = 0.3
        if self.st.smoothed_dbm <= -199.0:
            self.st.smoothed_dbm = dbm
        else:
            self.st.smoothed_dbm = alpha * dbm + (1.0 - alpha) * self.st.smoothed_dbm

        # 峰值保持（带衰减，对照 SDR++ fftHoldSpeed）
        if self.st.peak_dbm <= -199.0:
            self.st.peak_dbm = dbm
        else:
            self.st.peak_dbm = max(dbm,
                                   self.st.peak_dbm - self.st.peak_hold_decay_db_per_push)

        # 异常检测：与滑动均值比
        self.st.alarm = ""
        self.st.history.append(dbm)
        if len(self.st.history) > self.anomaly_window:
            self.st.history = self.st.history[-self.anomaly_window:]
        if len(self.st.history) >= 4:
            mean = float(np.mean(self.st.history[:-1]))
            if dbm - mean > self.anomaly_thr:
                self.st.alarm = "rise"
            elif mean - dbm > self.anomaly_thr:
                self.st.alarm = "drop"

        return self.st.smoothed_dbm

    # ------------------------------------------------------------------
    @property
    def dbm(self) -> float:
        return self.st.smoothed_dbm

    @property
    def peak(self) -> float:
        return self.st.peak_dbm

    @property
    def s_unit(self) -> Tuple[float, str]:
        return dbm_to_s_unit(self.st.smoothed_dbm)

    @property
    def alarm(self) -> str:
        return self.st.alarm
