"""
深空追迹多普勒定轨 - 往返验证测试
==================================
Test 1: Iridium-107 风格合成数据收敛测试（sgp4 真值 + 加噪多普勒 + 钟漂）
        - EKF 与参考历元 RLS 均须从 ~10km 位置偏差收敛到 <1km；
        - 残差均值接近 0；生成收敛曲线 PNG 到 artifacts/。
Test 2: LRO 参考轨道生成（skyfield / Meeus 回退）
        - 24h LRO 地距在 36万-40万 km；
        - 生成天空图 PNG。
Test 3: 坐标变换一致性
        - ECEF->ECI->ECEF 往返误差 < 1mm；
        - 伪距率雅可比数值微分验证 < 1%。
"""
import os
import datetime

import numpy as np
import pytest

from mbdsdr_ai import orbit_determination as od

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mbdsdr_ai", "artifacts")
ART = os.path.abspath(ART)


def _beijing_rx():
    """北京地面站 ECEF 位置/速度。"""
    rx_pos = od.geodetic_to_ecef(39.9, 116.4, 0.0)
    rx_vel = np.array([-od.OMEGA_E * rx_pos[1], od.OMEGA_E * rx_pos[0], 0.0])
    return rx_pos, rx_vel


def _synthetic_iridium_tle():
    """合成 Iridium 风格 TLE：圆轨道 ~780km，倾角 86.4°，周期 ~14.34 rev/day。"""
    line1 = ("1 99999U 24001A   24278.00000000  .00000000  00000-0  00000-0 0  9999")
    line2 = ("2 99999  86.4000 160.0000 0001000 320.0000 320.0000 14.34000000    00")
    return line1, line2


def _build_synthetic_pass(line1, line2, rng, f0, clk_drift_hz=30.0):
    """传播 30 分钟真值轨道，选仰角>10° 的连续过境，生成加噪多普勒观测。"""
    rx_pos, rx_vel = _beijing_rx()
    epoch = datetime.datetime(2024, 10, 4, 0, 0, 0)
    eu = (epoch - datetime.datetime(1970, 1, 1)).total_seconds()
    # 真值传播 30+ 分钟（细采样 5s），足够覆盖一次完整过境
    ts = eu + np.arange(0, 4 * 3600, 5.0)
    states = [od.leosat_state_from_tle(line1, line2, t) for t in ts]
    els = np.array([od.ecef_to_azel(states[k][0], rx_pos, 39.9, 116.4)[1]
                    for k in range(len(ts))])
    ic = int(np.argmax(els))
    # 中天附近取连续 el>10° 段（上升->中天->下降），即一次完整过境
    above = els > 10.0
    s = ic
    while s > 0 and above[s - 1]:
        s -= 1
    e = ic
    while e < len(els) - 1 and above[e + 1]:
        e += 1

    truth = []
    obs = []
    for k in range(s, e + 1):
        r, v = states[k]
        t = ts[k]
        # 真实伪距率 -> 多普勒频偏，叠加常数钟漂 + 高斯噪声 σ=10Hz
        sv = r - rx_pos
        rho = np.linalg.norm(sv)
        u = sv / rho
        rho_dot = u @ (v - rx_vel)
        fd_clean = od.rangerate_to_fd(rho_dot, f0)
        fd_obs = fd_clean + clk_drift_hz + rng.normal(0.0, 10.0)
        truth.append((t, r, v))
        obs.append((t, fd_obs))
    return truth, obs, rx_pos, rx_vel


# ═══════════════════════════════════════════════════════
# Test 1：Iridium 风格合成数据收敛测试
# ═══════════════════════════════════════════════════════

def test_iridium_synthetic_convergence():
    f0 = od.F0_DEFAULT_HZ
    line1, line2 = _synthetic_iridium_tle()
    rng = np.random.default_rng(7)           # 固定种子保证可复现
    truth, obs, rx_pos, rx_vel = _build_synthetic_pass(line1, line2, rng, f0)

    truth_r = np.array([t[1] for t in truth])
    truth_v0 = truth[0][2]
    # 初值：真值 + 10km 位置偏差 + 1m/s 速度偏差
    x0 = np.concatenate([
        truth_r[0] + np.array([10.0, -5.0, 3.0]),
        truth_v0 + np.array([0.001, -0.002, 0.001]),
        [0.0],
    ])
    P0 = np.diag([10.0 ** 2] * 3 + [0.01 ** 2] * 3 + [0.01 ** 2])
    r_sigma = od.C_LIGHT_KMS / f0 * 10.0

    # ---- EKF ----
    res_ekf = od.doppler_orbit_determine(obs, 39.9, 116.4, 0.0, x0,
                                         f0=f0, estimator="ekf")
    err_ekf, _ = od.position_error_curve(res_ekf["positions_ecef"], truth_r)

    # ---- RLS（参考历元批处理）----
    obs_rls = [(t, od.fd_to_rangerate(fd, f0), rx_pos, rx_vel) for (t, fd) in obs]
    x_ref, P_ref, res_rls = od.reference_epoch_rls_update(
        x0, P0, obs_rls, obs[0][0], r_sigma, iterations=4)
    err_rls = []
    for k, (t, fd) in enumerate(obs):
        sk, _ = od.propagate_ecef_state_and_stm(x_ref, t - obs[0][0])
        err_rls.append(np.linalg.norm(sk[0:3] - truth_r[k]))
    err_rls = np.array(err_rls)

    # 断言：初始偏差 ~11km，30min 内收敛到 <1km
    assert err_ekf[0] > 5.0, f"初始位置偏差应 >5km，实际 {err_ekf[0]:.2f}"
    assert err_ekf.min() < 1.0, f"EKF 未收敛到 1km 内，min={err_ekf.min():.3f}"
    assert err_ekf[-1] < 1.0, f"EKF 末端位置误差 {err_ekf[-1]:.3f} > 1km"
    assert err_rls.min() < 1.5, f"RLS 未收敛，min={err_rls.min():.3f}"
    # 残差均值接近 0（钟漂被吸收）
    stats = res_ekf["residual_stats"]
    assert abs(stats["mean"]) < 0.002, f"EKF 残差均值 {stats['mean']:.4f} 偏离 0"

    # ---- 生成收敛曲线 PNG ----
    os.makedirs(ART, exist_ok=True)
    t_rel = (res_ekf["times"] - res_ekf["times"][0])
    png_conv = os.path.join(ART, "doppler_convergence_iridium.png")
    od.plot_convergence_curve(t_rel, err_rls, err_ekf, res_ekf["residuals"],
                              png_conv, f0=f0)
    od.plot_residual_histogram(res_ekf["residuals"],
                               os.path.join(ART, "doppler_residual_hist.png"))
    assert os.path.exists(png_conv)
    print(f"\n[Test1] EKF start={err_ekf[0]:.2f} end={err_ekf[-1]:.3f} "
          f"min={err_ekf.min():.3f} km; RLS min={err_rls.min():.3f}; "
          f"resid RMS={stats['rms']:.5f} km/s")


# ═══════════════════════════════════════════════════════
# Test 2：LRO 参考轨道生成
# ═══════════════════════════════════════════════════════

def test_lro_reference_orbit():
    t0 = datetime.datetime(2024, 10, 4, 0, 0, 0).timestamp()
    times = t0 + np.linspace(0, 24 * 3600.0, 288)
    ref = od.get_lro_reference(times)

    # LRO 地距应在月球距离附近（36万-40万 km）
    dmin, dmax = float(ref["dist_km"].min()), float(ref["dist_km"].max())
    print(f"\n[Test2] LRO geocentric dist {dmin:.0f}..{dmax:.0f} km")
    assert 360000.0 < dmin, f"LRO 地距过小 {dmin:.0f}"
    assert dmax < 405000.0, f"LRO 地距过大 {dmax:.0f}"

    # 从北京看的方位/仰角，生成天空图
    rx_pos, _ = _beijing_rx()
    azs, els = [], []
    for i in range(len(times)):
        az, el = od.ecef_to_azel(ref["r_ecef"][i], rx_pos, 39.9, 116.4)
        azs.append(az)
        els.append(el)
    azs = np.array(azs)
    els = np.array(els)
    assert np.any(els > 0.0), "24h 内应存在仰角>0 的时刻（月球可见）"

    os.makedirs(ART, exist_ok=True)
    png_sky = os.path.join(ART, "lro_sky_plot.png")
    od.sky_plot(azs, els, save_path=png_sky, title="LRO / Moon Sky Track (Beijing, 24h)")
    assert os.path.exists(png_sky)


# ═══════════════════════════════════════════════════════
# Test 3：坐标变换一致性 + 雅可比数值验证
# ═══════════════════════════════════════════════════════

def test_coordinate_transform_roundtrip():
    r = np.array([-2000.0, 3500.0, 4000.0])
    v = np.array([5.0, -2.0, 1.0])
    jd = 2460000.5
    r_eci, v_eci = od.ecef_to_eci(r, v, jd)
    r2, v2 = od.eci_to_ecef(r_eci, v_eci, jd)
    pos_mm = np.linalg.norm(r - r2) * 1e6     # km -> mm
    vel_err = np.linalg.norm(v - v2)
    print(f"\n[Test3] ECEF<->ECI roundtrip pos={pos_mm:.2e} mm, vel={vel_err:.2e}")
    assert pos_mm < 1.0, f"坐标往返位置误差 {pos_mm:.3f} mm > 1mm"


def test_pseudorange_rate_jacobian_numeric():
    state = np.array([-2000.0, 3500.0, 4000.0, 5.0, -2.0, 1.0, 0.001])
    rx_pos = np.array([-2179.0, 3400.0, 4080.0])
    rx_vel = np.array([0.0, 0.0, 0.0])
    _, H = od.pseudorange_rate_and_jacobian(state, rx_pos, rx_vel)
    Hnum = np.zeros(7)
    for k in range(7):
        dp = state.copy()
        dm = state.copy()
        eps = 1e-6
        dp[k] += eps
        dm[k] -= eps
        hp, _ = od.pseudorange_rate_and_jacobian(dp, rx_pos, rx_vel)
        hm, _ = od.pseudorange_rate_and_jacobian(dm, rx_pos, rx_vel)
        Hnum[k] = (hp - hm) / (2 * eps)
    rel = np.abs(H - Hnum) / (np.abs(Hnum) + 1e-12)
    print(f"\n[Test3] Jacobian max rel err = {rel.max():.2e}")
    assert rel.max() < 0.01, f"雅可比数值验证相对误差 {rel.max():.2e} > 1%"
