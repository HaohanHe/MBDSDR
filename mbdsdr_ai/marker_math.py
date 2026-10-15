# SPDX-License-Identifier: MIT
"""Marker 数据层：吸附/检索/delta 读数（NanoVNA-App 第九批）。

上游口径（Unit1/GraphUnit）：marker 按频点归属迹线，鼠标吸附到最近
采样点（snapToNearestPoint），delta 型 marker 显示与参考 marker 的
频率/幅值差。

本实现（纯数据层，无 UI）：
- snap_to_nearest(freqs, f_target) -> (index, freq)：非降频轴二分查
  找最近采样；空轴/非有限目标显式拒绝。
- marker_value(freqs, trace, index) -> (f, v)：该 marker 的坐标读数。
- delta_reading(freqs, trace, a_idx, b_idx) -> dict：两 marker 间的
  Δf（Hz）、Δv（线性值差）、以及 dB 域差 20·log10(|vb|/|va|)（复数
  迹线按幅度域，如实标注口径）。
- peak_search(freqs, trace, n=1) -> list：局部极大值索引（dB 幅度域），
  供"marker 设为峰值"操作；平坦区不产出假峰（严格大于左右邻）。
"""
from __future__ import annotations

from bisect import bisect_left
from typing import Any, Dict, List, Tuple

import numpy as np

__all__ = [
    "snap_to_nearest",
    "marker_value",
    "delta_reading",
    "peak_search",
]


def snap_to_nearest(freqs, f_target: float) -> Tuple[int, float]:
    """最近采样吸附。freqs 须非降序有限值；f_target 须有限。"""
    f = np.asarray(freqs, dtype=np.float64)
    if len(f) == 0:
        raise ValueError("空频率轴不可吸附")
    if np.any(~np.isfinite(f)) or np.any(np.diff(f) < 0):
        raise ValueError("频率轴须为有限非降序列")
    if not np.isfinite(f_target):
        raise ValueError(f"目标频率非有限：{f_target}")
    k = bisect_left(f.tolist(), f_target)
    if k == 0:
        best = 0
    elif k >= len(f):
        best = len(f) - 1
    else:
        best = k - 1 if (f_target - f[k - 1]) <= (f[k] - f_target) else k
    return best, float(f[best])


def marker_value(freqs, trace, index: int) -> Tuple[float, Any]:
    """marker 坐标读数；(freq, trace[index])，越界显式拒绝。"""
    f = np.asarray(freqs)
    t = np.asarray(trace)
    if not (0 <= index < len(f)) or len(t) != len(f):
        raise IndexError(f"marker 索引 {index} 超出范围（{len(f)} 点）")
    return float(f[index]), t[index]


def delta_reading(freqs, trace, a_idx: int, b_idx: int) -> Dict[str, float]:
    """两 marker 间读数差：Δf、Δv（线性差）、ΔdB（幅度比，复数迹线按
    |v| 口径，docstring 登记）。va=0 时 dB 差无定义，显式给 None。"""
    fa, va = marker_value(freqs, trace, a_idx)
    fb, vb = marker_value(freqs, trace, b_idx)
    out: Dict[str, Any] = {"df_hz": fb - fa, "dv": vb - va}
    ma, mb = abs(va), abs(vb)
    out["db"] = (20.0 * np.log10(mb / ma)
                 if ma > 0 else None)
    return out


def peak_search(freqs, trace, n: int = 1) -> List[int]:
    """dB 幅度域局部极大值索引（严格大于左右邻，平坦区不出假峰）；
    按幅度降序返回前 n 个。n<1 或空迹线显式拒绝。"""
    if n < 1:
        raise ValueError(f"n 须 >=1，收到 {n}")
    t = np.asarray(trace)
    if len(t) < 3:
        raise ValueError("迹线少于 3 点无法判峰")
    mag = 20.0 * np.log10(np.maximum(np.abs(t), 1e-30))
    cand = [i for i in range(1, len(t) - 1)
            if mag[i] > mag[i - 1] and mag[i] > mag[i + 1]]
    cand.sort(key=lambda i: -mag[i])
    return cand[:n]
