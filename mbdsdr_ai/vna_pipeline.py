# SPDX-License-Identifier: MIT
"""VNA 一键分析管道：扫频结果 -> 结构化报告（第十二批）。

把本仓已落地的单点分析组合成一条诚实的工作流：每个子分析独立运行、
独立失败——无谐振就 None（带原因），TDR 检不出峰就 None，绝不为凑齐
报告伪造字段。

- analyze_one_port(freqs_hz, s11, ...) -> dict：
  * vswr_min / vswr_max + 对应频率（全迹线钉死）
  * z_mag 最小点（近似 ESR 口径沿用 analyze_resonance_py）
  * resonance：analyze_resonance_py 输出（无谐振 None）
  * tdr：tdr_length_py 长度 + tdr_modes.full_profile 剖面前 N 点
    （电缆名给定时用 CABLE_VF 的 VF，cable= 未知道显式报错）
  * lc_match：在 |S11| 最差频点的 lc_match 解集（含元件换算）
  * coax_loss：coax_loss_s11 去振铃结果 + 检出的波纹基频 bin
  * band：中心频点所属业余频段（带外 None）
- analyze_two_port(freqs_hz, s21) -> dict：filter_shape_py 滤波器指标
  + 通带内 ripple 统计。

校准不内嵌：调用方先用 vna_cal 显式求误差盒再喂校正后的 S 参数——
管道对输入的"是否已校准"不做假设（诚实边界，docstring 登记）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .fill_evidence_example import filter_shape_py, tdr_length_py
from .tdr_modes import full_profile
from .vna_dsp import coax_loss_s11
from .vna_match import CABLE_VF, cable_velocity_factor, lc_match, \
    reactance_to_component
from .vna_trace_math import find_band

__all__ = ["analyze_one_port", "analyze_two_port"]

_PROFILE_POINTS = 64          # 剖面采样点数（报告中截断，防膨胀）


def _check_inputs(freqs_hz, s) -> tuple:
    f = np.asarray(freqs_hz, dtype=np.float64)
    x = np.asarray(s, dtype=np.complex128)
    if len(f) != len(x) or len(f) < 4:
        raise ValueError("freqs/迹线须等长且 >=4 点")
    if np.any(~np.isfinite(f)) or np.any(~np.isfinite(x)):
        raise ValueError("输入含非有限值")
    if np.any(np.diff(f) < 0):
        raise ValueError("频率轴须非降序")
    return f, x


def _vswr(s11: np.ndarray, z0: float) -> np.ndarray:
    g = np.abs(s11)
    g = np.minimum(g, 0.999999)
    return (1.0 + g) / (1.0 - g)


def analyze_one_port(freqs_hz, s11, z0: float = 50.0, vf: float = 0.66,
                     cable: Optional[str] = None,
                     freq_hz: Optional[float] = None) -> Dict[str, Any]:
    """单端口全分析。freq_hz 指定 LC 匹配频点（缺省取 |S11| 最差点）；
    cable 给定电缆名时覆盖 vf 做 TDR 距离换算。"""
    if cable is not None:
        vf = cable_velocity_factor(cable)      # 未知名称显式 KeyError
    f, s = _check_inputs(freqs_hz, s11)
    vswr = _vswr(s, z0)
    z_mag = np.abs(z0 * (1 + s) / (1 - s))
    k_worst = int(np.argmax(vswr))
    f_match = float(freq_hz) if freq_hz is not None else float(f[k_worst])
    # LC 匹配在最差频点附近的采样点做
    k_near = int(np.argmin(np.abs(f - f_match)))

    out: Dict[str, Any] = {
        "points": len(f),
        "f_range_hz": (float(f[0]), float(f[-1])),
        "z0": z0,
        "vswr_min": (float(f[np.argmin(vswr)]), float(np.min(vswr))),
        "vswr_max": (float(f[k_worst]), float(np.max(vswr))),
        "z_mag_min": (float(f[np.argmin(z_mag)]),
                      float(np.min(z_mag))),
        "resonance": None,
        "tdr": None,
        "lc_match": [],
        "coax_loss": None,
        "band": find_band(float(f[len(f) // 2])),
    }

    # 谐振分析在阻抗域做：analyze_resonance_py 是 |S11| 域占位（其
    # docstring 自标"合成占位用"，|Γ|min=0.82 时 -3dB 阈值 >1 永不
    #  crossed）——管道内用教科书口径重实现：Z=Z0(1+Γ)/(1-Γ)，|Z| 极小
    # 点为 fr，两侧升到 esr·√2 的频距为 -3dB 带宽，Q=fr/BW。
    z_ohm = z0 * (1 + s) / (1 - s)
    zmag = np.abs(z_ohm)
    k_fr = int(np.argmin(zmag))
    esr = float(zmag[k_fr])
    target = esr * np.sqrt(2.0)
    lo = next((k for k in range(k_fr - 1, -1, -1) if zmag[k] >= target), 0)
    hi = next((k for k in range(k_fr + 1, len(f)) if zmag[k] >= target),
              len(f) - 1)
    bw = float(f[hi] - f[lo])
    q = float(f[k_fr]) / bw if bw > 0 else float("nan")
    if (bw > 0 and 0 < lo < k_fr < hi < len(f) - 1 and np.isfinite(q)
            and q >= 1.0):
        out["resonance"] = {"fr_hz": float(f[k_fr]), "bw_hz": bw, "q": q,
                            "esr": esr}
    # 阈值碰到轴端（lo==0/hi==末点）或 Q<1 = 无可判谐振——保持 None

    try:
        length = tdr_length_py(f.tolist(),
                               [[v.real, v.imag] for v in s], vf=vf)
        prof = full_profile(s, f, z0=z0)
        pick = np.linspace(0, len(prof.z_ohm) - 1,
                           min(_PROFILE_POINTS, len(prof.z_ohm))).astype(int)
        out["tdr"] = {
            "length_m": length,
            "vf": vf,
            "profile_t_s": prof.t_s[pick].tolist(),
            "profile_z_ohm": [float("inf") if np.isinf(prof.z_ohm[k])
                              else float(prof.z_ohm[k]) for k in pick],
        }
    except Exception as e:                       # 诚实记录失败原因
        out["tdr"] = {"error": str(e)}

    try:
        zl = z0 * (1 + s[k_near]) / (1 - s[k_near])
        for sol in lc_match(complex(zl), z0):
            comp = reactance_to_component(float(f[k_near]), sol.xs)
            entry = {"freq_hz": float(f[k_near]), "xs_ohm": sol.xs,
                     "xps_ohm": sol.xps, "xpl_ohm": sol.xpl,
                     "series_component": comp}
            out["lc_match"].append(entry)
    except ValueError:
        out["lc_match"] = []                     # 已良好匹配/无物理解：空列表

    clean, det = coax_loss_s11(s)
    out["coax_loss"] = {"ripple_bin": det,
                        "cleaned": clean.tolist() if det is not None else None}
    return out


def analyze_two_port(freqs_hz, s21) -> Dict[str, Any]:
    """双端口（传输）分析：滤波器形态 + -3dB 通带指标 + 通带 ripple。

    filter_shape_py 返回四形态字符串；带宽/中心由本函数在 dB 迹线上
    找 -3dB 交点自行计算（两个口径分开，不混）。
    """
    f, s = _check_inputs(freqs_hz, s21)
    db = 20.0 * np.log10(np.maximum(np.abs(s), 1e-30))
    shape = filter_shape_py(db.tolist())
    out: Dict[str, Any] = {
        "points": len(f),
        "f_range_hz": (float(f[0]), float(f[-1])),
        "filter_shape": shape,
        "f_center_hz": None,
        "bw_hz": None,
        "passband_ripple_db": None,
    }
    peak = float(np.max(db))
    thr = peak - 3.0
    above = db >= thr
    if above.sum() >= 2 and not above.all():
        idx = np.where(above)[0]
        out["f_center_hz"] = float(f[idx[len(idx) // 2]])
        out["bw_hz"] = float(f[idx[-1]] - f[idx[0]])
        band = db[idx[0]:idx[-1] + 1]
        if len(band) >= 4:
            out["passband_ripple_db"] = round(
                float(np.percentile(band, 95))
                - float(np.percentile(band, 5)), 3)
    return out
