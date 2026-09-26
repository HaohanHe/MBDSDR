"""
MBDSDR AI 内核 - 一阶 IIR DC 阻断器（DC blocker）
==================================================

两种等价形式，对照上游真实实现：

1. SDR++ 泄漏积分器形式
   repos/sdrpp/core/src/dsp/correction/dc_blocker.h:54-60
       out[i] = in[i] - offset;
       offset += out[i] * _rate;
   对复数 I/Q 两路独立（offset 是 complex_t，逐分量运算，无串扰）。

2. 经典一阶差分 IIR（本模块默认形式，任务规格指定）
       y[n] = x[n] - x[n-1] + R * y[n-1]
   这是上一形式在小步长下的等价闭环：令 a = 1/(1+rate)，SDR++ 形式的
   传递函数 H(z) = (1/(1+rate)) * (1 - z^-1)/(1 - a*z^-1)。
   忽略 1/(1+rate)≈1 的标量增益，极点即 R = a = 1/(1+rate)。

- R 越接近 1，高通截止频率越低，对带内信号损伤越小，但跟踪直流慢漂越慢。
- 对复数 IQ：I 路与 Q 路各自维持独立状态（无串扰），对照 SDR++
  dc_blocker.h:20-22（offset 为 complex_t，逐分量加减）。
- 流式：process() 跨块连续状态，O(1)，无需整段已知。

License: GPL-3.0-or-later
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
    """一阶 IIR 直流阻断器：y[n] = x[n] - x[n-1] + R*y[n-1]。

    对照 sdrpp/core/src/dsp/correction/dc_blocker.h:54-60（等价形式）。

    Parameters:
        r: 极点位置 R ∈ [0,1)，默认 0.999（任务规格）。
           0.995 跟踪更快，0.9995 对信号损伤更小。
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
        """处理一段样本；复数输入时 I/Q 两路独立各做一次 IIR。

        对照 dc_blocker.h:55-58：对 complex_t 逐分量处理（I、Q 无串扰）。
        """
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
