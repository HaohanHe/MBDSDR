# SPDX-License-Identifier: MIT
"""L 型匹配网络求解器 + 电缆速度因数库（NanoVNA-App 机制审计落地第二批）。

全部为教科书闭式解的独立推导（Pozar《Microwave Engineering》L-network
一节），不抄上游代码：

- lc_match(zl, r0)：负载 ZL 匹配到 R0 的 L 网络解集。
  * R > R0（高阻侧）：并联臂在负载侧。先由 |Z|²/RL > R0 判据求串联电抗
    Xs² = R0·|Z|²/RL − R0²（两个符号两支解），再求并联臂：
    1/Xp = BL − Xs/(R0²+Xs²)，其中 BL = −XL/|Z|²（注意并联臂导纳
    Y_p = −j/Xp 的负号——2026-10-15 首版推导在此踩坑，端到端测试抓住）。
  * R < R0（低阻侧）：串联臂先行，Xs = −XL ± √(R0·RL − RL²)（要求
    R0·RL > RL²），再由导纳口径求源侧并联臂 Xp = −|Z'|²/X'，
    Z' = RL + j(XL+Xs)。
  * |R−R0|/R0 ≤ 5%：退化为单串电抗 Xs = −XL（SWR<1.1，无需并联臂）。
  * 已良好匹配（VSWR ≤ 1.05）：返回空列表（无事可做，诚实空态）。
  * R ≤ 0 或 Q=|X|/R > 100：ValueError（无物理解，不返回垃圾）。

  每个解为 (xs, xps, xpl)：xps/xpl 为 None 表示该臂不存在。解集均经
  单元测试做端到端验证：把网络接回负载，输入阻抗必须 ≈ R0（实部虚部
  双钉死）——公式推导正确性不靠抄，靠验证。

- reactance_to_component(f_hz, x)：电抗 -> (类型, 值, 单位)。X>0 为电感
  L = X/(2πf)，X<0 为电容 C = −1/(2πf·X)；|X| 过小（<1e-12）诚实报 None。

- CABLE_VF / cable_velocity_factor(name)：常见同轴电缆速度因数表
  （公开数据），未知名称显式 KeyError。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

__all__ = [
    "LcSolution",
    "lc_match",
    "reactance_to_component",
    "CABLE_VF",
    "cable_velocity_factor",
]


@dataclass(frozen=True)
class LcSolution:
    """L 网络解：xs=串联电抗(Ω)，xps=源侧并联电抗，xpl=负载侧并联电抗。
    None 表示该臂不存在（退化解）。"""
    xs: float
    xps: Optional[float] = None
    xpl: Optional[float] = None


def _vswr(rl: float, xl: float, r0: float) -> float:
    n, p = rl - r0, rl + r0
    ro = math.sqrt((n * n + xl * xl) / max(p * p + xl * xl, 1e-30))
    ro = min(ro, 0.9999)
    return (1.0 + ro) / (1.0 - ro)


def _solve_hi(r0: float, rl: float, xl: float) -> List[LcSolution]:
    """R > R0：并联臂在负载侧。推导见模块 docstring。"""
    z2 = rl * rl + xl * xl
    disc = r0 * z2 / rl - r0 * r0
    if disc < 0.0:
        return []
    out = []
    for s in (1.0, -1.0):
        xs = s * math.sqrt(disc)
        bl = -xl / z2
        # Y_p = 1/(j·Xp) = −j/Xp，故 1/Xp = BL − Xs/(R0²+Xs²)
        inv_xp = bl - xs / (r0 * r0 + xs * xs)
        if abs(inv_xp) < 1e-15:
            continue
        out.append(LcSolution(xs=xs, xpl=1.0 / inv_xp))
    return out


def _solve_lo(r0: float, rl: float, xl: float) -> List[LcSolution]:
    """R < R0：串联臂先行，并联臂在源侧。推导见模块 docstring。"""
    disc = r0 * rl - rl * rl
    if disc < 0.0:
        return []
    out = []
    for s in (1.0, -1.0):
        xs = -xl + s * math.sqrt(disc)
        xp = xl + xs                 # Z' 的虚部 X'（首版误加 rl，端到端测试抓住）
        if abs(xp) < 1e-15:
            continue
        z2 = rl * rl + xp * xp
        out.append(LcSolution(xs=xs, xps=-z2 / xp))
    return out


def lc_match(zl: complex, r0: float = 50.0) -> List[LcSolution]:
    """负载 zl 到 r0 的 L 网络解集（见模块 docstring）。空列表=已良好匹配。"""
    rl, xl = float(zl.real), float(zl.imag)
    if rl <= 0.5:
        raise ValueError(f"负载实部须 > 0.5Ω，收到 {rl}")
    if _vswr(rl, xl, r0) <= 1.05:
        return []
    if abs(xl) / rl > 100.0:
        raise ValueError(f"负载 Q 因子过高（|X|/R = {abs(xl)/rl:.1f} > 100）")
    if r0 / 1.05 < rl < r0 * 1.05:
        return [LcSolution(xs=-xl)]
    return _solve_hi(r0, rl, xl) if rl >= r0 else _solve_lo(r0, rl, xl)


def reactance_to_component(f_hz: float, x: float
                           ) -> Optional[Tuple[str, float, str]]:
    """电抗 -> (类型, 值, 单位)。X>0 电感(H)，X<0 电容(F)；|X|→0 返回 None。"""
    if f_hz <= 0:
        raise ValueError(f"频率须为正，收到 {f_hz}")
    if abs(x) < 1e-12:
        return None
    w = 2.0 * math.pi * f_hz
    if x > 0:
        return ("L", x / w, "H")
    return ("C", -1.0 / (w * x), "F")


#: 常见同轴电缆速度因数表（公开数据；名称大小写不敏感）。
CABLE_VF: Dict[str, float] = {
    "rg-6": 0.82, "rg-8": 0.66, "rg-8x": 0.78, "rg-11": 0.66,
    "rg-58": 0.66, "rg-58a/u": 0.66, "rg-59": 0.66, "rg-62": 0.86,
    "rg-142": 0.695, "rg-174": 0.66, "rg-178": 0.69, "rg-213": 0.66,
    "rg-214": 0.66, "rg-223": 0.66, "rg-316": 0.695, "rg-400": 0.695,
    "rg-402": 0.695, "rg-405": 0.695, "rg-55": 0.659, "rg-62a/u": 0.86,
    "lmr-100": 0.83, "lmr-195": 0.83, "lmr-200": 0.83, "lmr-240": 0.84,
    "lmr-300": 0.85, "lmr-400": 0.85, "lmr-500": 0.86, "lmr-600": 0.87,
    "ecoflex-10": 0.86, "ecoflex-15": 0.86,
    "belden-8214": 0.66, "belden-8237": 0.66, "belden-9913": 0.84,
    "belden-9914": 0.84, "times-lmr400": 0.85,
    "3.5mm-rigid": 0.93, "semirigid": 0.70,
}


def cable_velocity_factor(name: str) -> float:
    """按名称查速度因数（0..1）。未知名称显式 KeyError，不猜。"""
    key = name.strip().lower()
    if key not in CABLE_VF:
        raise KeyError(f"未知电缆型号 {name!r}（CABLE_VF 共 {len(CABLE_VF)} 条）")
    return CABLE_VF[key]
