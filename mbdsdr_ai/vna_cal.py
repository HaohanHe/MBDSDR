# SPDX-License-Identifier: MIT
"""单端口 SOLT 校准（非理想校准件建模 + 误差盒求解，NanoVNA-App 第三批）。

全部公式为公开文献口径的独立实现（Keysight AN 1287-3 误差盒模型），
不抄上游代码：

- 校准件定义件（非理想 OSL）：
  * Open：边缘电容 C(f) = C0 + C1·f + C2·f² + C3·f³（Keysight 惯例 f 以
    GHz 代入，系数单位对应 pF/pF·GHz⁻¹…），Z = −j/(2πf·C)，
    Γ = (Z−Z0)/(Z+Z0)，再乘偏移长度相移 exp(−j·4πf·len/v)。
  * Short：引线电感 L(f) = L0+L1·f+L2·f²+L3·f³（f 以 GHz 代入，系数 nH
    系），Z = j·2πf·L，Γ 同上口径 + 偏移相移。
  * Load：R 串 jωL 串 1/(jωC) 集总模型。
  * 偏移相移 v = c·VF（VF 默认 1.0 空气线；相移只建模相位，**不建模偏移
    段损耗**——诚实边界，文档登记）。
- solve_error_box：对 e00（方向性）、e10e01（反射跟踪）、e11（源匹配）
  的求解。推导关键：Γm = e00 + T·Γ/(1−e11·Γ) 经整理对 (e00, T−e00·e11,
  e11) 是**线性**的：Γm = a + b·Γ + c·Γm·Γ，三件标准 → 3×3 线性方程组
  闭式解（比迭代稳健，无初值问题）。
- apply_calibration：Γ = (Γm−e00)/(T + e11·(Γm−e00))。
- s21_enhanced_response：S21c = (S21m−e30)/(e10e32·(1−e11·S11c))，
  e30=隔离度，e10e32=through 跟踪（上游增强响应口径；严格式含 (1−e11²)
  分子修正，本实现采用上游同款近似并如实注释）。

正确性论证：测试用已知误差项仿真整台 VNA（标准件+被测件全走误差盒），
校准后 Γ 必须恢复到 1e-9 量级——推导正确与否不靠抄，靠仿真钉死。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

__all__ = [
    "C_LIGHT",
    "OpenStandard",
    "ShortStandard",
    "LoadStandard",
    "ErrorBox",
    "solve_error_box",
    "apply_calibration",
    "s21_enhanced_response",
]

C_LIGHT = 299792458.0
_GHZ = 1e9


# ---- 校准件定义件 -------------------------------------------------------------

@dataclass(frozen=True)
class OpenStandard:
    """开路件：边缘电容三阶多项式（系数按 Keysight 惯例，f 代入 GHz）。"""
    c0_pF: float = 0.0
    c1: float = 0.0       # pF/GHz
    c2: float = 0.0       # pF/GHz²
    c3: float = 0.0       # pF/GHz³
    offset_len_m: float = 0.0
    offset_vf: float = 1.0
    use_ideal: bool = False

    def capacitance(self, f_hz) -> np.ndarray:
        if self.use_ideal:
            return np.zeros_like(np.asarray(f_hz, dtype=np.float64))
        g = np.asarray(f_hz, dtype=np.float64) / _GHZ
        return (self.c0_pF + self.c1 * g + self.c2 * g ** 2
                + self.c3 * g ** 3) * 1e-12

    def gamma(self, f_hz, z0: float = 50.0) -> np.ndarray:
        f = np.asarray(f_hz, dtype=np.float64)
        if self.use_ideal:
            return _apply_offset(np.ones_like(f, dtype=np.complex128),
                                 f, self.offset_len_m, self.offset_vf)
        c = self.capacitance(f)
        w = 2.0 * np.pi * f
        with np.errstate(divide="ignore"):
            z = -1j / np.maximum(w * c, 1e-30)
        g = np.where(np.isfinite(z), (z - z0) / (z + z0),
                     np.ones_like(z, dtype=np.complex128))
        return _apply_offset(g, f, self.offset_len_m, self.offset_vf)


@dataclass(frozen=True)
class ShortStandard:
    """短路件：引线电感三阶多项式（f 代入 GHz，系数 nH 系）。"""
    l0_nH: float = 0.0
    l1: float = 0.0       # nH/GHz
    l2: float = 0.0       # nH/GHz²
    l3: float = 0.0       # nH/GHz³
    offset_len_m: float = 0.0
    offset_vf: float = 1.0
    use_ideal: bool = False

    def gamma(self, f_hz, z0: float = 50.0) -> np.ndarray:
        f = np.asarray(f_hz, dtype=np.float64)
        w = 2.0 * np.pi * f
        if self.use_ideal:
            return _apply_offset(-np.ones_like(f, dtype=np.complex128),
                                 f, self.offset_len_m, self.offset_vf)
        g = f / _GHZ
        l = (self.l0_nH + self.l1 * g + self.l2 * g ** 2
             + self.l3 * g ** 3) * 1e-9
        z = 1j * w * l
        gam = (z - z0) / (z + z0)
        return _apply_offset(gam, f, self.offset_len_m, self.offset_vf)


@dataclass(frozen=True)
class LoadStandard:
    """匹配负载：R 串 jωL 串 1/(jωC) 集总模型。"""
    r_ohm: float = 50.0
    l_nH: float = 0.0
    c_pF: float = 0.0
    offset_len_m: float = 0.0
    offset_vf: float = 1.0

    def gamma(self, f_hz, z0: float = 50.0) -> np.ndarray:
        f = np.asarray(f_hz, dtype=np.float64)
        w = 2.0 * np.pi * f
        z = self.r_ohm + 1j * w * self.l_nH * 1e-9
        if self.c_pF != 0.0:
            z = z + 1.0 / (1j * w * self.c_pF * 1e-12)
        gam = (z - z0) / (z + z0)
        return _apply_offset(gam, f, self.offset_len_m, self.offset_vf)


def _apply_offset(gamma, f_hz, length_m: float, vf: float) -> np.ndarray:
    """偏移长度相移：Γ·exp(−j·4πf·len/v)（双程；只相位，无损耗——见模块
    docstring 诚实边界）。"""
    if length_m == 0.0:
        return np.asarray(gamma)
    if vf <= 0.0:
        raise ValueError(f"偏移段 VF 须为正，收到 {vf}")
    v = C_LIGHT * vf
    ph = np.exp(-1j * 4.0 * np.pi * np.asarray(f_hz, dtype=np.float64)
                * length_m / v)
    return np.asarray(gamma) * ph


# ---- 误差盒 -------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorBox:
    """单端口误差盒：e00=方向性，t=e10e01=反射跟踪，e11=源匹配。"""
    e00: complex
    t: complex
    e11: complex


def solve_error_box(gamma_meas_o, gamma_meas_s, gamma_meas_l,
                    gamma_o, gamma_s, gamma_l) -> ErrorBox:
    """三件标准实测/定义 Γ -> 误差项（3×3 线性闭式解，推导见模块 docstring）。"""
    mo, ms, ml = (np.asarray(g, dtype=np.complex128)
                  for g in (gamma_meas_o, gamma_meas_s, gamma_meas_l))
    oo, ss, ll = (np.asarray(g, dtype=np.complex128)
                  for g in (gamma_o, gamma_s, gamma_l))
    # 每频点一个 3x3 方程组：Γm = a + b·Γ + c·Γm·Γ（推导见模块 docstring）
    eqs = np.stack([                     # (3 方程, 3 系数, n 频点)
        np.stack([np.ones_like(oo), oo, mo * oo]),
        np.stack([np.ones_like(ss), ss, ms * ss]),
        np.stack([np.ones_like(ll), ll, ml * ll]),
    ])
    a = np.moveaxis(eqs, -1, 0)          # (n, 3, 3)
    b = np.moveaxis(np.stack([mo, ms, ml]), -1, 0)   # (n, 3)
    det = np.linalg.det(a)
    if np.any(np.abs(det) < 1e-30):
        raise ValueError("误差盒方程组奇异：三件标准 Γ 不独立（检查定义件/测量）")
    sol = np.linalg.solve(a, b[..., None])[..., 0]   # (n, 3) 批量求解
    e00, bcoef, e11 = sol[:, 0], sol[:, 1], sol[:, 2]
    return ErrorBox(e00=e00, t=bcoef + e00 * e11, e11=e11)


def apply_calibration(gamma_meas, box: ErrorBox):
    """实测 Γ -> 校准后 Γ：Γ = (Γm−e00)/(T + e11·(Γm−e00))。"""
    gm = np.asarray(gamma_meas, dtype=np.complex128)
    d = gm - box.e00
    return d / (box.t + box.e11 * d)


def s21_enhanced_response(s21_meas, s11_cal, e30: complex,
                          e10e32: complex, e11: complex):
    """S21 增强响应校准（上游口径）。

    S21c = (S21m − e30) / (e10e32 · (1 − e11·S11c))
    e30=隔离度（未测给 0），e10e32=through 跟踪项。严格式分子另有 (1−e11²)
    修正，本实现与上游一致采用近似式并在此注释，不静默混口径。
    """
    if e10e32 == 0:
        raise ValueError("e10e32（through 跟踪）不能为 0")
    s21m = np.asarray(s21_meas, dtype=np.complex128)
    s11c = np.asarray(s11_cal, dtype=np.complex128)
    e30, e10e32, e11 = (complex(v) for v in (e30, e10e32, e11))
    return (s21m - e30) / (e10e32 * (1.0 - e11 * s11c))
