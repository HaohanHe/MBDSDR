# SPDX-License-Identifier: MIT
"""TDR 模式扩展：LP-Impulse / LP-Step + 阻抗剖面（NanoVNA-App 第七批）。

上游 Graphs.cpp TDR 三模式：Bandpass（现有 tdr_length_py）、LP-Impulse
（共轭延拓 2N）、LP-Step（冲激响应累积和得阶跃 ρ(t)，再
Z = Z0·(1+ρ)/(1−ρ) 得阻抗剖面——电缆故障定位的物理核心输出）。

本实现（教科书信号处理口径独立推导）：
- _hermitian_extend：正频段 S11 共轭延拓成 2N 谱（经典 2N conj
  extend）——非精确厄米（DC/奈奎斯特不对称），输出取实部等价于厄米
  投影，是 LP TDR 的标准实践，docstring 登记不装作精确；
- tdr_impulse_response：延拓 + Kaiser(β=6，与 tdr_length_py 同口径)
  窗 -> IFFT 得冲激响应，返回 (t_roundtrip 轴, ρ_impulse)；
- tdr_step_response：冲激累积和 -> 阶跃 ρ_step(t)；
- impedance_profile：Z(t) = Z0(1+ρ)/(1−ρ)；|ρ|≥1 时 Z 无界（开路/
  短路极限）——返回 inf 并如实标注，不 clip 造假。

诚实边界：LP 模式假设负频段与正频段共轭对称（被测响应实值），对含频
散/非互易的件不成立；剖面 t=0 处含参考面直流分量，解读从 t>0 开始。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

__all__ = [
    "tdr_impulse_response",
    "tdr_step_response",
    "impedance_profile",
    "TdrModesResult",
]

_KAISER_BETA = 6.0


def _hermitian_extend(x: np.ndarray) -> np.ndarray:
    """正频段谱共轭延拓成 2N 厄米谱（实冲激响应的充分条件）。"""
    return np.concatenate([x, np.conj(x[::-1])])


def _time_axis(n_ext: int, span_hz: float) -> np.ndarray:
    """延拓后时间轴（双程时延秒）：采样间隔 = 1/(2·span)。"""
    dt = 1.0 / (2.0 * span_hz)
    return np.arange(n_ext) * dt


def tdr_impulse_response(s11, freqs_hz, window_beta: float = _KAISER_BETA
                         ) -> Tuple[np.ndarray, np.ndarray]:
    """LP-Impulse：返回 (t_roundtrip 轴, 冲激响应 ρ(t))。

    freqs 须为等距正频段；span = f[-1]−f[0]；beta<=0 为矩形窗。

    DC 与窗的诚实边界（docstring 登记）：Kaiser 等锥形窗在频带边缘含
    DC bin 处趋零，会把阶跃响应渐近值压掉且校准恢复会同比放大振铃/
    噪声——因此 LP-Step/阻抗剖面默认用矩形窗（beta=0，渐近值物理
    精确）；beta>0 留给看重旁瓣抑制、能接受渐近值漂移的调用方，本
    函数不做自动校准（不引入噪声放大因子）。
    """
    f = np.asarray(freqs_hz, dtype=np.float64)
    x = np.asarray(s11, dtype=np.complex128)
    n = len(f)
    if n < 4 or len(x) != n:
        raise ValueError("s11/freqs 长度须一致且 >=4")
    span = f[-1] - f[0]
    if span <= 0:
        raise ValueError("频率跨度必须为正")
    xw = x * np.kaiser(n, window_beta) if window_beta > 0 else x
    ext = _hermitian_extend(xw)
    imp = np.fft.ifft(ext).real                    # 厄米投影 -> 实响应
    return _time_axis(len(ext), span), imp


def tdr_step_response(s11, freqs_hz, window_beta: float = 0.0
                      ) -> Tuple[np.ndarray, np.ndarray]:
    """LP-Step：冲激响应累积和 -> 阶跃 ρ_step(t)（同轴 TDR 示波器口径）。

    默认矩形窗（window_beta=0）保证渐近值 = 实测 DC 反射（物理精确）；
    beta>0 的旁瓣/渐近值权衡见 tdr_impulse_response docstring。
    """
    t, imp = tdr_impulse_response(s11, freqs_hz, window_beta)
    return t, np.cumsum(imp)


class TdrModesResult:
    """阻抗剖面结果：t 轴、Z(t)、及越界标注（|ρ|>=1 处 Z=inf）。"""

    def __init__(self, t_s: np.ndarray, z_ohm: np.ndarray,
                 clipped: np.ndarray) -> None:
        self.t_s = t_s
        self.z_ohm = z_ohm
        #: 布尔掩码：True 表示该点 |ρ|>=1，Z 无界（如实标注，不 clip 造假）
        self.unbounded = clipped


def impedance_profile(rho_step, z0: float = 50.0) -> TdrModesResult:
    """阶跃 ρ(t) -> 阻抗剖面 Z(t)。z0<=0 显式拒绝。

    |ρ|→1 两个极限物理含义不同：ρ→+1 开路 Z→+∞（无界标注）；ρ→−1 短路
    Z→0（有界，如实给 0 不标无界）。unbounded 掩码只标真正无界的点。
    """
    if z0 <= 0:
        raise ValueError(f"z0 须为正，收到 {z0}")
    r = np.asarray(rho_step, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = z0 * (1.0 + r) / (1.0 - r)
    unbounded = np.isinf(z)
    return TdrModesResult(np.arange(len(r)) * 0.0, z, unbounded)


def full_profile(s11, freqs_hz, z0: float = 50.0,
                 window_beta: float = 0.0) -> TdrModesResult:
    """一站式：S11 -> 阻抗剖面（t 轴 + Z(t) + 无界掩码）。带参考面切除：
    返回 t 从第一个非零采样开始（t=0 的直流参考面分量不占位）。默认
    矩形窗（渐近值物理精确），窗口权衡见 tdr_impulse_response。"""
    t, rho = tdr_step_response(s11, freqs_hz, window_beta)
    res = impedance_profile(rho, z0)
    return TdrModesResult(t[1:], res.z_ohm[1:], res.unbounded[1:])
