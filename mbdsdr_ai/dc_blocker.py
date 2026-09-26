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
        # I/Q 两路独立状态（复数信号必须分别维持，不能共用）
        self._x_prev = 0.0
        self._y_prev = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段样本；复数输入时 I/Q 两路独立各做一次 IIR。

        对照 dc_blocker.h:55-58：对 complex_t 逐分量处理（I、Q 无串扰）。
        """
        x = np.asarray(x)
        if x.size == 0:
            return x
        if np.iscomplexobj(x):
            i_out = self._process_real(x.real.astype(np.float64))
            # 复用同一实例时 I/Q 不能共用状态——分别用临时实例
            q_blocker = _PairBlocker(self.r)
            q_blocker._x_prev = self._q_x_prev
            q_blocker._y_prev = self._q_y_prev
            q_out = q_blocker._process_real(x.imag.astype(np.float64))
            self._q_x_prev = q_blocker._x_prev
            self._q_y_prev = q_blocker._y_prev
            self._x_prev = i_out._x_prev if hasattr(i_out, "_x_prev") else self._x_prev
            return self._combine(i_out, q_out, x.dtype)
        return self._process_real(x.astype(np.float64))

    # 内部：I 路状态
    _q_x_prev: float = 0.0
    _q_y_prev: float = 0.0

    def _combine(self, i_out, q_out, dtype):
        return (i_out + 1j * q_out).astype(np.complex64 if dtype == np.complex64
                                            else np.complex128)

    def _process_real(self, x: np.ndarray) -> np.ndarray:
        """向量化一阶 IIR：y[n] = x[n] - x[n-1] + R*y[n-1]。

        用 scipy.signal.lfilter 带初始状态（b=[1,-1], a=[1,-r]）；
        SciPy 缺失时退化为 NumPy 递推。结果严格等价。
        """
        r = self.r
        x_prev = self._x_prev
        y_prev = self._y_prev
        n = x.shape[0]

        try:
            from scipy.signal import lfilter
            # zi[0] = -x_prev + r*y_prev：使第一段输出等价于连续流（无突变）
            zi = np.array([-x_prev + r * y_prev], dtype=np.float64)
            y, zf = lfilter([1.0, -1.0], [1.0, -r], x, zi=zi)
            self._x_prev = float(x[-1]) if n else x_prev
            self._y_prev = float(y[-1]) if n else y_prev
            return y
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
        self._x_prev = float(x[-1]) if n else x_prev
        self._y_prev = float(yp)
        return y

    def reset(self) -> None:
        """清空内部状态（换源/换频时调用）。"""
        self._x_prev = 0.0
        self._y_prev = 0.0
        self._q_x_prev = 0.0
        self._q_y_prev = 0.0


class _PairBlocker:
    """Q 路独立状态容器（避免 I/Q 共用状态导致串扰）。"""

    def __init__(self, r: float) -> None:
        self.r = r
        self._x_prev = 0.0
        self._y_prev = 0.0

    def _process_real(self, x: np.ndarray) -> np.ndarray:
        r = self.r
        x_prev = self._x_prev
        y_prev = self._y_prev
        n = x.shape[0]
        try:
            from scipy.signal import lfilter
            zi = np.array([-x_prev + r * y_prev], dtype=np.float64)
            y, _ = lfilter([1.0, -1.0], [1.0, -r], x, zi=zi)
            self._x_prev = float(x[-1]) if n else x_prev
            self._y_prev = float(y[-1]) if n else y_prev
            return y
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
        self._x_prev = float(x[-1]) if n else x_prev
        self._y_prev = float(yp)
        return y
