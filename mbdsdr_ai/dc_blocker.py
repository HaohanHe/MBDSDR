# SPDX-License-Identifier: MIT
"""
First-order high-pass DC blocker.

Removes the DC component of a real or complex signal with a one-pole
high-pass difference equation

    y[n] = x[n] - x[n-1] + R * y[n-1]

which realises the transfer function ``H(z) = (1 - z^-1) / (1 - R z^-1)``.
As R approaches 1 the corner frequency drops (less in-band attenuation) at
the cost of slower tracking of slow DC drift.  For complex I/Q the in-phase
and quadrature channels are filtered independently, so there is no crosstalk.
Filter state is carried across calls, making block-wise processing continuous
and O(1) per sample.
"""

from __future__ import annotations

import numpy as np

__all__ = ["DCBlocker"]


def _first_order_iir(x: np.ndarray, r: float,
                     x_prev: float, y_prev: float):
    """一段一阶 IIR：y[n] = x[n] - x[n-1] + R*y[n-1]。

    用 scipy.signal.lfilter（b=[1,-1], a=[1,-r]）带初始状态；
    SciPy 缺失时退化为 NumPy 递推。返回 (y, x_prev_new, y_prev_new)。
    """
    n = x.shape[0]
    try:
        from scipy.signal import lfilter
        # zi[0] = -x_prev + r*y_prev：使第一段输出等价于连续流（无突变）
        zi = np.array([-x_prev + r * y_prev], dtype=np.float64)
        y, _zf = lfilter([1.0, -1.0], [1.0, -r], x, zi=zi)
        return y, float(x[-1]) if n else x_prev, float(y[-1]) if n else y_prev
    except ImportError:
        pass

    diff = np.empty(n, dtype=np.float64)
    diff[0] = x[0] - x_prev
    if n > 1:
        diff[1:] = x[1:] - x[:-1]
    y = np.empty(n, dtype=np.float64)
    yp = y_prev
    for k in range(n):
        yp = diff[k] + r * yp
        y[k] = yp
    return y, float(x[-1]) if n else x_prev, float(yp)


class DCBlocker:
    """First-order IIR DC blocker: ``y[n] = x[n] - x[n-1] + R*y[n-1]``.

    Parameters:
        r: pole location R in [0, 1), default 0.999.
           0.995 tracks faster; 0.9995 distorts the passband less.
    """

    def __init__(self, r: float = 0.999) -> None:
        if not 0.0 <= r < 1.0:
            raise ValueError(f"DC blocker pole r must be in [0, 1), got {r}")
        self.r = float(r)
        # I/Q 两路独立状态（复数信号必须分别维持，不能共用，否则 I/Q 串扰）
        self._i = (0.0, 0.0)   # (x_prev, y_prev) on I
        self._q = (0.0, 0.0)   # (x_prev, y_prev) on Q
        self._real = (0.0, 0.0)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Process a block; complex input is filtered independently per I/Q."""
        x = np.asarray(x)
        if x.size == 0:
            return x
        if np.iscomplexobj(x):
            yi, xi, yi_p = _first_order_iir(
                x.real.astype(np.float64), self.r, self._i[0], self._i[1])
            yq, xq, yq_p = _first_order_iir(
                x.imag.astype(np.float64), self.r, self._q[0], self._q[1])
            self._i = (xi, yi_p)
            self._q = (xq, yq_p)
            return (yi + 1j * yq).astype(x.dtype)
        yr, xr, yr_p = _first_order_iir(
            x.astype(np.float64), self.r, self._real[0], self._real[1])
        self._real = (xr, yr_p)
        return yr.astype(x.dtype)

    def reset(self) -> None:
        """清空内部状态（换源/换频时调用）。"""
        self._i = (0.0, 0.0)
        self._q = (0.0, 0.0)
        self._real = (0.0, 0.0)
