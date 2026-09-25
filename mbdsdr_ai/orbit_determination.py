"""
MBDSDR AI 内核 - 深空追迹多普勒定轨系统
==========================================
基于多普勒频偏（伪距率）的卫星轨道确定，对标课件 Demo 算法：

  信号处理前端  detect_doppler：FFT 找信号峰值 -> 相对频偏 fd 与 SNR；支持滑窗批处理。
  观测模型      pseudorange_rate_and_jacobian：伪距率 ρdot = u·(v_sat - v_rx) + 钟差项，
                返回预测 ρdot 与 7 维雅可比。
  动力学/坐标   propagate_ecef_state_and_stm：ECEF 二体传播（含 Coriolis/离心力）+
                6x6 状态转移矩阵 STM 的 RK4 数值积分；ECEF<->ECI（GMST，速度含 Coriolis）。
  估计器        EKF（扩展卡尔曼）与 reference_epoch_rls_update（参考历元递推最小二乘）。
  参考轨道      LEO 用 sgp4/TLE；LRO 用 skyfield 月球星历（离线回退 Meeus 解析月球理论）
                + 简化二体绕月模型（~50km 高度，~113min 周期）。
  可视化        位置误差曲线 / 残差统计 / 方位仰角天空图（matplotlib 存 PNG）。

状态向量（7 维）：[x, y, z, vx, vy, vz, b]
  - 位置 km（ECEF），速度 km/s（ECEF）；
  - b 为接收机钟差漂移对应的等效伪距率偏置（km/s，即 d(c·t_bias)/dt）。多普勒（伪距率）
    观测量对常数钟差 t_bias 不敏感，只对其时间导数（钟漂）敏感，故第 7 维估计钟漂——
    这正是往返测试中“钟差漂移”被吸收、残差均值归零的原因。

物理常数：
  c            = 299792458 m/s
  f0 (LRO S 波段下行) = 2271e6 Hz
  GM_EARTH     = 398600.4418 km^3/s^2
  omega_earth  = 7.292115e-5 rad/s

License: GPL-3.0
"""
from __future__ import annotations

import math
import os
from typing import List, Tuple, Dict, Any, Optional

import numpy as np

# 课件/物理常数（统一 km-s 单位制）
C_LIGHT_MS = 299792458.0          # m/s
C_LIGHT_KMS = 299792.458          # km/s
F0_DEFAULT_HZ = 2271.0e6          # LRO S 波段下行标称频率
GM_EARTH = 398600.4418            # km^3/s^2
OMEGA_E = 7.292115e-5             # rad/s 地球自转角速度
WGS84_A = 6378.137                # km 赤道半径
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)

# 月球常数（LRO 参考轨道用）
GM_MOON = 4902.8005                # km^3/s^2
MOON_RADIUS = 1737.4               # km
LRO_ORBIT_ALT = 50.0               # km（任务书 ~50km）
LRO_PERIOD_S = 113.0 * 60.0        # ~113 min

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


# ═══════════════════════════════════════════════════════
# 1. 坐标变换（ECEF / ECI / ENU / LLA）
# ═══════════════════════════════════════════════════════

def gmst_rad(jd_ut1: float) -> float:
    """Greenwich 平恒星时（弧度），IAU1982 折叠式。jd_ut1 为儒略日。"""
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    gmst = math.radians((gmst_sec % 86400.0) / 240.0)
    return gmst % (2.0 * math.pi)


def jd_from_unix(unix_s: float) -> float:
    """unix 秒 -> UTC 儒略日。"""
    return unix_s / 86400.0 + 2440587.5


def ecef_to_eci(pos_ecef: np.ndarray, vel_ecef: np.ndarray,
                jd: float) -> Tuple[np.ndarray, np.ndarray]:
    """ECEF -> ECI（绕 z 轴转 +GMST）。速度含 Coriolis：v_ECI = R·v_ECEF + Ω×r。

    逆变换 eci_to_ecef 为其严格逆，二者往返误差量级 1e-9 km（见测试 3）。
    """
    th = gmst_rad(jd)
    c, s = math.cos(th), math.sin(th)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    pos_eci = R @ np.asarray(pos_ecef, dtype=float)
    vel_ecef = np.asarray(vel_ecef, dtype=float)
    # Ω×r_ECEF（在 ECEF 分量下）：Ω=(0,0,w) -> ( -w*y, w*x, 0 )
    om_cross_r = np.array([-OMEGA_E * pos_ecef[1], OMEGA_E * pos_ecef[0], 0.0])
    vel_eci = R @ (vel_ecef + om_cross_r)
    return pos_eci, vel_eci


def eci_to_ecef(pos_eci: np.ndarray, vel_eci: np.ndarray,
                jd: float) -> Tuple[np.ndarray, np.ndarray]:
    """ECI -> ECEF（绕 z 轴转 -GMST）。v_ECEF = R^T·v_ECI - Ω×r_ECEF。"""
    th = gmst_rad(jd)
    c, s = math.cos(th), math.sin(th)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    pos_ecef = R.T @ np.asarray(pos_eci, dtype=float)
    # v_ECEF = R^T v_ECI - Ω×r_ECEF
    om_cross_r = np.array([-OMEGA_E * pos_ecef[1], OMEGA_E * pos_ecef[0], 0.0])
    vel_ecef = R.T @ np.asarray(vel_eci, dtype=float) - om_cross_r
    return pos_ecef, vel_ecef


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
    """WGS-84 大地坐标 -> ECEF (km)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon)
    y = (n + alt_km) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return np.array([x, y, z])


def ecef_to_enu_axes(lat_deg: float, lon_deg: float) -> np.ndarray:
    """返回 3x3 旋转矩阵，把站心 ECEF 差分向量投到 ENU 系。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    so, co = math.sin(lon), math.cos(lon)
    return np.array([
        [-so, co, 0.0],
        [-sl * co, -sl * so, cl],
        [cl * co, cl * so, sl],
    ])


def ecef_to_azel(sat_ecef: np.ndarray, rx_ecef: np.ndarray,
                 lat_deg: float, lon_deg: float) -> Tuple[float, float]:
    """站心 ECEF 向量 -> 方位/仰角（度）。方位北顺时针。"""
    d = np.asarray(sat_ecef) - np.asarray(rx_ecef)
    R_enu = ecef_to_enu_axes(lat_deg, lon_deg)
    e, n, u = R_enu @ d
    rng = math.sqrt(e * e + n * n + u * u)
    el = math.degrees(math.asin(max(-1.0, min(1.0, u / rng))))
    az = (math.degrees(math.atan2(e, n)) + 360.0) % 360.0
    return az, el


# ═══════════════════════════════════════════════════════
# 2. 信号处理前端：FFT 多普勒检测
# ═══════════════════════════════════════════════════════

def detect_doppler(iq_samples: np.ndarray, f0: float = F0_DEFAULT_HZ,
                   fs: float = 1.0e6) -> Tuple[float, float]:
    """对一段复基带 IQ 做 FFT，找谱峰，返回相对中心频偏 fd (Hz) 与 SNR (dB)。

    fd 为信号相对调谐中心的偏移（Hz）。snr 取峰值功率与全频谱功率中位数之比(dB)。
    """
    x = np.asarray(iq_samples, dtype=np.complex128)
    x = x - np.mean(x)
    n = len(x)
    if n < 16:
        return 0.0, 0.0
    window = np.hanning(n)
    spec = np.fft.fft(x * window)
    freqs = np.fft.fftfreq(n, d=1.0 / fs)          # 相对中心 (Hz)，含负频率
    power = np.abs(spec) ** 2
    # 排除 DC 附近 bin，避免直流泄漏误检
    power[0] = 0.0
    power[1] = 0.0
    power[-1] = 0.0
    peak = int(np.argmax(power))
    fd = float(freqs[peak])
    noise_floor = float(np.median(power)) + 1e-12
    snr_db = 10.0 * math.log10(power[peak] / noise_floor)
    return fd, snr_db


def detect_doppler_timeseries(iq_samples: np.ndarray, fs: float,
                              f0: float = F0_DEFAULT_HZ,
                              window_s: float = 0.1,
                              hop_s: float = 0.05) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """滑窗批处理：把整段 IQ 切成滑窗，逐窗 FFT。返回 (t[s], fd[Hz], snr[dB])。"""
    win = max(16, int(round(window_s * fs)))
    hop = max(16, int(round(hop_s * fs)))
    iq = np.asarray(iq_samples, dtype=np.complex128)
    ts: List[float] = []
    fds: List[float] = []
    snrs: List[float] = []
    for start in range(0, len(iq) - win + 1, hop):
        seg = iq[start:start + win]
        fd, snr = detect_doppler(seg, f0=f0, fs=fs)
        ts.append((start + win / 2.0) / fs)
        fds.append(fd)
        snrs.append(snr)
    return np.array(ts), np.array(fds), np.array(snrs)


# ═══════════════════════════════════════════════════════
# 3. 观测模型：伪距率与雅可比
# ═══════════════════════════════════════════════════════

def fd_to_rangerate(fd_hz: float, f0: float = F0_DEFAULT_HZ) -> float:
    """频偏 -> 伪距率 ρdot (km/s)。ρdot = -c·fd/f0。"""
    return -C_LIGHT_KMS * fd_hz / f0


def rangerate_to_fd(rho_dot_kms: float, f0: float = F0_DEFAULT_HZ) -> float:
    """伪距率 -> 频偏 fd (Hz)。fd = -f0·ρdot/c。"""
    return -f0 * rho_dot_kms / C_LIGHT_KMS


def pseudorange_rate_and_jacobian(state: np.ndarray,
                                  rx_pos: np.ndarray,
                                  rx_vel: np.ndarray
                                  ) -> Tuple[float, np.ndarray]:
    """预测伪距率 ρdot 及对 7 维状态的雅可比。

    ρdot = u·(v_sat - v_rx) + b，其中 u = (r_sat - r_rx)/|·| 为站星视线单位向量
    （从接收机指向卫星），b 为钟漂等效伪距率偏置（km/s）。

    返回:
      rho_dot : 预测伪距率 (km/s)
      H       : shape (7,)，∂ρdot/∂state
    """
    state = np.asarray(state, dtype=float)
    r = state[0:3]
    v = state[3:6]
    b = state[6]
    rx_pos = np.asarray(rx_pos, dtype=float)
    rx_vel = np.asarray(rx_vel, dtype=float)

    s = r - rx_pos                       # 站心卫星向量
    rho = float(np.linalg.norm(s))
    u = s / rho
    v_rel = v - rx_vel
    rho_dot = float(u @ v_rel + b)

    H = np.zeros(7)
    # ∂ρdot/∂v_sat = u
    H[3:6] = u
    # ∂ρdot/∂r_sat = (I - u u^T) v_rel / rho
    H[0:3] = (v_rel - u * (u @ v_rel)) / rho
    # ∂ρdot/∂b = 1
    H[6] = 1.0
    return rho_dot, H


# ═══════════════════════════════════════════════════════
# 4. 动力学：ECEF 二体+J2 传播 + STM（RK4）
# ═══════════════════════════════════════════════════════

J2 = 1.08262668e-3                 # 地球二阶带谐系数
WGS84_RE = 6378.137                # km，J2 归一化赤道半径


def _ecef_accel(r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """ECEF 旋转系中卫星加速度：中心引力 + J2 摄动 + 离心力 + Coriolis 力。

    旋转系（ECEF）牛顿方程：
      a = -GM r/r^3 + a_J2 + (w^2 x, w^2 y, 0) + 2(w vy, -w vx, 0)
    其中 J2 为 LEO 主导摄动（sgp4 亦含），不加则 30min 内相对真值漂移 ~9km。
    """
    r = np.asarray(r, dtype=float)
    v = np.asarray(v, dtype=float)
    rmag = float(np.linalg.norm(r))
    acc = -GM_EARTH / rmag ** 3 * r
    # J2 摄动（标准矢量形式）
    z = r[2]
    z2r2 = z * z / rmag ** 2
    k = -1.5 * J2 * GM_EARTH * WGS84_RE ** 2 / rmag ** 5
    acc[0] += k * r[0] * (1.0 - 5.0 * z2r2)
    acc[1] += k * r[1] * (1.0 - 5.0 * z2r2)
    acc[2] += k * z * (3.0 - 5.0 * z2r2)
    # 离心力项 -Ω×(Ω×r) = (w^2 x, w^2 y, 0)
    acc[0] += OMEGA_E ** 2 * r[0]
    acc[1] += OMEGA_E ** 2 * r[1]
    # Coriolis 项 -2Ω×v = 2(w vy, -w vx, 0)
    acc[0] += 2.0 * OMEGA_E * v[1]
    acc[1] += -2.0 * OMEGA_E * v[0]
    return acc


def _ecef_eom6(s6: np.ndarray) -> np.ndarray:
    """6 维轨道方程右端 ds/dt = [v, a(r,v)]。"""
    out = np.empty(6)
    out[0:3] = s6[3:6]
    out[3:6] = _ecef_accel(s6[0:3], s6[3:6])
    return out


def _A_matrix(s6: np.ndarray) -> np.ndarray:
    """6x6 系统雅可比 A = ∂f/∂s，中心差分（自动含 J2/Coriolis 耦合）。"""
    A = np.zeros((6, 6))
    eps = 1e-6
    f0 = _ecef_eom6(s6)
    for j in range(6):
        dp = s6.copy()
        dm = s6.copy()
        h = eps * (1.0 + abs(s6[j]))
        dp[j] += h
        dm[j] -= h
        A[:, j] = (_ecef_eom6(dp) - _ecef_eom6(dm)) / (2.0 * h)
    return A


def _ecef_dynamics_aug(y: np.ndarray) -> np.ndarray:
    """ECEF 轨道方程 + 变分方程右端。y[0:6]=[r,v]；y[6:42]=6x6 STM 拉直。"""
    s6 = y[0:6]
    Phi = y[6:42].reshape(6, 6)
    ds = _ecef_eom6(s6)
    A = _A_matrix(s6)
    dPhi = A @ Phi
    return np.concatenate([ds, dPhi.ravel()])


def propagate_ecef_state_and_stm(state: np.ndarray, dt: float
                                 ) -> Tuple[np.ndarray, np.ndarray]:
    """在 ECEF 系内把 7 维状态从当前时刻传播 dt 秒，并返回 7x7 状态转移矩阵 Φ。

    RK4 数值积分轨道 6 维状态 + 6x6 STM（变分方程）。钟漂 b 视为常数（bdot=0），
    Φ 的第 7 对角元为 1、与轨道无耦合。

    参数:
      state : shape (7,)，[x,y,z,vx,vy,vz,b]
      dt    : 传播时长（秒，可负）
    返回:
      state_new : shape (7,)
      Phi       : shape (7,7)，state_new ≈ state + Phi·(state 扰动)
    """
    state = np.asarray(state, dtype=float)
    # 积分增广向量：轨道 6 + STM 36 = 42
    y0 = np.zeros(42)
    y0[0:6] = state[0:6]
    y0[6:42] = np.eye(6).ravel()

    # RK4 子步：每步 <=30s，保证 STM 精度
    nsub = max(1, int(math.ceil(abs(dt) / 30.0)))
    h = dt / nsub
    y = y0.copy()
    for _ in range(nsub):
        k1 = _ecef_dynamics_aug(y)
        k2 = _ecef_dynamics_aug(y + 0.5 * h * k1)
        k3 = _ecef_dynamics_aug(y + 0.5 * h * k2)
        k4 = _ecef_dynamics_aug(y + h * k3)
        y = y + (h / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)

    state_new = np.zeros(7)
    state_new[0:6] = y[0:6]
    state_new[6] = state[6]                 # 钟漂常数
    Phi = np.eye(7)
    Phi[0:6, 0:6] = y[6:42].reshape(6, 6)
    # 第 7 行/列与轨道无耦合，保持单位阵对角 1
    return state_new, Phi


# ═══════════════════════════════════════════════════════
# 5. 估计器：EKF 与 参考历元 RLS
# ═══════════════════════════════════════════════════════

class ExtendedKalmanFilter:
    """扩展卡尔曼滤波（7 维状态）。

    预测步：STM 传播状态 + 协方差 P = Φ P Φᵀ + Q。
    更新步：ρdot 残差 + 雅可比 + 卡尔曼增益。
    Q / R 可配置。
    """

    def __init__(self, state: np.ndarray, P: np.ndarray,
                 q_sigma: np.ndarray, r_sigma: float):
        self.state = np.asarray(state, dtype=float).copy()
        self.P = np.asarray(P, dtype=float).copy()
        # 过程噪声协方差（离散），按各维 q_sigma^2
        self.Q = np.diag(np.asarray(q_sigma, dtype=float) ** 2)
        self.r_sigma = r_sigma            # 伪距率测量噪声 (km/s)

    def predict(self, dt: float) -> None:
        self.state, Phi = propagate_ecef_state_and_stm(self.state, dt)
        self.P = Phi @ self.P @ Phi.T + self.Q

    def update(self, rho_dot_obs: float, rx_pos: np.ndarray, rx_vel: np.ndarray
               ) -> float:
        rho_dot_pred, H = pseudorange_rate_and_jacobian(self.state, rx_pos, rx_vel)
        y = rho_dot_obs - rho_dot_pred                 # 新息
        Hm = H.reshape(1, 7)
        S = float(np.asarray(Hm @ self.P @ Hm.T).ravel()[0]) + self.r_sigma ** 2
        K = (self.P @ Hm.T).ravel() / S
        self.state = self.state + K * y
        I7 = np.eye(7)
        self.P = (I7 - np.outer(K, Hm)) @ self.P
        return y


def reference_epoch_rls_update(state: np.ndarray, P: np.ndarray,
                               observations: List[Tuple[float, float, np.ndarray, np.ndarray]],
                               reference_epoch: float,
                               r_sigma: float,
                               iterations: int = 3) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """参考历元递推最小二乘（批处理 + 迭代）。

    所有观测量都通过 STM 映射回参考历元 reference_epoch，在参考历元状态上叠加法方程，
    一次解出参考历元状态修正。等价于“把多历元多普勒观测折叠到同一历元”的经典 RLS。

    参数:
      state        : 参考历元先验状态 (7,)
      P            : 参考历元先验协方差 (7,7)（作为加权约束）
      observations : [(t_abs, rho_dot, rx_pos(3), rx_vel(3)), ...]，t 为绝对秒
      reference_epoch : 参考历元绝对秒 t0
      r_sigma      : 伪距率测量噪声 (km/s)
      iterations   : 线性化迭代次数
    返回:
      state_ref : 修正后的参考历元状态 (7,)
      P_ref     : 后验协方差 (7,7)
      residuals : 每次观测的拟合残差 (km/s)
    """
    x_ref = np.asarray(state, dtype=float).copy()
    P = np.asarray(P, dtype=float)
    R_inv = 1.0 / (r_sigma ** 2)
    W0 = np.linalg.inv(P)                       # 先验信息矩阵

    for _ in range(iterations):
        N = W0.copy()                            # 信息矩阵
        rhs = W0 @ (np.asarray(state) - x_ref)    # 先验约束项
        resids: List[float] = []
        for (ti, rho_dot_obs, rx_pos, rx_vel) in observations:
            dt = ti - reference_epoch
            xi, Phi = propagate_ecef_state_and_stm(x_ref, dt)
            rho_pred, Hi = pseudorange_rate_and_jacobian(xi, rx_pos, rx_vel)
            H_ref = Hi @ Phi                     # 映射回参考历元
            y = rho_dot_obs - rho_pred
            resids.append(y)
            N += np.outer(H_ref, H_ref) * R_inv
            rhs += H_ref * (y * R_inv)
        dx = np.linalg.solve(N, rhs)
        x_ref = x_ref + dx
        if float(np.linalg.norm(dx)) < 1e-9:
            break
    P_ref = np.linalg.inv(N)
    return x_ref, P_ref, np.array(resids)


# ═══════════════════════════════════════════════════════
# 6. LEO 初值（sgp4/TLE）与 LRO 参考轨道（skyfield / Meeus 回退）
# ═══════════════════════════════════════════════════════

def leosat_state_from_tle(line1: str, line2: str, unix_s: float
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """用 sgp4 从 TLE 传播，返回某时刻卫星在 ECEF 下的位置(km)/速度(km/s)。"""
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(line1, line2)
    jd = unix_s / 86400.0 + 2440587.5
    jdi, jdf = int(jd), jd - int(jd)
    e, r_teme, v_teme = sat.sgp4(jdi, jdf)
    if e != 0:
        raise RuntimeError(f"sgp4 传播失败 code={e}")
    r_teme = np.array(r_teme)
    v_teme = np.array(v_teme)
    # TEME -> ECEF：绕 z 转 -GMST，速度含 Coriolis（与 orbit.py 一致）
    gmst = gmst_rad(jd)
    c, s = math.cos(-gmst), math.sin(-gmst)
    r_ecef = np.array([c * r_teme[0] - s * r_teme[1],
                       s * r_teme[0] + c * r_teme[1],
                       r_teme[2]])
    vx = c * v_teme[0] - s * v_teme[1] + OMEGA_E * r_ecef[1]
    vy = s * v_teme[0] + c * v_teme[1] - OMEGA_E * r_ecef[0]
    v_ecef = np.array([vx, vy, v_teme[2]])
    return r_ecef, v_ecef


def _moon_position_meeus(jd: float) -> Tuple[np.ndarray, float]:
    """Meeus《Astronomical Algorithms》ch.47 截断版月球地心位置（ECI 赤道系，km）。

    离线回退方案：当 skyfield 无法下载 JPL 星历时使用。精度约 0.2-0.3°，
    地距 36.3万-40.5万 km 范围，足够 LRO 天空图与距离范围校验。
    返回 (r_eci(3,), 距离 km)。
    """
    T = (jd - 2451545.0) / 36525.0
    # 月球/太阳平均轨道要素（度）
    Lp = 218.3164477 + 481267.88123421 * T
    D = 297.8501921 + 445267.1114034 * T
    Mmoon = 134.9633964 + 477198.8675055 * T
    Msun = 357.5291092 - 35999.0502909 * T
    F = 93.2720950 + 483202.0175233 * T
    E = 1.0 - 0.002516 * T - 0.0000074 * T * T

    r2d = math.pi / 180.0

    def rad(x):
        return x * r2d

    # 黄经周期项（度）
    lon = (6.289 * math.sin(rad(Mmoon)) * E
           + 1.274 * math.sin(rad(2 * Mmoon - Msun)) * E
           + 0.656 * math.sin(rad(2 * D))
           + 0.214 * math.sin(rad(2 * Mmoon)) * E
           - 0.186 * math.sin(rad(Msun)) * E
           - 0.114 * math.sin(rad(2 * F)))
    lam = (Lp + lon) % 360.0
    # 黄纬（度）
    beta = (5.128 * math.sin(rad(F))
            + 0.281 * math.sin(rad(Mmoon + F)) * E
            + 0.278 * math.sin(rad(2 * Mmoon + F)) * E
            + 0.173 * math.sin(rad(2 * D - F))
            + 0.271 * math.sin(rad(F - Msun)))
    # 地心距离（km）
    dist = (385001.0
            - 20905.0 * math.cos(rad(Mmoon)) * E
            - 3699.0 * math.cos(rad(2 * Mmoon - Msun)) * E
            - 2956.0 * math.cos(rad(2 * D))
            - 570.0 * math.cos(rad(2 * Mmoon))
            - 469.0 * math.cos(rad(2 * F)))
    # 黄赤交角
    eps = math.radians(23.4392911 - 0.0130042 * T)
    l = math.radians(lam)
    b = math.radians(beta)
    ce, se = math.cos(eps), math.sin(eps)
    cl, sl = math.cos(l), math.sin(l)
    cb, sb = math.cos(b), math.sin(b)
    # 黄道 -> 赤道直角（地心，ECI 赤道系）
    x = dist * cb * cl
    y = dist * (cb * sl * ce - sb * se)
    z = dist * (cb * sl * se + sb * ce)
    return np.array([x, y, z]), dist


def get_lro_reference(unix_times: np.ndarray,
                      use_skyfield: bool = True
                      ) -> Dict[str, np.ndarray]:
    """返回 LRO 在 ECI 与 ECEF 下的参考位置/速度序列。

    LRO 绕月轨道高度 ~50km、周期 ~113min（简化圆轨道）。月球地心位置优先用
    skyfield JPL 星历；星历不可用时回退 Meeus 解析月球理论。速度由中心差分得到。

    返回 dict:
      t_unix, jd, r_eci( N,3 ), v_eci( N,3 ), r_ecef( N,3 ), v_ecef( N,3 ), dist_km
    """
    times = np.asarray(unix_times, dtype=float)
    jds = times / 86400.0 + 2440587.5

    moon_eci = np.zeros((len(times), 3))
    # 优先 skyfield JPL 星历；不可用则回退 Meeus 解析月球理论
    eph = None
    ts = None
    if use_skyfield:
        try:
            from skyfield.api import load as _load
            ts = _load.timescale()
            eph = _load("de421.bsp")
        except Exception:
            eph = None
            ts = None

    if eph is not None:
        earth = eph["earth"]
        moon = eph["moon"]
        t_obj = ts.J(jds)
        astrometric = earth.at(t_obj).observe(moon)
        gc = astrometric.frame_xyz("J2000")
        moon_eci = np.column_stack([
            gc.x.au * 149597870.700,
            gc.y.au * 149597870.700,
            gc.z.au * 149597870.700,
        ])
    else:
        for i, jd in enumerate(jds):
            r, _ = _moon_position_meeus(jd)
            moon_eci[i] = r

    # LRO 相对月球的圆轨道（~50km 高度，~113min 周期）
    r_lro = MOON_RADIUS + LRO_ORBIT_ALT
    n = 2.0 * math.pi / LRO_PERIOD_S
    t0 = times[0]
    # 取一个任意轨道面（相对月球惯性系赤道）
    lro_rel = np.zeros((len(times), 3))
    lro_rel_v = np.zeros((len(times), 3))
    for i, t in enumerate(times):
        ph = n * (t - t0) + 0.7
        lro_rel[i] = r_lro * np.array([math.cos(ph), math.sin(ph), 0.0])
        lro_rel_v[i] = r_lro * n * np.array([-math.sin(ph), math.cos(ph), 0.0])

    lro_eci = moon_eci + lro_rel

    # 月球速度（中心差分）
    moon_v = np.gradient(moon_eci, times, axis=0)
    lro_v_eci = moon_v + lro_rel_v

    # ECI -> ECEF
    r_ecef = np.zeros_like(lro_eci)
    v_ecef = np.zeros_like(lro_v_eci)
    for i, jd in enumerate(jds):
        re, ve = eci_to_ecef(lro_eci[i], lro_v_eci[i], jd)
        r_ecef[i] = re
        v_ecef[i] = ve

    dist = np.linalg.norm(lro_eci, axis=1)
    return {
        "t_unix": times,
        "jd": jds,
        "r_eci": lro_eci,
        "v_eci": lro_v_eci,
        "r_ecef": r_ecef,
        "v_ecef": v_ecef,
        "dist_km": dist,
    }


# ═══════════════════════════════════════════════════════
# 7. 输出与可视化
# ═══════════════════════════════════════════════════════

def position_error_curve(estimated: np.ndarray, reference: np.ndarray
                         ) -> Tuple[np.ndarray, np.ndarray]:
    """三维位置差 (km) 随时间。输入 estimated/reference 为 (N,3) ECEF 位置。"""
    est = np.asarray(estimated, dtype=float)
    ref = np.asarray(reference, dtype=float)
    d = est - ref
    return np.linalg.norm(d, axis=1), d


def residual_statistics(residuals: np.ndarray) -> Dict[str, float]:
    """残差统计：均值/标准差/RMS/最大绝对值。"""
    r = np.asarray(residuals, dtype=float)
    return {
        "mean": float(np.mean(r)),
        "std": float(np.std(r)),
        "rms": float(np.sqrt(np.mean(r ** 2))),
        "max_abs": float(np.max(np.abs(r))),
        "count": int(r.size),
    }


def sky_plot(az: np.ndarray, el: np.ndarray,
             save_path: Optional[str] = None, title: str = "Sky Plot") -> np.ndarray:
    """方位/仰角天空图数据（保存 PNG）。返回 az/el 数组本身。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    az = np.asarray(az, dtype=float)
    el = np.asarray(el, dtype=float)
    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_theta_offset(0.0)
    r = 90.0 - el                       # 天顶距
    ax.scatter(np.radians(az), r, c=np.linspace(0, 1, len(az)), cmap="viridis", s=12)
    ax.set_yticks([0, 30, 60, 90])
    ax.set_yticklabels(["90°", "60°", "30°", "0°"])
    ax.set_title(title)
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=110)
    plt.close(fig)
    return np.column_stack([az, el])


def plot_convergence_curve(t: np.ndarray, pos_err_rls: np.ndarray,
                           pos_err_ekf: np.ndarray,
                           residuals_ekf: np.ndarray,
                           save_path: str,
                           f0: float = F0_DEFAULT_HZ) -> None:
    """画位置误差收敛曲线 + 残差图，存 PNG。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(8, 7))
    axes[0].plot(t / 60.0, pos_err_rls, label="RLS", lw=1.6)
    axes[0].plot(t / 60.0, pos_err_ekf, label="EKF", lw=1.6)
    axes[0].axhline(1.0, color="r", ls="--", lw=1.0, label="1 km 阈值")
    axes[0].set_xlabel("时间 (min)")
    axes[0].set_ylabel("三维位置误差 (km)")
    axes[0].set_yscale("log")
    axes[0].set_title("多普勒定轨收敛曲线 (Iridium 风格 LEO)")
    axes[0].legend()
    axes[0].grid(True, which="both", alpha=0.3)
    fd_res = -f0 * np.asarray(residuals_ekf) / C_LIGHT_KMS   # 残差换算回 Hz
    axes[1].plot(t / 60.0, fd_res, lw=0.9, color="teal")
    axes[1].axhline(0, color="k", lw=0.6)
    axes[1].set_xlabel("时间 (min)")
    axes[1].set_ylabel("伪距率残差 (Hz)")
    axes[1].set_title("EKF 测量残差（频偏等效）")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    fig.savefig(save_path, dpi=110)
    plt.close(fig)


def plot_residual_histogram(residuals: np.ndarray, save_path: str,
                            label: str = "EKF") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(np.asarray(residuals), bins=30, color="steelblue", edgecolor="k", alpha=0.8)
    ax.set_xlabel("伪距率残差 (km/s)")
    ax.set_ylabel("计数")
    ax.set_title(f"{label} 残差直方图")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    fig.savefig(save_path, dpi=110)
    plt.close(fig)


# ═══════════════════════════════════════════════════════
# 8. 高级封装：端到端 Doppler 定轨
# ═══════════════════════════════════════════════════════

def doppler_orbit_determine(observations: List[Tuple[float, float]],
                            rx_lat: float, rx_lon: float, rx_alt: float,
                            init_state: np.ndarray,
                            init_P: Optional[np.ndarray] = None,
                            f0: float = F0_DEFAULT_HZ,
                            estimator: str = "ekf"
                            ) -> Dict[str, Any]:
    """端到端：喂入多普勒观测序列 -> 输出轨道估计与误差诊断。

    observations: [(t_unix, fd_hz), ...]  多普勒频偏序列。
    init_state  : 7 维初值（ECEF）。
    estimator   : "ekf" 或 "rls"。
    """
    rx_pos = geodetic_to_ecef(rx_lat, rx_lon, rx_alt)
    # 地面站 ECEF 速度 = Ω×r = (-w y, w x, 0)
    rx_vel = np.array([-OMEGA_E * rx_pos[1], OMEGA_E * rx_pos[0], 0.0])

    obs = []
    for (ti, fd) in observations:
        rho_dot = fd_to_rangerate(fd, f0)
        obs.append((ti, rho_dot, rx_pos, rx_vel))

    if init_P is None:
        init_P = np.diag([10.0 ** 2] * 3 + [0.01 ** 2] * 3 + [(0.01) ** 2])
    # 测量噪声：fd σ=10Hz -> rho_dot σ
    r_sigma = C_LIGHT_KMS / f0 * 10.0
    q_sigma = np.array([1e-5, 1e-5, 1e-5, 1e-6, 1e-6, 1e-6, 1e-6])

    if estimator == "rls":
        t0 = observations[0][0]
        x_ref, P_ref, resids = reference_epoch_rls_update(
            init_state, init_P, obs, t0, r_sigma)
        est_t, est_r, est_v = [], [], []
        for (ti, _, _, _) in obs:
            si, _ = propagate_ecef_state_and_stm(x_ref, ti - t0)
            est_t.append(ti)
            est_r.append(si[0:3])
            est_v.append(si[3:6])
        return {
            "estimator": "rls",
            "state_ref": x_ref,
            "times": np.array(est_t),
            "positions_ecef": np.array(est_r),
            "velocities_ecef": np.array(est_v),
            "residuals": resids,
            "residual_stats": residual_statistics(resids),
        }

    # EKF
    ekf = ExtendedKalmanFilter(init_state, init_P, q_sigma, r_sigma)
    est_t, est_r, est_v, resids = [], [], [], []
    t_prev = obs[0][0]
    for (ti, rho_dot, rxp, rxv) in obs:
        ekf.predict(ti - t_prev)
        y = ekf.update(rho_dot, rxp, rxv)
        t_prev = ti
        est_t.append(ti)
        est_r.append(ekf.state[0:3].copy())
        est_v.append(ekf.state[3:6].copy())
        resids.append(y)
    return {
        "estimator": "ekf",
        "state": ekf.state,
        "P": ekf.P,
        "times": np.array(est_t),
        "positions_ecef": np.array(est_r),
        "velocities_ecef": np.array(est_v),
        "residuals": np.array(resids),
        "residual_stats": residual_statistics(np.array(resids)),
    }


# ═══════════════════════════════════════════════════════
# 9. 工具注册
# ═══════════════════════════════════════════════════════

def register_tool_registry(registry) -> None:
    """把多普勒定轨 / LRO 跟踪工具注册到 MBDSDR ToolRegistry。"""
    from .tool_registry import ToolResult

    def _doppler_orbit_determine(args: Dict[str, Any]) -> ToolResult:
        obs_in = args.get("observations", [])
        observations = [(float(o["t"]), float(o["fd"])) for o in obs_in]
        try:
            res = doppler_orbit_determine(
                observations,
                rx_lat=float(args.get("rx_lat", 39.9)),
                rx_lon=float(args.get("rx_lon", 116.4)),
                rx_alt=float(args.get("rx_alt", 0.0)),
                init_state=np.array(args.get("init_state"), dtype=float),
                f0=float(args.get("f0", F0_DEFAULT_HZ)),
                estimator=args.get("estimator", "ekf"),
            )
        except Exception as e:  # noqa
            return ToolResult(success=False, content="", error=str(e),
                              tool_name="doppler_orbit_determine")
        stats = res["residual_stats"]
        return ToolResult(
            success=True,
            content=(f"多普勒定轨完成({res['estimator']})：N={stats['count']}，"
                     f"残差均值={stats['mean']:.5f} km/s，RMS={stats['rms']:.5f} km/s"),
            tool_name="doppler_orbit_determine",
            data={
                "residual_stats": stats,
                "final_state": res.get("state", res.get("state_ref")).tolist(),
                "times": res["times"].tolist(),
                "positions_ecef": res["positions_ecef"].tolist(),
            },
        )

    def _lro_track(args: Dict[str, Any]) -> ToolResult:
        hours = float(args.get("hours", 24.0))
        # 地面站坐标：优先用参数，否则从配置读取；都没有则报错
        cfg_lat = cfg_lon = None
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        try:
            if os.path.exists(cfg_path):
                import json as _cfg_json
                with open(cfg_path, "r", encoding="utf-8") as f:
                    _cfg = _cfg_json.load(f)
                cfg_lat = _cfg.get("ground_station_lat")
                cfg_lon = _cfg.get("ground_station_lon")
        except Exception:
            pass
        rx_lat = args.get("rx_lat", cfg_lat)
        rx_lon = args.get("rx_lon", cfg_lon)
        if rx_lat is None or rx_lon is None:
            return ToolResult(
                success=False,
                content="未配置地面站坐标。请在参数中传入 rx_lat/rx_lon，"
                        "或在 ~/.mbdsdr/config.json 中设置 ground_station_lat / ground_station_lon。",
            )
        rx_lat = float(rx_lat)
        rx_lon = float(rx_lon)
        n = int(args.get("n", 288))
        import time as _t
        t0 = float(args.get("t_unix", _t.time()))
        times = t0 + np.linspace(0, hours * 3600.0, n)
        ref = get_lro_reference(times)
        rx_pos = geodetic_to_ecef(rx_lat, rx_lon, 0.0)
        azs, els = [], []
        for i in range(n):
            az, el = ecef_to_azel(ref["r_ecef"][i], rx_pos, rx_lat, rx_lon)
            azs.append(az)
            els.append(el)
        return ToolResult(
            success=True,
            content=(f"LRO 参考轨道：{hours}h，地距 "
                     f"{ref['dist_km'].min():.0f}-{ref['dist_km'].max():.0f} km，"
                     f"仰角范围 {min(els):.1f}-{max(els):.1f}°"),
            tool_name="lro_track",
            data={
                "dist_km": ref["dist_km"].tolist(),
                "azimuth_deg": azs,
                "elevation_deg": els,
            },
        )

    registry.register(
        name="doppler_orbit_determine",
        description=(
            "多普勒定轨：输入多普勒频偏序列 [(t,fd_hz)]（或由 IQ 经 detect_doppler 得到）"
            "+ 7 维初值，用 EKF 或参考历元 RLS 估计 ECEF 轨道 [x,y,z,vx,vy,vz,钟漂]。"
            "返回估计轨道位置序列与残差统计（均值/标准差/RMS）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "t": {"type": "number", "description": "unix 秒"},
                            "fd": {"type": "number", "description": "多普勒频偏 Hz"},
                        },
                    },
                    "description": "多普勒观测序列 [(t_unix, fd_hz), ...]",
                },
                "init_state": {
                    "type": "array", "items": {"type": "number"},
                    "description": "7 维初值 [x,y,z,vx,vy,vz,b]（ECEF, km,km/s）",
                },
                "rx_lat": {"type": "number", "default": 39.9},
                "rx_lon": {"type": "number", "default": 116.4},
                "rx_alt": {"type": "number", "default": 0.0},
                "f0": {"type": "number", "default": F0_DEFAULT_HZ},
                "estimator": {"type": "string", "enum": ["ekf", "rls"], "default": "ekf"},
            },
            "required": ["observations", "init_state"],
        },
        handler=_doppler_orbit_determine,
        category="orbit",
    )

    registry.register(
        name="lro_track",
        description=(
            "LRO 位置预测+天空图：用 skyfield 月球星历（离线回退 Meeus 解析月球理论）"
            "+ 简化二体绕月模型（~50km 高度/~113min 周期）计算未来 N 小时 LRO 地心系位置，"
            "并给出从地面站看的方位/仰角。返回地距范围与天空图数据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "hours": {"type": "number", "default": 24.0},
                "rx_lat": {"type": "number", "default": 39.9},
                "rx_lon": {"type": "number", "default": 116.4},
                "n": {"type": "integer", "default": 288},
            },
            "required": [],
        },
        handler=_lro_track,
        category="orbit",
    )
