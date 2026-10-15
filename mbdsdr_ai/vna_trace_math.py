# SPDX-License-Identifier: MIT
"""迹线数学（NanoVNA-App 图表数据层，第六批）。

- group_delay：先 unwrap 相位，再后向差分 τ = −Δφ/(360·Δf)，端点复制
  （上游 Graphs.cpp 9928-9939 口径）。输入须为等距频率轴，否则显式
  ValueError（上游只支持线性轴，本实现同口径并诚实校验）。
- normalize_trace：归一化记忆通道——抓参考迹线后逐点 level −= ref。
  domain="db" 时按 dB 相减（等价复数相除的幅度域口径，无相位信息场景）；
  domain="complex" 时逐点复数相除（含相位，需要参考的完整复数迹线）。
- gjb_conductance：并联等效的 GJB 图数据（1/Rp、−1/Xp，国军标习惯）。
- HAM_BANDS / find_band：常见业余频段表（IARU 公共数据）；带外返回
  None（诚实空态，不猜最近频段）。

诚实边界：group_delay 的端点复制意味着首尾点群延迟等于次邻点——端点
精度低一档，docstring 登记；normalize 的 db 域口径不携带相位修正能力。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

__all__ = [
    "group_delay",
    "normalize_trace",
    "gjb_conductance",
    "HAM_BANDS",
    "find_band",
]


def group_delay(freqs_hz, phase_deg) -> np.ndarray:
    """群延迟（秒）：unwrap 相位后向差分口径（端点复制）。"""
    f = np.asarray(freqs_hz, dtype=np.float64)
    ph = np.unwrap(np.radians(np.asarray(phase_deg, dtype=np.float64)))
    if f.ndim != 1 or len(f) < 2:
        raise ValueError("频率轴须为长度 >=2 的一维数组")
    df = np.diff(f)
    if np.any(df <= 0):
        raise ValueError("频率轴必须严格递增（上游只支持线性等距轴）")
    dphi = np.diff(ph)
    tau = -dphi / (2.0 * np.pi * df)
    return np.concatenate(([tau[0]], tau, [tau[-1]]))


def normalize_trace(level, reference, domain: str = "complex"):
    """归一化：level −= ref。domain='complex' 逐点复数相除（含相位）；
    domain='db' 按 dB 逐点相减（幅度域口径）。未知 domain 显式拒绝。"""
    if domain == "complex":
        lvl = np.asarray(level, dtype=np.complex128)
        ref = np.asarray(reference, dtype=np.complex128)
        if np.any(ref == 0):
            raise ValueError("归一化参考迹线含零值（复数相除无定义）")
        return lvl / ref
    if domain == "db":
        return np.asarray(level, dtype=np.float64) - np.asarray(
            reference, dtype=np.float64)
    raise ValueError(f"domain 须为 'complex'/'db'，收到 {domain!r}")


def gjb_conductance(rp, xp) -> Tuple[np.ndarray, np.ndarray]:
    """GJB 图数据：(1/Rp, −1/Xp)。零 Rp 显式 ValueError。"""
    rp = np.asarray(rp, dtype=np.float64)
    xp = np.asarray(xp, dtype=np.float64)
    if np.any(rp == 0):
        raise ValueError("Rp 含零值（1/Rp 无定义）")
    return 1.0 / rp, -1.0 / xp


#: 常见业余频段（名称, 下限 Hz, 上限 Hz；IARU 公共数据，供频带背景着色）。
HAM_BANDS: List[Tuple[str, float, float]] = [
    ("2200m", 135700.0, 137800.0),
    ("630m", 472000.0, 479000.0),
    ("160m", 1800000.0, 2000000.0),
    ("80m", 3500000.0, 4000000.0),
    ("60m", 5332000.0, 5405000.0),
    ("40m", 7000000.0, 7300000.0),
    ("30m", 10100000.0, 10150000.0),
    ("20m", 14000000.0, 14350000.0),
    ("17m", 18068000.0, 18168000.0),
    ("15m", 21000000.0, 21450000.0),
    ("12m", 24890000.0, 24990000.0),
    ("10m", 28000000.0, 29700000.0),
    ("6m", 50000000.0, 54000000.0),
    ("4m", 70000000.0, 70500000.0),
    ("2m", 144000000.0, 148000000.0),
    ("70cm", 420000000.0, 450000000.0),
    ("23cm", 1240000000.0, 1300000000.0),
    ("13cm", 2320000000.0, 2450000000.0),
    ("9cm", 3300000000.0, 3500000000.0),
    ("5cm", 5650000000.0, 5925000000.0),
]


def find_band(freq_hz: float) -> Optional[Tuple[str, float, float]]:
    """查频点所属业余频段；带外返回 None（诚实空态）。"""
    for name, lo, hi in HAM_BANDS:
        if lo <= freq_hz <= hi:
            return (name, lo, hi)
    return None
