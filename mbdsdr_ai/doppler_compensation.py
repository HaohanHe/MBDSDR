# SPDX-License-Identifier: MIT
"""
MBDSDR - 多普勒频移补偿（NCO 数字下混频，确定性实现）
================================================================

过境卫星相对地面站有径向速度 → 接收 IQ 被乘上一个时变复指数（多普勒）。
本模块用数字振荡器（NCO）逐样本累积相位后混频，把多普勒**逆运算**回基带：

    接收模型  r(t) = s(t) · exp(+j 2π ∫ f_d(τ) dτ)
    补偿      s̃(t) = r(t) · exp(-j 2π ∫ f_d(τ) dτ)

其中 f_d(t) 既可以来自 TLE/轨道预报（:mod:`mbdsdr_ai.sat_passes.compute_doppler_curve`），
也可以是直接给定的逐样本频移序列（实测 AFC 输出）。

设计要点：
- **确定性**：全程 float64、无全局随机态；``apply_doppler_shift`` 与
  ``remove_doppler_shift`` 用同一套相位累积，成对调用精确回原（仅浮点舍入）。
- **可注入频移**：``doppler_hz`` 支持
    * 标量（恒定频偏）；
    * 等长数组（逐样本瞬时频移 Hz）；
    * callable(t_seconds) -> Hz（任意连续轨迹，如线性 chirp / SGP4 曲线）。
- **无硬件诚实空态**：本模块只做数学混频，不联网、不读串口；频移序列由调用方
  负责给出（预报或实测），不给定时就是恒等（零补偿），绝不"猜"多普勒。

公开方法学：数字下混频 = 复振荡器混频（标准 SDR DSP，与 gr-fir / UHD dsp 同原理）；
NCO 相位累积 phase[k] = 2π/fs · Σ f[j]，矩形近似积分。
"""
from __future__ import annotations

from typing import Callable, Union

import numpy as np

__all__ = ["remove_doppler_shift", "apply_doppler_shift", "doppler_phase_radians"]

# doppler_hz 允许的类型：标量 / 逐样本数组 / t->Hz 可调用
DopplerSpec = Union[float, int, np.ndarray, Callable[[float], float]]


def _resolve_doppler_vector(doppler_hz: DopplerSpec, n: int, fs: float) -> np.ndarray:
    """把标量/数组/callable 统一成长度 n 的 float64 逐样本频移向量。"""
    if callable(doppler_hz):
        t = np.arange(n, dtype=np.float64) / float(fs)
        f = np.empty(n, dtype=np.float64)
        for k in range(n):
            f[k] = float(doppler_hz(float(t[k])))
        return f
    arr = np.asarray(doppler_hz, dtype=np.float64)
    if arr.ndim == 0 or arr.size == 1:
        return np.full(n, float(arr.flat[0]), dtype=np.float64)
    if arr.size != n:
        raise ValueError(
            f"doppler_hz 长度 {arr.size} 与样本数 n={n} 不一致；"
            f"应给标量、等长数组或 callable(t)->Hz")
    return arr


def doppler_phase_radians(doppler_hz: DopplerSpec, n: int, fs: float) -> np.ndarray:
    """逐样本累积多普勒相位（弧度）：phase[k] = 2π/fs · Σ_{j=0..k} f[j]。

    矩形近似积分；返回长度 n 的 float64 相位向量。
    """
    f = _resolve_doppler_vector(doppler_hz, n, fs)
    return (2.0 * np.pi / float(fs)) * np.cumsum(f)


def remove_doppler_shift(iq: np.ndarray, fs: float,
                         doppler_hz: DopplerSpec) -> np.ndarray:
    """补偿 IQ 上的多普勒：乘以 exp(-j·phase)，把信号拉回基带。

    参数:
        iq:        复基带样本（complex64/128）。
        fs:        采样率 Hz。
        doppler_hz: 施加在 IQ 上的多普勒频移序列（标量/逐样本/callable）。
                    这是**接收时实际叠加**的 f_d(t)；本函数逆运算消除它。
                    给 0 → 恒等（不补偿，诚实空态）。

    返回:
        补偿后的复样本，dtype 与输入一致。
    """
    iq = np.asarray(iq)
    n = iq.size
    if n == 0:
        return iq
    phase = doppler_phase_radians(doppler_hz, n, fs)
    out = iq * np.exp(-1j * phase)
    return out.astype(iq.dtype, copy=False)


def apply_doppler_shift(iq: np.ndarray, fs: float,
                        doppler_hz: DopplerSpec) -> np.ndarray:
    """正方向叠加多普勒：乘以 exp(+j·phase)。

    仅用于**合成/测试**——在干净基带上人为叠加多普勒频移，模拟接收链路。
    与 :func:`remove_doppler_shift` 成对，逆运算精确回原。真实链路里不需要它。
    """
    iq = np.asarray(iq)
    n = iq.size
    if n == 0:
        return iq
    phase = doppler_phase_radians(doppler_hz, n, fs)
    out = iq * np.exp(+1j * phase)
    return out.astype(iq.dtype, copy=False)
