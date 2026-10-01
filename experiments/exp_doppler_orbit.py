#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：多普勒定轨收敛（仿真口径，固定 TLE）
==============================================

闭环（B2 审计 §2.4）：固定 TLE -> sgp4 真值过境 -> 在长春站逐历元算真多普勒频偏
-> 加高斯噪声（固定种子）-> 初值给 ~5km/0.05km/s 扰动 -> EKF 与参考历元 RLS
解算 -> 记录三维位置误差随观测数/时间的收敛曲线。

全部调用 mbdsdr_ai.orbit_determination 既有真实零件（leosat_state_from_tle /
pseudorange_rate_and_jacobian / rangerate_to_fd / ExtendedKalmanFilter /
reference_epoch_rls_update / position_error_curve），不另写估计器。

口径：synthetic（仿真）。真值由 sgp4 从固定 TLE 生成，观测=真值+固定种子噪声。
无真实 IQ 录制时**不**生成任何 OTA/录制产物；真实过境 IQ 验证留待用户录制。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai import orbit_determination as od  # noqa: E402
from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

# 固定 TLE（ISS/ZARYA，公开发布）；真值传播用 sgp4。
TLE_L1 = ("1 25544U 98067A   24275.50000000  .00016717  00000-0  10270-3 0  9000")
TLE_L2 = ("2 25544  51.6400 292.0000 0001333  90.0000 270.0000 15.50000000    000")
# 过境窗口：仰角 0.8->71->2.6 deg（长春站 43.8868N,125.3245E）
T_BASE = datetime.datetime(2024, 10, 1, 12, 0, 0,
                           tzinfo=datetime.timezone.utc).timestamp() + 6240.0
RX_LAT, RX_LON, RX_ALT = 43.8868, 125.3245, 0.0


def build_truth(n_obs: int, pass_s: float):
    """从固定 TLE 生成真值轨道 + 真值多普勒观测（无噪声）。

    返回: times(N,), truth_states(N,7), truth_fd(N,), rx_pos, rx_vel。
    """
    rx_pos = od.geodetic_to_ecef(RX_LAT, RX_LON, RX_ALT)
    rx_vel = np.array([-od.OMEGA_E * rx_pos[1], od.OMEGA_E * rx_pos[0], 0.0])
    times = T_BASE + np.linspace(0.0, pass_s, n_obs)
    truth_states = np.zeros((n_obs, 7))
    truth_fd = np.zeros(n_obs)
    for i, t in enumerate(times):
        r, v = od.leosat_state_from_tle(TLE_L1, TLE_L2, float(t))
        truth_states[i, 0:3] = r
        truth_states[i, 3:6] = v
        rho_dot, _ = od.pseudorange_rate_and_jacobian(
            truth_states[i], rx_pos, rx_vel)
        truth_fd[i] = od.rangerate_to_fd(rho_dot)
    return times, truth_states, truth_fd, rx_pos, rx_vel


def run_ekf(times, obs_fd, truth_states, rx_pos, rx_vel,
            init_state, r_sigma):
    """逐历元 EKF，记录每步三维位置误差 vs 真值。返回 (times, err_km, residuals)。"""
    q_sigma = np.array([1e-5, 1e-5, 1e-6, 1e-6, 1e-6, 1e-6, 1e-6])
    init_P = np.diag([5.0 ** 2] * 3 + [0.05 ** 2] * 3 + [0.01 ** 2])
    ekf = od.ExtendedKalmanFilter(init_state, init_P, q_sigma, r_sigma)
    errs, resids = [], []
    t_prev = times[0]
    for i, t in enumerate(times):
        ekf.predict(t - t_prev)
        rho_dot = od.fd_to_rangerate(obs_fd[i])
        y = ekf.update(rho_dot, rx_pos, rx_vel)
        t_prev = t
        err = float(np.linalg.norm(ekf.state[0:3] - truth_states[i, 0:3]))
        errs.append(err)
        resids.append(y)
    return np.array(errs), np.array(resids)


def run_rls_at_k(times, obs_fd, truth_states, rx_pos, rx_vel,
                 init_state, r_sigma, k):
    """用前 k 个观测做参考历元 RLS，返回该 k 处末历元三维位置误差。"""
    init_P = np.diag([5.0 ** 2] * 3 + [0.05 ** 2] * 3 + [0.01 ** 2])
    obs = []
    for i in range(k):
        obs.append((float(times[i]), od.fd_to_rangerate(obs_fd[i]),
                    rx_pos, rx_vel))
    t0 = float(times[0])
    x_ref, _, _ = od.reference_epoch_rls_update(
        init_state, init_P, obs, t0, r_sigma)
    # 传播到第 k-1 个观测历元，与真值比
    s_end, _ = od.propagate_ecef_state_and_stm(x_ref, times[k - 1] - t0)
    return float(np.linalg.norm(s_end[0:3] - truth_states[k - 1, 0:3]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--n-obs", type=int, default=31)
    ap.add_argument("--pass-s", type=float, default=600.0)
    ap.add_argument("--sigmas", type=str, default="1,5,10,20,50",
                    help="多普勒观测噪声标准差序列（Hz）")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    sigmas = [float(s) for s in args.sigmas.split(",")]
    runner_obj = runner.ExperimentRunner(args.seed)

    times, truth_states, truth_fd, rx_pos, rx_vel = build_truth(
        args.n_obs, args.pass_s)

    # 初值扰动：真值 + 5 km 位置 / 0.05 km/s 速度
    perturb_r = np.array([5.0, -3.0, 2.0])
    perturb_v = np.array([0.03, -0.02, 0.04])

    conv_rows = []     # 逐观测误差曲线
    floor_rows = []    # 每 sigma 的收敛地板
    rep_sigma = 1.0
    rep_ekf_curve = None
    rep_rls_curve = None
    rep_rls_t = None

    for sigma in sigmas:
        rng = runner_obj.make_rng(f"doppler_sigma{sigma}")
        noise = rng.normal(0.0, sigma, size=len(times))
        obs_fd = truth_fd + noise

        init_state = truth_states[0].copy()
        init_state[0:3] += perturb_r
        init_state[3:6] += perturb_v
        r_sigma = od.C_LIGHT_KMS / od.F0_DEFAULT_HZ * sigma

        ekf_errs, resids = run_ekf(
            times, obs_fd, truth_states, rx_pos, rx_vel, init_state, r_sigma)

        # RLS 随观测数增长的收敛曲线（抽样 k）
        rls_errs, rls_t = [], []
        for k in range(5, args.n_obs + 1, 3):
            e = run_rls_at_k(times, obs_fd, truth_states, rx_pos, rx_vel,
                             init_state, r_sigma, k)
            rls_errs.append(e)
            rls_t.append(times[k - 1] - times[0])

        for i in range(args.n_obs):
            conv_rows.append({
                "sigma_fd_hz": sigma, "obs_index": i,
                "t_s": round(float(times[i] - times[0]), 2),
                "method": "ekf", "pos_err_km": round(float(ekf_errs[i]), 4),
                "data_origin": ORIGIN,
            })
        floor_rows.append({
            "sigma_fd_hz": sigma,
            "ekf_final_err_km": round(float(ekf_errs[-1]), 4),
            "rls_final_err_km": round(float(rls_errs[-1]), 4),
            "n_obs": args.n_obs, "data_origin": ORIGIN,
        })
        print(f"[sigma_fd={sigma:5.1f} Hz] EKF 末误差={ekf_errs[-1]:7.3f} km | "
              f"RLS 末误差={rls_errs[-1]:7.3f} km", flush=True)

        if abs(sigma - rep_sigma) < 1e-6:
            rep_ekf_curve = ekf_errs
            rep_rls_curve = np.array(rls_errs)
            rep_rls_t = np.array(rls_t)

    # --- CSV ---
    os.makedirs(args.out, exist_ok=True)
    p1 = os.path.join(args.out, "doppler_convergence.csv")
    with open(p1, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(conv_rows[0].keys()))
        w.writeheader(); w.writerows(conv_rows)
    p2 = os.path.join(args.out, "doppler_floor.csv")
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(floor_rows[0].keys()))
        w.writeheader(); w.writerows(floor_rows)
    print(f"\n[写] {p1}\n[写] {p2}")

    # --- 收敛曲线图（代表 sigma=10Hz，EKF 逐历元 + RLS 增长窗）---
    t_ekf = times - times[0]
    curves = {"EKF": rep_ekf_curve}
    curves_x = {"EKF": t_ekf}
    if rep_rls_curve is not None:
        curves["RLS"] = rep_rls_curve
        curves_x["RLS"] = rep_rls_t
    n_samples = args.n_obs * len(sigmas)
    fig_path = eplot.plot_convergence(
        t_ekf, curves, origin=ORIGIN, n_samples=n_samples,
        out_dir=FIG_DIR, fname_prefix="doppler_convergence",
        title=f"Orbit determination convergence (sigma_fd={rep_sigma} Hz)",
        curves_x=curves_x)
    print(f"[写] {fig_path}")

    # --- manifest ---
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"n_obs": args.n_obs, "pass_s": args.pass_s,
                "sigma_fd_hz": sigmas, "tle": "ISS 25544",
                "station": [RX_LAT, RX_LON, RX_ALT],
                "csv_convergence": os.path.basename(p1),
                "csv_floor": os.path.basename(p2),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_doppler.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；观测点 N={n_samples}（{len(sigmas)} 个噪声档 × {args.n_obs} 历元）")


if __name__ == "__main__":
    main()
