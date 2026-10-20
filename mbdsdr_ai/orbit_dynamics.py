# SPDX-License-Identifier: MIT
"""MBDSDR AI 内核 - 轨道动力学原子积木（纯函数、可组合）
=========================================================

设计红线（AGENTS.md「原子能力、模型编排」）：本模块**只提供一块块最小
原子积木**，每块只干一件事，绝不打包成「定轨大工具」。定轨/测速/预报等
复合流程由模型现场调用这些积木自由编排——工具是积木，拼法是模型的能力。

积木清单（单位显式：m、m/s、Hz、s、rad；坐标系在 docstring 标明）：
- 动力学：two_body_dynamics_stm / propagate_state_and_stm（二体 + 6x6 变分方程 STM）
- 观测模型：pseudorange_rate_and_jacobian（伪距率 + 对 7 维状态偏导）
- 多普勒换算：observed_pseudorange_rate（观测频率 -> 伪距率）
- 估计：rls_update（参考历元非线性递推最小二乘·单步）/ validate_state
- 坐标：ecef_to_eci / eci_to_ecef / ecef_to_enu / enu_to_az_el / geodetic_to_ecef

依据：二体动力学 + 高斯变分方程（状态转移矩阵 STM），伪距率观测模型
（多普勒 = 视线方向相对速度投影 + 接收机钟漂）。WGS-72 椭球与 SGP4 一致。

依赖：numpy（必需）、scipy（STM 积分用 DOP853，与参考实现同口径）。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

try:
    from scipy.integrate import solve_ivp
    _HAS_SCIPY = True
except Exception:  # noqa: BLE001 - scipy 为可选，缺失时走纯 numpy RK4
    _HAS_SCIPY = False

# 物理常量（SI：m、m/s、rad/s）
MU_EARTH = 3.986004418e14     # m^3/s^2, 地球引力常数
OMEGA_E = 7.2921151467e-5     # rad/s, 地球自转角速度
C_LIGHT = 299792458.0         # m/s, 真空光速

# WGS-72 椭球（与 orbit.py / SGP4 内核一致，避免米级系统差）
WGS72_A = 6378135.0           # m, 赤道半径
WGS72_F = 1.0 / 298.26        # 扁率
WGS72_E2 = WGS72_F * (2.0 - WGS72_F)

# 状态各分量量纲缩放（RLS 收敛判据用）：[m,m,m,m/s,m/s,m/s,s]
STATE_SCALE = np.array([1e6, 1e6, 1e6, 1e3, 1e3, 1e3, 1e-6])


# ─────────────────────────────────────────────────────────────
# 动力学：二体 + 变分方程（6x6 状态转移矩阵 STM）
# ─────────────────────────────────────────────────────────────
def two_body_dynamics_stm(_t: float, augmented: np.ndarray) -> np.ndarray:
    """二体动力学 + 6x6 STM 微分方程右端。

    augmented: 42 维 = [r(3), v(3), stm(36, 行优先)]。
    返回同维度导数。STM 满足 dPhi/dt = A(t)·Phi，A 为二体动力学雅可比。
    """
    state = augmented[:6]
    r = state[:3]
    v = state[3:6]
    radius = float(np.linalg.norm(r))
    if radius < 6.0e6:
        raise ValueError(f"轨道半径异常: {radius:.3f} m")

    accel = -MU_EARTH * r / radius**3
    # 引力梯度 d(-mu*r/|r|^3)/dr = mu*(3 r r^T/|r|^5 - I/|r|^3)
    grav_grad = MU_EARTH * (
        3.0 * np.outer(r, r) / radius**5 - np.eye(3) / radius**3
    )
    a_mat = np.zeros((6, 6))
    a_mat[:3, 3:6] = np.eye(3)
    a_mat[3:6, :3] = grav_grad

    stm = augmented[6:].reshape(6, 6)
    return np.hstack((v, accel, (a_mat @ stm).ravel()))


def _rk4_step(f, t: float, y: np.ndarray, dt: float) -> np.ndarray:
    """经典四阶 Runge-Kutta 单步（scipy 缺失时的纯 numpy 降级路径）。"""
    k1 = f(t, y)
    k2 = f(t + dt / 2.0, y + dt / 2.0 * k1)
    k3 = f(t + dt / 2.0, y + dt / 2.0 * k2)
    k4 = f(t + dt, y + dt * k3)
    return y + dt / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def _ecef_to_eci_linear_transform(timestamp: float) -> np.ndarray:
    """ECEF -> ECI 的 6x6 线性变换（含地球自转牵连速度项）。"""
    theta = earth_rotation_angle(timestamp)
    rot = np.array([
        [math.cos(theta), -math.sin(theta), 0.0],
        [math.sin(theta), math.cos(theta), 0.0],
        [0.0, 0.0, 1.0],
    ])
    omega_x = np.array([
        [0.0, -OMEGA_E, 0.0],
        [OMEGA_E, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ])
    tf = np.zeros((6, 6))
    tf[:3, :3] = rot
    tf[3:6, :3] = rot @ omega_x
    tf[3:6, 3:6] = rot
    return tf


def _eci_to_ecef_linear_transform(timestamp: float) -> np.ndarray:
    """ECI -> ECEF 的 6x6 线性变换。"""
    theta = earth_rotation_angle(timestamp)
    rot = np.array([
        [math.cos(theta), math.sin(theta), 0.0],
        [-math.sin(theta), math.cos(theta), 0.0],
        [0.0, 0.0, 1.0],
    ])
    omega_x = np.array([
        [0.0, -OMEGA_E, 0.0],
        [OMEGA_E, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ])
    tf = np.zeros((6, 6))
    tf[:3, :3] = rot
    tf[3:6, :3] = -omega_x @ rot
    tf[3:6, 3:6] = rot
    return tf


def propagate_state_and_stm(
    state_ecef: np.ndarray,
    t_from: float,
    t_to: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """从 t_from 传播 ECEF 状态到 t_to，返回 (末状态, 6x6 STM)。

    state_ecef: 7 维 [x,y,z,vx,vy,vz, clock_drift]（SI）。
    STM 为两历元间状态转移矩阵（对 6 维动力学状态；clock_drift 不变）。
    动力学在 ECI 下积分（二体 + STM），再转回 ECEF。
    """
    state_ecef = np.asarray(state_ecef, dtype=float)
    dt = float(t_to - t_from)
    if abs(dt) < 1e-12:
        return state_ecef.copy(), np.eye(7)

    pos_eci, vel_eci = ecef_to_eci(state_ecef[:3], state_ecef[3:6], t_from)
    y0_eci = np.hstack((pos_eci, vel_eci))
    aug0 = np.hstack((y0_eci, np.eye(6).ravel()))

    if _HAS_SCIPY:
        atol = np.hstack(([1e-3] * 3, [1e-6] * 3, [1e-11] * 36))
        sol = solve_ivp(
            two_body_dynamics_stm, [0.0, dt], aug0,
            method="DOP853", rtol=1e-10, atol=atol,
        )
        if not sol.success:
            raise RuntimeError(sol.message)
        aug_end = sol.y[:, -1]
    else:
        # 纯 numpy RK4 降级：定步长（<=5s 或 200 段取小），精度足够短弧
        n = max(1, int(abs(dt) / 5.0), 200 if abs(dt) > 1000 else 1)
        h = dt / n
        aug_end = aug0
        t = 0.0
        for _ in range(n):
            aug_end = _rk4_step(two_body_dynamics_stm, t, aug_end, h)
            t += h

    final_eci = aug_end[:6]
    stm_eci = aug_end[6:].reshape(6, 6)

    pos_ecef, vel_ecef = eci_to_ecef(final_eci[:3], final_eci[3:6], t_to)
    propagated = np.hstack((pos_ecef, vel_ecef, state_ecef[6]))

    stm_ecef6 = (
        _eci_to_ecef_linear_transform(t_to)
        @ stm_eci
        @ _ecef_to_eci_linear_transform(t_from)
    )
    stm_ecef = np.eye(7)
    stm_ecef[:6, :6] = stm_ecef6
    return propagated, stm_ecef


# ─────────────────────────────────────────────────────────────
# 观测模型：伪距率（多普勒）+ 雅可比
# ─────────────────────────────────────────────────────────────
def pseudorange_rate_and_jacobian(
    sat_state_ecef: np.ndarray,
    receiver_ecef: np.ndarray,
) -> Tuple[float, np.ndarray]:
    """伪距率观测模型：rate = los·v + c·clock_drift。

    sat_state_ecef: 7 维 ECEF 卫星状态（含 clock_drift）。
    receiver_ecef: 3 维 ECEF 接收机位置。
    返回 (伪距率 m/s, 对 7 维状态的 1x7 雅可比)。
    """
    state = np.asarray(sat_state_ecef, dtype=float)
    rec = np.asarray(receiver_ecef, dtype=float)
    rel = state[:3] - rec
    dist = float(np.linalg.norm(rel))
    if dist < 1.0:
        raise ValueError("卫星与接收机距离异常")

    los = rel / dist
    estimate = float(los @ state[3:6] + C_LIGHT * state[6])
    jac = np.zeros(7)
    jac[:3] = ((np.eye(3) - np.outer(los, los)) @ state[3:6]) / dist
    jac[3:6] = los
    jac[6] = C_LIGHT
    return estimate, jac


def observed_pseudorange_rate(frequency: float, center_frequency: float) -> float:
    """观测多普勒频率 -> 伪距率（m/s）。

    range_rate = -c * (f_obs - f_center) / f_center（远离为正）。
    """
    return -C_LIGHT * (float(frequency) - float(center_frequency)) / float(center_frequency)


def range_rate_to_doppler_hz(range_rate: float, center_frequency: float) -> float:
    """伪距率(m/s) -> 多普勒频移(Hz)（observed_pseudorange_rate 的逆）。"""
    return -float(range_rate) * float(center_frequency) / C_LIGHT


def tle_state_ecef(tle_line1: str, tle_line2: str, timestamp: float) -> np.ndarray:
    """SGP4 传播 TLE -> ECEF 状态（7 维 [x,y,z,vx,vy,vz,0]，SI）。

    TEME -> ECEF 用 GMST 旋转（与本模块 ecef_to_eci/eci_to_ecef 同一套
    earth_rotation_angle，保证先验与传播口径自洽）。clock_drift 置 0
    （先验无钟漂信息，由估计器标定）。
    """
    from sgp4.api import Satrec  # 延迟导入：纯动力学路径不强依赖 sgp4

    sat = Satrec.twoline2rv(str(tle_line1), str(tle_line2))
    jd = timestamp / 86400.0 + 2440587.5
    jdi = int(jd)
    jdf = jd - jdi
    err, r_teme, v_teme = sat.sgp4(jdi, jdf)  # km, km/s
    if err != 0:
        raise ValueError(f"SGP4 传播失败，错误码 {err}")

    theta = earth_rotation_angle(timestamp)
    cg, sg = math.cos(-theta), math.sin(-theta)
    r_teme = np.asarray(r_teme, dtype=float) * 1e3   # -> m
    v_teme = np.asarray(v_teme, dtype=float) * 1e3   # -> m/s
    rx = cg * r_teme[0] - sg * r_teme[1]
    ry = sg * r_teme[0] + cg * r_teme[1]
    rz = r_teme[2]
    vx = cg * v_teme[0] - sg * v_teme[1]
    vy = sg * v_teme[0] + cg * v_teme[1]
    vz = v_teme[2]
    # 牵连速度：v_ECEF = R·v_TEME − Ω×r（Ω×r = ω(−y, x, 0)）
    vx += OMEGA_E * ry
    vy += -OMEGA_E * rx
    return np.array([rx, ry, rz, vx, vy, vz, 0.0])


def calibrate_time_offset(
    rows,
    receiver_ecef,
    center_frequency: float,
    tle_line1: str,
    tle_line2: str,
    search_limit: float = 30.0,
) -> dict:
    """估计整段观测的固定时间偏差与伪距率偏差（原子校时积木）。

    rows: 可迭代 (timestamp, frequency)。不校正时间错位时，RLS 会把它
    误解释为沿轨位置误差而偏离轨道。粗扫 1s + 细扫 0.1s 最小化去偏
    后的伪距率残差 RMS。

    返回 dict：time_offset_s / rate_bias_mps / centered_rms_mps。
    """
    rec = np.asarray(receiver_ecef, dtype=float)
    row_list = [(float(t), float(f)) for t, f in rows]

    def _eval(offset: float):
        residuals = []
        for ts, freq in row_list:
            st = tle_state_ecef(tle_line1, tle_line2, ts + offset)
            pred, _ = pseudorange_rate_and_jacobian(st, rec)
            residuals.append(observed_pseudorange_rate(freq, center_frequency) - pred)
        r = np.asarray(residuals, dtype=float)
        bias = float(np.mean(r))
        centered = float(np.sqrt(np.mean(np.square(r - bias))))
        return centered, bias

    coarse = np.arange(-search_limit, search_limit + 0.5, 1.0)
    best_c = float(coarse[int(np.argmin([_eval(o)[0] for o in coarse]))])
    fine = np.arange(best_c - 1.0, best_c + 1.0001, 0.1)
    results = [_eval(o) for o in fine]
    idx = int(np.argmin([x[0] for x in results]))
    return {
        "time_offset_s": float(fine[idx]),
        "rate_bias_mps": results[idx][1],
        "centered_rms_mps": results[idx][0],
    }


# ─────────────────────────────────────────────────────────────
# 估计：参考历元非线性递推最小二乘（单步）
# ─────────────────────────────────────────────────────────────
def rls_update(
    ref_state_prior: np.ndarray,
    ref_cov_prior: np.ndarray,
    ref_timestamp: float,
    obs_timestamp: float,
    observation: float,
    receiver_ecef: np.ndarray,
    observation_sigma: float,
    max_iterations: int = 8,
) -> dict:
    """参考历元 RLS 单步更新（遗忘因子 1、过程噪声 0，协方差单调下降）。

    所有法方程累积在同一参考历元。返回 dict：
      ref_state / ref_cov（更新后参考历元）、state / stm（观测历元）、
      innovation / normalized_innovation / postfit_residual。
    """
    meas_var = float(observation_sigma) ** 2
    prior_state, prior_stm = propagate_state_and_stm(
        ref_state_prior, ref_timestamp, obs_timestamp
    )
    prior_est, prior_jac = pseudorange_rate_and_jacobian(prior_state, receiver_ecef)
    prior_ref_jac = prior_jac @ prior_stm
    innovation = float(observation - prior_est)
    cov_h = ref_cov_prior @ prior_ref_jac
    innov_var = float(prior_ref_jac @ cov_h + meas_var)
    if not np.isfinite(innov_var) or innov_var <= 0.0:
        raise ValueError(f"新息方差异常: {innov_var}")

    ref_iter = ref_state_prior.copy()
    for _ in range(max_iterations):
        cur_state, stm = propagate_state_and_stm(ref_iter, ref_timestamp, obs_timestamp)
        est, cur_jac = pseudorange_rate_and_jacobian(cur_state, receiver_ecef)
        ref_jac = cur_jac @ stm
        cov_h = ref_cov_prior @ ref_jac
        innov_var = float(ref_jac @ cov_h + meas_var)
        gain = cov_h / innov_var
        iter_innov = float(
            observation - est + ref_jac @ (ref_iter - ref_state_prior)
        )
        ref_next = ref_state_prior + gain * iter_innov
        scaled = float(np.max(np.abs((ref_next - ref_iter) / STATE_SCALE)))
        ref_iter = ref_next
        if scaled < 1e-8:
            break

    ref_post = ref_iter
    cur_post, post_stm = propagate_state_and_stm(ref_post, ref_timestamp, obs_timestamp)
    post_est, post_jac = pseudorange_rate_and_jacobian(cur_post, receiver_ecef)
    ref_jac = post_jac @ post_stm
    cov_h = ref_cov_prior @ ref_jac
    innov_var = float(ref_jac @ cov_h + meas_var)
    gain = cov_h / innov_var
    ikh = np.eye(7) - np.outer(gain, ref_jac)
    cov_post = ikh @ ref_cov_prior @ ikh.T + meas_var * np.outer(gain, gain)
    cov_post = 0.5 * (cov_post + cov_post.T)

    return {
        "ref_state": ref_post,
        "ref_cov": cov_post,
        "state": cur_post,
        "stm": post_stm,
        "innovation": innovation,
        "normalized_innovation": innovation / math.sqrt(innov_var),
        "postfit_residual": float(observation - post_est),
    }


def validate_state(state: np.ndarray) -> None:
    """轨道状态合理性检查（LEO 量级），异常抛 ValueError。"""
    radius = float(np.linalg.norm(state[:3]))
    speed = float(np.linalg.norm(state[3:6]))
    if not (6.0e6 < radius < 9.0e6):
        raise ValueError(f"轨道半径异常: {radius:.3f} m")
    if not (4.0e3 < speed < 10.0e3):
        raise ValueError(f"轨道速度异常: {speed:.3f} m/s")


# ─────────────────────────────────────────────────────────────
# 坐标转换原子积木
# ─────────────────────────────────────────────────────────────
def earth_rotation_angle(timestamp: float) -> float:
    """UTC unix 秒 -> 地球自转角（rad，GMST 近似）。"""
    jd = timestamp / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * t * t - t**3 / 38710000.0)
    return math.radians(gmst % 360.0)


def ecef_to_eci(pos_ecef, vel_ecef, timestamp: float):
    """ECEF -> ECI（位置 m、速度 m/s）。"""
    theta = earth_rotation_angle(timestamp)
    rot = np.array([
        [math.cos(theta), -math.sin(theta), 0.0],
        [math.sin(theta), math.cos(theta), 0.0],
        [0.0, 0.0, 1.0],
    ])
    p = np.asarray(pos_ecef, dtype=float)
    v = np.asarray(vel_ecef, dtype=float)
    omega = np.array([0.0, 0.0, OMEGA_E])
    return rot @ p, rot @ (v + np.cross(omega, p))


def eci_to_ecef(pos_eci, vel_eci, timestamp: float):
    """ECI -> ECEF（位置 m、速度 m/s）。"""
    theta = earth_rotation_angle(timestamp)
    rot = np.array([
        [math.cos(theta), math.sin(theta), 0.0],
        [-math.sin(theta), math.cos(theta), 0.0],
        [0.0, 0.0, 1.0],
    ])
    p = np.asarray(pos_eci, dtype=float)
    v = np.asarray(vel_eci, dtype=float)
    omega = np.array([0.0, 0.0, OMEGA_E])
    return rot @ p, rot @ v - np.cross(omega, rot @ p)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    """WGS-72 大地坐标(°,°,m) -> ECEF(m)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = WGS72_A / math.sqrt(1.0 - WGS72_E2 * sin_lat * sin_lat)
    return np.array([
        (n + alt_m) * cos_lat * math.cos(lon),
        (n + alt_m) * cos_lat * math.sin(lon),
        (n * (1.0 - WGS72_E2) + alt_m) * sin_lat,
    ])


def ecef_to_enu(target_ecef, receiver_ecef) -> np.ndarray:
    """ECEF 相对向量 -> 站心 ENU（东/北/天，m）。"""
    rec = np.asarray(receiver_ecef, dtype=float)
    tgt = np.asarray(target_ecef, dtype=float)
    lon, lat, _ = ecef_to_geodetic(rec)
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    d = tgt - rec
    rot = np.array([
        [-math.sin(lon_r), math.cos(lon_r), 0.0],
        [-math.sin(lat_r) * math.cos(lon_r), -math.sin(lat_r) * math.sin(lon_r), math.cos(lat_r)],
        [math.cos(lat_r) * math.cos(lon_r), math.cos(lat_r) * math.sin(lon_r), math.sin(lat_r)],
    ])
    return rot @ d


def ecef_to_geodetic(pos_ecef) -> Tuple[float, float, float]:
    """ECEF(m) -> (经度°, 纬度°, 高度 m)。WGS-72，迭代法。"""
    x, y, z = (float(v) for v in np.asarray(pos_ecef, dtype=float)[:3])
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - WGS72_E2))
    for _ in range(6):
        sin_lat = math.sin(lat)
        n = WGS72_A / math.sqrt(1.0 - WGS72_E2 * sin_lat * sin_lat)
        alt = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - WGS72_E2 * n / (n + alt)))
    sin_lat = math.sin(lat)
    n = WGS72_A / math.sqrt(1.0 - WGS72_E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - n
    return math.degrees(lon), math.degrees(lat), alt


def enu_to_az_el(enu) -> Tuple[float, float]:
    """站心 ENU -> (方位角°, 仰角°)。方位自北顺时针。"""
    e, n, u = (float(v) for v in np.asarray(enu, dtype=float)[:3])
    az = math.degrees(math.atan2(e, n)) % 360.0
    el = math.degrees(math.atan2(u, math.hypot(e, n)))
    return az, el
