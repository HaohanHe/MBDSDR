# SPDX-License-Identifier: MIT
"""
Parametric I/Q imbalance correction (gain g + quadrature error phi).

A zero-IF receiver's in-phase and quadrature branches suffer from a gain
mismatch and a quadrature-phase error, which produces an image symmetric about
the centre frequency.  This module implements the standard parametric 2x2
correction matrix and estimates (g, phi) from the signal's second-order
statistics.

Imbalance model (I branch as reference)::

    I_m = I_i
    Q_m = g * (cos(phi) * Q_i + sin(phi) * I_i)

where (I_i, Q_i) are the ideal orthogonal equal-power components.

Inversion (correction)::

    I_c = I_m
    Q_c = Q_m/(g*cos(phi)) - tan(phi) * I_m

Correction matrix C acting on the column vector [I_m, Q_m]^T::

        [  1            0          ]
    C = [ -tan(phi)   1/(g cos phi) ]

Parameter estimate (assumes an approximately isotropic signal,
E[I_i^2] = E[Q_i^2] = sigma^2, E[I_i Q_i] = 0)::

    g      = sqrt(var_Q / var_I)
    sin phi = cov_IQ / sqrt(var_I * var_Q)

With no calibration the matrix stays the identity (g=1, phi=0): it never
hard-codes a fake calibration.
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
