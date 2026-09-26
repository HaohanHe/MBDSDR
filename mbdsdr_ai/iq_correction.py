"""
MBDSDR AI 内核 - I/Q 不平衡参数化校正（增益 g + 相位 φ 校正矩阵）
==================================================================

零中频（ZIF）接收机 I/Q 两路存在增益差与相位不正交，产生关于中心频
对称的镜像（image）。本模块实现标准的参数化 2×2 校正矩阵，并从数据
二阶矩估计 (g, φ)。

不平衡模型（以 I 路为参考）::

    I_m = I_i
    Q_m = g * (cos φ * Q_i + sin φ * I_i)

其中 (I_i, Q_i) 是理想正交等功率分量，g 为 Q 路增益比，φ 为相位误差。

反演（校正）::

    I_c = I_m
    Q_c = Q_m/(g·cos φ) - tan φ · I_m

校正矩阵 C（作用于列向量 [I_m, Q_m]ᵀ）::

        [  1        0        ]
    C = [ -tan φ   1/(g cos φ) ]

参数估计（假设信号在 I/Q 平面近似各向同性，E[I_i²]=E[Q_i²]=σ²，
E[I_i Q_i]=0）::

    g      = sqrt(var_Q / var_I)
    sin φ  = cov_IQ / sqrt(var_I · var_Q)

默认未校准时 C = 单位阵（g=1, φ=0）——绝不硬编码假校准参数。

对照：
  - SDR++ core/src/dsp/correction/ 只有 dc_blocker.h，无 IQ 不平衡块；
    参数化 g/φ 校正取自零中频接收机经典模型（与 GNU Radio /gr-osmosdr
    设备端 IQ 校正、KrakenSDR 等通用做法一致）。
  - 协方差关系同 mbdsdr_ai/iq_frontend.py:281-289 的诊断估计。

License: GPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

__all__ = ["IQImbalance", "IQCorrector", "estimate_imbalance"]


@dataclass
class IQImbalance:
    """估计出的 I/Q 不平衡参数。"""
    gain_ratio: float        # g = var_Q/var_I 的平方根
    phase_error_deg: float   # φ（度），Q 路相对 I 路的相位正交误差

    @property
    def gain_error_db(self) -> float:
        """增益不平衡（dB）：20*log10(g)。"""
        return 20.0 * np.log10(max(self.gain_ratio, 1e-12))


def estimate_imbalance(iq: np.ndarray) -> IQImbalance:
    """从一段复 IQ 估计 (g, φ)。

    假设信号在 I/Q 平面近似各向同性。返回 gain_ratio=g, phase_error_deg=φ。
    """
    iq = np.asarray(iq)
    i = iq.real.astype(np.float64)
    q = iq.imag.astype(np.float64)
    var_i = float(np.var(i))
    var_q = float(np.var(q))
    cov_iq = float(np.mean((i - i.mean()) * (q - q.mean())))
    if var_i <= 1e-15 or var_q <= 1e-15:
        return IQImbalance(1.0, 0.0)
    g = float(np.sqrt(var_q / var_i))
    sin_phi = np.clip(cov_iq / np.sqrt(var_i * var_q), -1.0, 1.0)
    phi_deg = float(np.degrees(np.arcsin(sin_phi)))
    return IQImbalance(gain_ratio=g, phase_error_deg=phi_deg)


class IQCorrector:
    """参数化 I/Q 不平衡校正器（增益 g + 相位 φ）。

    未校准时为单位阵（直通）；调用 set_imbalance(g, phi_deg) 注入已知
    校准参数，或 fit(iq) 从数据估计后自动设置。

    用法::

        c = IQCorrector()
        c.fit(iq_calibration_samples)   # 从数据估计 g, φ
        y = c.process(x)                # 校正后续流
    """

    def __init__(self) -> None:
        self._g = 1.0
        self._phi = 0.0  # rad
        self._matrix = np.eye(2, dtype=np.float64)

    # ---- 参数设置 ----
    def set_imbalance(self, gain_ratio: float, phase_error_deg: float) -> None:
        """设置已知不平衡参数并重建校正矩阵。"""
        self._g = float(gain_ratio)
        self._phi = float(np.radians(phase_error_deg))
        self._rebuild()

    def fit(self, iq: np.ndarray) -> IQImbalance:
        """从一段 IQ 估计 g, φ 并应用。"""
        est = estimate_imbalance(iq)
        self.set_imbalance(est.gain_ratio, est.phase_error_deg)
        return est

    def _rebuild(self) -> None:
        g = self._g
        phi = self._phi
        c = np.cos(phi)
        # 防 cosφ 接近 0（φ→90°）时除零
        if abs(c) < 1e-6:
            c = 1e-6 if c >= 0 else -1e-6
        self._matrix = np.array([
            [1.0,                 0.0],
            [-np.tan(phi),        1.0 / (g * c)],
        ], dtype=np.float64)

    # ---- 状态 ----
    @property
    def matrix(self) -> np.ndarray:
        """当前 2×2 校正矩阵。"""
        return self._matrix.copy()

    @property
    def imbalance(self) -> IQImbalance:
        return IQImbalance(self._g, float(np.degrees(self._phi)))

    def reset(self) -> None:
        """恢复单位阵（未校准直通）。"""
        self._g = 1.0
        self._phi = 0.0
        self._matrix = np.eye(2, dtype=np.float64)

    # ---- 处理 ----
    def process(self, x: np.ndarray) -> np.ndarray:
        """对一段复 IQ 施加校正矩阵。未校准（单位阵）时数值上近似直通。"""
        x = np.asarray(x)
        if x.size == 0 or not np.iscomplexobj(x):
            return x
        iq = np.column_stack((x.real, x.imag)).astype(np.float64)
        out = iq @ self._matrix.T
        return (out[:, 0] + 1j * out[:, 1]).astype(x.dtype)


def inject_imbalance(iq: np.ndarray, gain_ratio: float,
                     phase_error_deg: float) -> np.ndarray:
    """测试辅助：给理想 IQ 注入已知增益/相位不平衡（正向模型）。

    I_m = I_i；Q_m = g(cosφ Q_i + sinφ I_i)。
    与 IQCorrector 互为正反演，用于单测闭环。
    """
    iq = np.asarray(iq)
    g = float(gain_ratio)
    phi = np.radians(phase_error_deg)
    i = iq.real.astype(np.float64)
    q = iq.imag.astype(np.float64)
    q_bad = g * (np.cos(phi) * q + np.sin(phi) * i)
    return (i + 1j * q_bad).astype(iq.dtype)
