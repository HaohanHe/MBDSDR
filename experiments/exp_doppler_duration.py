#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：定轨收敛 vs 观测时长（仿真口径，固定 TLE/种子/噪声）
==========================================================

问题：单次过境里，观测窗口拉多长，RLS 定轨的三维位置误差才能收敛？
固定 TLE(ISS)、固定台站(长春)、固定多普勒噪声 sigma、固定种子，只扫
观测窗长 T（秒）。每个窗长用窗内全部观测做参考历元 RLS，取末历元位置误差。

预期：窗太短（几何变化不足，单站多普勒观测性弱）-> 误差大；
窗拉长 -> 仰角升高、几何增强 -> 误差收敛到地板。这是单站多普勒观测性
的固有特性（与 exp_doppler_orbit 的诚实局限一致）。

口径：synthetic（仿真）。真值 sgp4 生成，观测=真值+固定种子噪声。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai import orbit_determination as od  # noqa: E402
from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402
# 复用同一条闭环的真值生成与 RLS（避免估计器漂移）
from experiments.exp_doppler_orbit import (  # noqa: E402
    build_truth,
)

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

FIXED_SIGMA_HZ = 10.0          # 固定多普勒观测噪声（Hz）
DT_OBS = 20.0                  # 固定观测采样间隔（s）
# 与 exp_doppler_orbit 同一套初值扰动（真值 + 5km/0.05km/s）
PERTURB_R = np.array([5.0, -3.0, 2.0])
PERTURB_V = np.array([0.03, -0.02, 0.04])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--window-grid", type=str,
                    default="60,120,200,300,450,600",
                    help="观测窗长网格(s)")
    ap.add_argument("--sigma-hz", type=float, default=FIXED_SIGMA_HZ)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    windows = [float(t) for t in args.window_grid.split(",")]
    runner_obj = runner.ExperimentRunner(args.seed)
    r_sigma = od.C_LIGHT_KMS / od.F0_DEFAULT_HZ * args.sigma_hz

    # 整条过境（600s）真值：仰角 0.8->71->2.6 deg，中过境(t=300s)仰角最高。
    # 参考历元取在中过境（几何最好），扫围绕它的观测窗长 -> 观测性随窗长改善。
    from experiments.exp_doppler_orbit import T_BASE
    PASS_S = 600.0
    N_FULL = 61                       # dt=10s 细网格
    REF_OFFSET = PASS_S / 2.0         # 中过境 = T_BASE + 300s
    times, truth_states, truth_fd, rx_pos, rx_vel = build_truth(N_FULL, PASS_S)
    ref_idx = int(np.argmin(np.abs(times - (T_BASE + REF_OFFSET))))
    ref_epoch = float(times[ref_idx])
    init_P = np.diag([5.0 ** 2] * 3 + [0.05 ** 2] * 3 + [0.01 ** 2])

    rows = []
    xs, errs = [], []
    for T in windows:
        # 选 [ref_epoch - T/2, ref_epoch + T/2] 内的观测（钳制在过境内）
        lo, hi = ref_epoch - T / 2.0, ref_epoch + T / 2.0
        sel = [i for i in range(N_FULL) if lo <= times[i] <= hi]
        n_obs = len(sel)
        # 每窗长独立子流加噪
        rng = runner_obj.make_rng(f"dur_{int(T)}")
        noise = rng.normal(0.0, args.sigma_hz, size=N_FULL)
        obs_fd = truth_fd + noise

        # 参考历元先验 = 中过境真值 + 固定扰动
        init_state = truth_states[ref_idx].copy()
        init_state[0:3] += PERTURB_R
        init_state[3:6] += PERTURB_V

        obs = [(float(times[i]), od.fd_to_rangerate(obs_fd[i]), rx_pos, rx_vel)
               for i in sel]
        x_ref, _, _ = od.reference_epoch_rls_update(
            init_state, init_P, obs, ref_epoch, r_sigma)
        err = float(np.linalg.norm(x_ref[0:3] - truth_states[ref_idx, 0:3]))
        rows.append({
            "window_s": round(float(T), 1),
            "n_obs": n_obs,
            "ref_offset_s_from_peak": REF_OFFSET,
            "sigma_fd_hz": args.sigma_hz,
            "rls_ref_err_km": round(float(err), 4),
            "data_origin": ORIGIN,
        })
        xs.append(float(T)); errs.append(float(err))
        print(f"[窗长 {T:6.1f} s / {n_obs:2d} obs] RLS 参考历元误差 = {err:8.3f} km",
              flush=True)

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "doppler_duration_convergence.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\n[写] {csv_path}")

    n_samples = len(rows)
    fig_path = eplot.plot_convergence(
        None, {"RLS": errs}, origin=ORIGIN, n_samples=n_samples,
        out_dir=FIG_DIR, fname_prefix="doppler_duration_convergence",
        xlabel="Observation window duration around peak elevation (s)",
        ylabel="RLS reference-epoch 3D position error (km)",
        title=f"Orbit accuracy vs observation window (sigma_fd={args.sigma_hz} Hz)",
        curves_x={"RLS": xs})
    print(f"[写] {fig_path}")

    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"window_grid_s": windows,
                "reference_epoch": "中过境仰角峰值(t=T_BASE+300s)",
                "sigma_fd_hz": args.sigma_hz,
                "tle": "ISS 25544（与 exp_doppler_orbit 同一 TLE）",
                "method": "reference-epoch RLS over observations in [t0-T/2,t0+T/2]",
                "metric": "参考历元(t0)三维位置误差",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        extra={"note": "单站多普勒-only：窗短时观测性弱、误差大；窗拉长纳入升/降段观测后收敛"},
        filename="manifest_doppler_duration.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；窗长档 N={n_samples}；固定 TLE/种子/噪声")


if __name__ == "__main__":
    main()
