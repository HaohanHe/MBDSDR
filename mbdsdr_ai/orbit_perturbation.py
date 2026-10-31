# SPDX-License-Identifier: MIT
"""P1-4 定轨摄动与初轨确定原子工具。

能力拆解（218 条）P1-4 的两个原子能力：

  dynamics_stm_j2 / propagate_state_and_stm_j2 —— ECI 系二体+J2 动力学
    及 6x6 状态转移矩阵（STM）一体化积分。J2 是 LEO 主导摄动（sgp4
    亦含）；不加 J2 时 30 min 相对真值漂移 ~9 km（orbit_determination
    实测结论）。ECI 积分避免旋转系离心/Coriolis 项（传播 600 s 可差
    1388 km，orbit_determination 注释实测）。

  initial_orbit_laplace —— 纯测角（RA/Dec）初轨确定（Laplace 法，
    二体假设）。由三次站心观测的方向矢量二阶导数与测站运动学
    直接解算 t2 时刻斜距的多根方程，几何多解如实全部上报。

单位约定：km、km/s、s（与 orbit_dynamics / orbit_determination 全链一致）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .orbit_dynamics import (
    MU_EARTH,
    OMEGA_E,
    _rk4_step,
    _HAS_SCIPY,
)
from .orbit_determination import (
    J2,
    WGS84_RE,
    geodetic_to_ecef,
    gmst_rad,
    jd_from_unix,
)

if _HAS_SCIPY:
    from scipy.integrate import solve_ivp

__all__ = [
    "j2_accel_eci",
    "dynamics_stm_j2",
    "propagate_state_and_stm_j2",
    "initial_orbit_laplace",
    "register_tool_registry",
]


# ─────────────────────────────────────────────────────────────
# ECI 二体 + J2 动力学与 STM
# ─────────────────────────────────────────────────────────────
def j2_accel_eci(r: np.ndarray) -> np.ndarray:
    """ECI 系 J2 摄动加速度（标准矢量形式，Vallado 口径）。

    a_J2 = -1.5·J2·μ·Re²/r⁵ · [x(1-5u), y(1-5u), z(3-5u)]，
    其中 u = z²/r²。LEO 主导摄动项；更高阶带谐/ tesseral 与大气阻力、
    光压不在本工具口径内（诚实边界，见模块 docstring）。
    """
    r = np.asarray(r, dtype=float)
    rmag = float(np.linalg.norm(r))
    if rmag < 6.0e3:
        raise ValueError(f"轨道半径异常: {rmag:.3f} km")
    z = r[2]
    u = z * z / (rmag * rmag)
    k = -1.5 * J2 * MU_EARTH * WGS84_RE ** 2 / rmag ** 5
    return np.array([
        k * r[0] * (1.0 - 5.0 * u),
        k * r[1] * (1.0 - 5.0 * u),
        k * r[2] * (3.0 - 5.0 * u),
    ])


def _ecij2_eom6(s6: np.ndarray) -> np.ndarray:
    """ECI 二体+J2 六维方程右端 ds/dt = [v, -μr/r³ + a_J2]。"""
    out = np.empty(6)
    r = s6[0:3]
    rmag = float(np.linalg.norm(r))
    if rmag < 6.0e3:
        raise ValueError(f"轨道半径异常: {rmag:.3f} km")
    out[0:3] = s6[3:6]
    out[3:6] = -MU_EARTH / rmag ** 3 * r + j2_accel_eci(r)
    return out


def _ecij2_A_matrix(s6: np.ndarray) -> np.ndarray:
    """6x6 系统雅可比 A = ∂f/∂s，中心差分（与 orbit_determination
    `_A_matrix` 同一建立方法：自动含 J2 非线性耦合，无解析求导风险）。"""
    A = np.zeros((6, 6))
    f0 = _ecij2_eom6(s6)
    for j in range(6):
        h = 1e-6 * (1.0 + abs(s6[j]))
        dp = s6.copy()
        dm = s6.copy()
        dp[j] += h
        dm[j] -= h
        A[:, j] = (_ecij2_eom6(dp) - _ecij2_eom6(dm)) / (2.0 * h)
    return A


def dynamics_stm_j2(_t: float, augmented: np.ndarray) -> np.ndarray:
    """ECI 二体+J2 动力学 + 6x6 STM 变分方程右端。

    augmented: 42 维 = [r(3), v(3), stm(36, 行优先)]。
    STM 满足 dPhi/dt = A(t)·Phi，A 为中心差分雅可比（含 J2 耦合）。
    """
    state = augmented[:6]
    Phi = augmented[6:].reshape(6, 6)
    ds = _ecij2_eom6(state)
    dPhi = _ecij2_A_matrix(state) @ Phi
    return np.hstack((ds, dPhi.ravel()))


def propagate_state_and_stm_j2(
    state_eci: np.ndarray,
    dt_s: float,
    max_step_s: float = 5.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """ECI 状态传播 dt_s 秒（二体+J2），返回 (末状态, 6x6 STM)。

    state_eci: [x,y,z,vx,vy,vz]（km, km/s）。
    STM 为对初始状态的偏导 ∂(r,v)(t+dt)/∂(r,v)(t)。
    """
    y0 = np.asarray(state_eci, dtype=float)
    if y0.shape != (6,):
        raise ValueError(f"状态须为 6 维 [r(3), v(3)]，收到 {y0.shape}")
    if not np.all(np.isfinite(y0)):
        raise ValueError("状态含非有限值")
    dt = float(dt_s)
    aug0 = np.hstack((y0, np.eye(6).ravel()))
    if abs(dt) < 1e-12:
        return y0.copy(), np.eye(6)
    if _HAS_SCIPY:
        atol = np.hstack(([1e-3] * 3, [1e-6] * 3, [1e-11] * 36))
        sol = solve_ivp(
            dynamics_stm_j2, [0.0, dt], aug0,
            method="DOP853", rtol=1e-10, atol=atol,
        )
        if not sol.success:
            raise RuntimeError(sol.message)
        aug_end = sol.y[:, -1]
    else:
        n = max(1, int(abs(dt) / max_step_s))
        h = dt / n
        aug_end = aug0
        t = 0.0
        for _ in range(n):
            aug_end = _rk4_step(dynamics_stm_j2, t, aug_end, h)
            t += h
    return aug_end[:6], aug_end[6:].reshape(6, 6)


# ─────────────────────────────────────────────────────────────
# Laplace 初轨确定（纯测角，二体假设）
# ─────────────────────────────────────────────────────────────
def _station_eci(lat_deg: float, lon_deg: float, alt_km: float,
                 unix_s: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """测站 ECEF 位置（由大地坐标）→ ECI 位置/速度/加速度。

    速度/加速度来自地球自转牵连运动：
      Ṙ = Ω×R，R̈ = Ω×(Ω×R)（Ω 常量，无欧拉项）。
    """
    r_ecef = np.asarray(geodetic_to_ecef(lat_deg, lon_deg, alt_km), dtype=float)
    theta = gmst_rad(jd_from_unix(unix_s))
    c, s = math.cos(theta), math.sin(theta)
    rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    r = rot @ r_ecef
    om = np.array([0.0, 0.0, OMEGA_E])
    v = np.cross(om, r)
    a = np.cross(om, v)
    return r, v, a


def _triple(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """标量三重积 (a, b, c) = a·(b×c)。"""
    return float(a @ np.cross(b, c))


def _anglediff_rate(L: np.ndarray, Ld: np.ndarray, Ldd: np.ndarray,
                    R: np.ndarray, Rd: np.ndarray, Rdd: np.ndarray,
                    ) -> Tuple[float, float, float]:
    """Laplace 斜距/斜距率公式系数（Vallado Eq. 7-40 ~ 7-44 口径）。

    运动方程 r = R + ρL 代入 r̈ = -μr/r³，与 L̇/L̈ 叉乘消去
    ρ̈ 得 ρ(ρ) = -(μ·D1/r³ + D2)/D3；再与 L̈ 组合得 ρ̇。
    返回 (D1, D2, D3_rho, D4_rhod) 所需的全部系数打包：
      (coef_r3, const_rho, coef_rhod, const_rhod)
    """
    D1 = _triple(R, L, Ld)      # (R, L, L̇)
    D2 = _triple(Rdd, L, Ld)    # (R̈, L, L̇)
    D3 = _triple(Ldd, L, Ld)    # (L̈, L, L̇)
    D4 = _triple(R, Ldd, L)     # (R, L̈, L)
    D5 = _triple(Rdd, Ldd, L)   # (R̈, L̈, L)
    D6 = _triple(Ld, Ldd, L)    # (L̇, L̈, L)
    if abs(D3) < 1e-30:
        raise ValueError("Laplace 系数 D3=(L̈,L,L̇) 退化——三次观测近乎共线，"
                         "几何不足以定轨（请拉大观测时间间隔）")
    # ρ = -(μ D1/r³ + D2)/D3
    coef_r3 = -MU_EARTH * D1 / D3
    const_rho = -D2 / D3
    # ρ̇ = -(μ D4/r³ + D5)/(2 D6)
    coef_rhod = -MU_EARTH * D4 / (2.0 * D6) if abs(D6) > 1e-30 else 0.0
    const_rhod = -D5 / (2.0 * D6) if abs(D6) > 1e-30 else 0.0
    return coef_r3, const_rho, coef_rhod, const_rhod


def initial_orbit_laplace(
    times_unix_s: Sequence[float],
    ra_deg: Sequence[float],
    dec_deg: Sequence[float],
    station_lat_deg: float,
    station_lon_deg: float,
    station_alt_km: float,
) -> Dict[str, Any]:
    """Laplace 初轨确定：三次站心 RA/Dec → t2 时刻 ECI 状态。

    times_unix_s / ra_deg / dec_deg : 三次观测（ Unix 秒，J2000 赤道坐标
                                      赤经/赤纬，度）；
    station_*                        : 测站大地坐标（度，km）。

    方法：三点差分得 t2 方向矢量导数 L̇, L̈；测站 ECI 运动学 R, Ṙ, R̈；
    由 r = R + ρL 与 r̈ = -μr/r³ 叉乘消元得 ρ = f(r)；代入
    r² = R² + 2ρ(R·L) + ρ² 解标量多根方程，取 ρ>0 的全部实根如实上报，
    默认主解为半径最小根。速度 v = Ṙ + ρ̇L + ρL̇。

    返回：state_eci（主解 [r,v]）/ solutions（全部 ρ>0 根列表）/
    rms_angle_arcsec（观测方向与主解反算方向的三次残差 RMS）/
    t_mid_unix_s。二体假设：短弧内 J2 引入的模型误差进入残差如实可见。
    """
    if not (len(times_unix_s) == len(ra_deg) == len(dec_deg) == 3):
        raise ValueError("Laplace 法须恰好 3 次观测")
    t = [float(v) for v in times_unix_s]
    if not (t[0] < t[1] < t[2]):
        raise ValueError("观测时刻须严格递增")
    dt1 = t[0] - t[1]
    dt3 = t[2] - t[1]
    if min(abs(dt1), abs(dt3)) < 1.0:
        raise ValueError("观测间隔须 ≥1 s（差分导数稳定下限）")

    # 方向矢量 ECI（J2000 ≈ 历元平赤道，工程口径忽略岁差章动，与 gmst 一致）
    los: List[np.ndarray] = []
    for ra, de in zip(ra_deg, dec_deg):
        ra_r = math.radians(float(ra))
        de_r = math.radians(float(de))
        los.append(np.array([
            math.cos(de_r) * math.cos(ra_r),
            math.cos(de_r) * math.sin(ra_r),
            math.sin(de_r),
        ]))
    # 三点 Lagrange 插值在**中点 t2** 的导数（非等间隔）：
    # L̇ = Σ L_i·ℓ_i'(t2)，L̈ = 2·Σ L_i / Π_{j≠i}(t_i − t_j)
    #   ℓ_0'(t2) = (t2−t3? 否)——按基函数逐次求导：
    #   ℓ_0'(t2) = −dt3 / (dt1·(t0−t2))
    #   ℓ_1'(t2) = −(dt1+dt3) / (dt1·dt3)
    #   ℓ_2'(t2) = −dt1 / ((t2−t0)·dt3)
    # 等间隔校验：ℓ' = [−1, 0, +1]/(2h)（中心差分），ℓ'' = [1,−2,1]/h²。
    L2 = los[1]
    Ld = (los[0] * (-dt3 / (dt1 * (t[0] - t[2])))
          + L2 * (-(dt1 + dt3) / (dt1 * dt3))
          + los[2] * (-dt1 / ((t[2] - t[0]) * dt3)))
    Ldd = 2.0 * (los[0] / (dt1 * (t[0] - t[2]))
                 + L2 / (dt1 * dt3)
                 + los[2] / ((t[2] - t[0]) * dt3))

    R2, Rd2, Rdd2 = _station_eci(station_lat_deg, station_lon_deg,
                                 station_alt_km, t[1])

    coef_r3, const_rho, coef_rhod, const_rhod = _anglediff_rate(
        L2, Ld, Ldd, R2, Rd2, Rdd2)

    def rho_of(r: float) -> float:
        return coef_r3 / r ** 3 + const_rho

    def residual(r: float) -> float:
        rho = rho_of(r)
        return (R2 @ R2 + 2.0 * rho * (R2 @ L2) + rho * rho) - r * r

    # 半径网格扫描 + 二分求全部实根（ρ>0 约束）
    r_lo = max(6.4e3, float(np.linalg.norm(R2)) * 0.5)
    r_hi = 5.0e5
    n_scan = 4000
    grid = np.linspace(r_lo, r_hi, n_scan)
    vals = np.array([residual(r) for r in grid])
    roots: List[float] = []
    for i in range(n_scan - 1):
        if not (np.isfinite(vals[i]) and np.isfinite(vals[i + 1])):
            continue
        if vals[i] == 0.0:
            roots.append(float(grid[i]))
        elif vals[i] * vals[i + 1] < 0.0:
            a, b = float(grid[i]), float(grid[i + 1])
            for _ in range(80):
                m = 0.5 * (a + b)
                if residual(a) * residual(m) <= 0.0:
                    b = m
                else:
                    a = m
            roots.append(0.5 * (a + b))
    solutions: List[Dict[str, Any]] = []
    seen: List[float] = []
    for r in roots:
        rho = rho_of(r)
        if rho <= 0.0 or r <= 0.0:
            continue
        if any(abs(r - s) < 1.0 for s in seen):
            continue
        seen.append(r)
        rho_dot = coef_rhod / r ** 3 + const_rhod
        state = np.hstack((R2 + rho * L2,
                           Rd2 + rho_dot * L2 + rho * Ld))
        solutions.append({
            "range_km": rho,
            "radius_km": r,
            "state_eci": state.tolist(),
        })
    if not solutions:
        raise ValueError("Laplace 方程无 ρ>0 实根——观测几何/精度不足以定轨")

    # 主解：半径最小（近地根）；残差 = 主解状态反算三次观测方向之差
    solutions.sort(key=lambda d: d["radius_km"])
    primary = solutions[0]
    state = np.array(primary["state_eci"], dtype=float)

    def ang_residuals(s6: np.ndarray) -> np.ndarray:
        res = []
        for ti, Lobs in zip(t, los):
            Rt, _, _ = _station_eci(station_lat_deg, station_lon_deg,
                                    station_alt_km, ti)
            # 二体传播回观测历元（J2 模型误差在此显式，不隐藏）
            prop, _ = propagate_state_and_stm_j2(s6, ti - t[1])
            dvec = prop[:3] - Rt
            d = dvec / np.linalg.norm(dvec)
            res.append(math.degrees(math.acos(
                min(1.0, max(-1.0, float(d @ Lobs))))))
        return np.array(res)

    res_deg = ang_residuals(state)
    rms_arcsec = float(np.sqrt(np.mean((res_deg * 3600.0) ** 2)))
    return {
        "state_eci": primary["state_eci"],
        "range_km": primary["range_km"],
        "radius_km": primary["radius_km"],
        "n_solutions": len(solutions),
        "solutions": solutions,
        "rms_angle_arcsec": round(rms_arcsec, 3),
        "t_mid_unix_s": t[1],
        "assumption": "two_body_j2_residuals_visible",
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-4 摄动 STM 传播 / Laplace 初轨确定积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _prop(args):
        try:
            state = [float(v) for v in args.get("state_eci", [])]
            res_state, stm = propagate_state_and_stm_j2(
                state, float(args.get("dt_s", 0.0)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"J2 STM 传播失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(
                {"state_eci": res_state.tolist(), "stm": stm.tolist()},
                ensure_ascii=False),
            data={"state_eci": res_state.tolist(), "stm": stm.tolist()})

    def _laplace(args):
        try:
            res = initial_orbit_laplace(
                args.get("times_unix_s", []),
                args.get("ra_deg", []),
                args.get("dec_deg", []),
                float(args.get("station_lat_deg", 0.0)),
                float(args.get("station_lon_deg", 0.0)),
                float(args.get("station_alt_km", 0.0)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"Laplace 初轨确定失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False),
            data=res)

    registry.register(
        name="orbit_propagate_stm_j2",
        description=(
            "ECI 二体+J2 状态传播 + 6x6 STM（变分方程一体化积分，"
            "J2 为 LEO 主导摄动；不含大气阻力/光压/高阶带谐，诚实边界）。"),
        parameters={
            "type": "object",
            "properties": {
                "state_eci": {"type": "array", "items": {"type": "number"},
                              "description": "[x,y,z,vx,vy,vz] km, km/s"},
                "dt_s": {"type": "number", "description": "传播秒数（可负）"},
            },
            "required": ["state_eci", "dt_s"],
        },
        handler=_prop,
        category="orbit")

    registry.register(
        name="initial_orbit_laplace",
        description=(
            "Laplace 纯测角初轨确定：3 次站心 RA/Dec → t2 ECI 状态。"
            "多解如实上报（n_solutions），残差 RMS 角秒可见；"
            "二体假设，J2 模型误差残差内不隐藏。"),
        parameters={
            "type": "object",
            "properties": {
                "times_unix_s": {"type": "array",
                                 "items": {"type": "number"}},
                "ra_deg": {"type": "array", "items": {"type": "number"}},
                "dec_deg": {"type": "array", "items": {"type": "number"}},
                "station_lat_deg": {"type": "number", "default": 0.0},
                "station_lon_deg": {"type": "number", "default": 0.0},
                "station_alt_km": {"type": "number", "default": 0.0},
            },
            "required": ["times_unix_s", "ra_deg", "dec_deg"],
        },
        handler=_laplace,
        category="orbit")
