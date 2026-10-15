# SPDX-License-Identifier: MIT
"""VNA DSP 底座（NanoVNA-App 机制审计落地第一批）。

全部机制为公开教科书口径的独立实现（RBJ Audio EQ Cookbook 双二阶系数、
滑动窗中值），不抄上游 GPL 代码：

- Biquad：LP/HP/BP/notch/peak 五种 RBJ 双二阶 IIR（cookbook ω0 口径，
  α = sin(ω0)/(2Q)，A = 10^(dBgain/40)），转置直接 II 型（数值稳定），
  prime() 用直流值预充 z1/z2 消除起始瞬态；支持实数与复数信号。
- 中值滤波：滑动窗（2n+1）端点钳位延拓；复数版对实部/虚部独立取中值。
- weighted_median_filter：剔除一个中值元素后取均值（上游 weighted
  median 口径）。诚实边界：对孤脉冲只衰减不根除（脉冲残留 ≈ amp/窗宽），
  毛刺定位请用 median_filter。
- coax_loss_s11：电缆损耗提取（上游 Coax Loss 图机制）。S11 迹线上的
  开路/短路终端"振铃"产生窄谱线波纹：先去线性趋势，FFT 定位波纹基频
  bin，双二阶陷波后正向+反向各滤一遍（零相位、无端点瞬态）。检测用
  双阈值（相对中值显著性 + 相对残差峰绝对占比）防止把缓变趋势误判为
  振铃。返回 (干净 S11, 检出的基频 bin)；未触发时原样返回并如实标注。

诚实边界：陷波 Q 固定 0.3（上游口径）；宁缺毋滥——检不出就明说 None。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

__all__ = [
    "Biquad",
    "median_filter",
    "weighted_median_filter",
    "coax_loss_s11",
]


# ---- 双二阶 IIR ------------------------------------------------------------

@dataclass
class Biquad:
    """RBJ Audio EQ Cookbook 双二阶节（a0 归一化为 1），转置直接 II 型。

    系数口径：ω0 = 2π·fc/fs，α = sin(ω0)/(2Q)，peak 用 A = 10^(dBgain/40)。
    """

    b0: float
    b1: float
    b2: float
    a1: float
    a2: float
    _z1: complex = field(default=0j, repr=False)
    _z2: complex = field(default=0j, repr=False)

    # -- 工厂（fc、fs 单位一致即可，内部只用比值） --
    @classmethod
    def lowpass(cls, fc: float, fs: float, q: float) -> "Biquad":
        w = 2.0 * np.pi * fc / fs
        cw, sw = np.cos(w), np.sin(w)
        al = sw / (2.0 * q)
        b = [(1.0 - cw) / 2.0, 1.0 - cw, (1.0 - cw) / 2.0]
        a = [1.0 + al, -2.0 * cw, 1.0 - al]
        return cls(b[0] / a[0], b[1] / a[0], b[2] / a[0],
                   a[1] / a[0], a[2] / a[0])

    @classmethod
    def highpass(cls, fc: float, fs: float, q: float) -> "Biquad":
        w = 2.0 * np.pi * fc / fs
        cw, sw = np.cos(w), np.sin(w)
        al = sw / (2.0 * q)
        b = [(1.0 + cw) / 2.0, -(1.0 + cw), (1.0 + cw) / 2.0]
        a = [1.0 + al, -2.0 * cw, 1.0 - al]
        return cls(b[0] / a[0], b[1] / a[0], b[2] / a[0],
                   a[1] / a[0], a[2] / a[0])

    @classmethod
    def bandpass(cls, fc: float, fs: float, q: float) -> "Biquad":
        """峰值增益归一化为 1 的带通（cookbook constant 0 dB peak gain）。"""
        w = 2.0 * np.pi * fc / fs
        cw, sw = np.cos(w), np.sin(w)
        al = sw / (2.0 * q)
        b = [al, 0.0, -al]
        a = [1.0 + al, -2.0 * cw, 1.0 - al]
        return cls(b[0] / a[0], b[1] / a[0], b[2] / a[0],
                   a[1] / a[0], a[2] / a[0])

    @classmethod
    def notch(cls, fc: float, fs: float, q: float) -> "Biquad":
        w = 2.0 * np.pi * fc / fs
        cw, sw = np.cos(w), np.sin(w)
        al = sw / (2.0 * q)
        b = [1.0, -2.0 * cw, 1.0]
        a = [1.0 + al, -2.0 * cw, 1.0 - al]
        return cls(b[0] / a[0], b[1] / a[0], b[2] / a[0],
                   a[1] / a[0], a[2] / a[0])

    @classmethod
    def peak(cls, fc: float, fs: float, q: float, gain_db: float) -> "Biquad":
        w = 2.0 * np.pi * fc / fs
        cw, sw = np.cos(w), np.sin(w)
        al = sw / (2.0 * q)
        a0v = 10.0 ** (gain_db / 40.0)
        b = [1.0 + al * a0v, -2.0 * cw, 1.0 - al * a0v]
        a = [1.0 + al / a0v, -2.0 * cw, 1.0 - al / a0v]
        return cls(b[0] / a[0], b[1] / a[0], b[2] / a[0],
                   a[1] / a[0], a[2] / a[0])

    # -- 滤波 --
    def reset(self) -> None:
        self._z1 = 0j
        self._z2 = 0j

    def prime(self, x0: complex) -> None:
        """直流预充：常值输入 x0 下的稳态 z1/z2，消除起始瞬态。"""
        den = 1.0 + self.a1 + self.a2
        if abs(den) < 1e-15:
            self.reset()
            return
        g = (self.b0 + self.b1 + self.b2) / den   # 直流增益
        y = x0 * g
        self._z2 = x0 * self.b2 - self.a2 * y
        self._z1 = x0 * self.b1 - self.a1 * y + self._z2

    def step(self, x: complex) -> complex:
        """转置直接 II 型单步。"""
        y = x * self.b0 + self._z1
        self._z1 = x * self.b1 - self.a1 * y + self._z2
        self._z2 = x * self.b2 - self.a2 * y
        return y

    def filter(self, x) -> np.ndarray:
        """整段滤波（实/复信号皆可，内部按复数状态机跑）。"""
        xs = np.atleast_1d(np.asarray(x))
        out = np.empty(len(xs),
                       dtype=np.complex128 if np.iscomplexobj(xs)
                       else np.float64)
        self.reset()
        for i, v in enumerate(xs):
            r = self.step(complex(v))
            out[i] = r if np.iscomplexobj(xs) else r.real
        return out


# ---- 中值滤波 ---------------------------------------------------------------

def _median_edge_extend(x: np.ndarray, n: int) -> np.ndarray:
    """端点钳位延拓，窗口 2n+1 可完整覆盖原序列每个位置。"""
    return np.concatenate([np.full(n, x[0]), x, np.full(n, x[-1])])


def median_filter(x, n: int) -> np.ndarray:
    """滑动中值（窗宽 2n+1，端点钳位延拓）。复数版实/虚独立取中值。"""
    if n < 1:
        return np.asarray(x).copy()
    xs = np.asarray(x)
    ext = _median_edge_extend(xs, n)
    out = np.empty_like(xs)
    for i in range(len(xs)):
        w = ext[i:i + 2 * n + 1]
        if np.iscomplexobj(xs):
            out[i] = complex(np.median(w.real), np.median(w.imag))
        else:
            out[i] = np.median(w)
    return out


def weighted_median_filter(x, n: int) -> np.ndarray:
    """加权中值：剔除一个中值元素后取均值（上游 weighted_median 口径）。

    诚实边界：对孤脉冲只衰减不根除（脉冲残留 ≈ amp/窗宽）——扫迹平滑
    用；毛刺定位请用 median_filter，校准数据不要用本滤波器。
    """
    if n < 1:
        return np.asarray(x).copy()
    xs = np.asarray(x)
    ext = _median_edge_extend(xs, n)
    out = np.empty_like(xs)
    for i in range(len(xs)):
        w = ext[i:i + 2 * n + 1].astype(np.float64)
        med = float(np.median(w))
        j = int(np.argmin(np.abs(w - med)))     # 剔除一个中值元素
        rest = np.delete(w, j)
        out[i] = np.mean(rest)
    return out


# ---- 电缆损耗提取（Coax Loss 机制） ------------------------------------------

def coax_loss_s11(s11, notch_q: float = 0.3,
                  prominence: float = 6.0,
                  peak_ratio: float = 0.25
                  ) -> Tuple[np.ndarray, Optional[int]]:
    """S11 迹线上的电缆损耗提取：去趋势 -> FFT 定位振铃基频 -> 陷波 -> 零相位往返滤波。

    参数
    ----
    s11 : 复数序列（等频距采样）。
    notch_q : 陷波器 Q（上游口径 0.3）。
    prominence : 残差频谱显著性阈值（峰 / 频谱中值），默认 6 倍。
    peak_ratio : 绝对占比阈值：峰须 >= peak_ratio × (n/2) × max|残差|，
        防止数值噪声谱线误触发（正弦残差该比值 ≈ 0.5）。

    返回 (filtered, detected_bin)。detected_bin 为检出的波纹基频 bin，
    未触发陷波时为 None（诚实标注，调用方可区分"滤过了"与"本来干净"）。

    诚实边界：缓变趋势经线性去趋势后再检测——只认**窄谱线**振铃；
    宁缺毋滥，检不出就原样返回 None。
    """
    x = np.asarray(s11, dtype=np.complex128)
    n = len(x)
    if n < 8:
        return x.copy(), None
    # 线性去趋势（最小二乘拟合 idx 的一次项），保留复数实/虚各自趋势
    idx = np.arange(n, dtype=np.float64)
    a = np.vstack([idx, np.ones(n)]).T
    coef_r, *_ = np.linalg.lstsq(a, x.real, rcond=None)
    coef_i, *_ = np.linalg.lstsq(a, x.imag, rcond=None)
    resid = (x.real - a @ coef_r) + 1j * (x.imag - a @ coef_i)
    spec = np.abs(np.fft.fft(resid))
    lo, hi = 1, n // 2                       # 忽略直流与镜像
    if hi <= lo:
        return x.copy(), None
    band = spec[lo:hi]
    k = int(np.argmax(band)) + lo
    floor = np.median(band) + 1e-30
    abs_peak = peak_ratio * (n / 2.0) * float(np.max(np.abs(resid))) + 1e-30
    if not (spec[k] / floor >= prominence and spec[k] >= abs_peak):
        return x.copy(), None
    bq = Biquad.notch(fc=k / n, fs=1.0, q=notch_q)
    y = bq.filter(x)                          # 正向
    bq.reset()
    y = bq.filter(np.asarray(y)[::-1])[::-1]  # 时间反转再滤 -> 零相位
    return np.asarray(y), k
