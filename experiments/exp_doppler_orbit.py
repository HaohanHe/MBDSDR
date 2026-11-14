#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：多普勒定轨收敛（仿真口径，固定 TLE）
==============================================

闭环（B2 审计 §2.4）：固定 TLE -> sgp4 真值过境 -> 在示例站逐历元算真多普勒频偏
-> 加高斯噪声（固定种子）-> 初值给 ~5km/0.05km/s 扰动 -> EKF 与参考历元 RLS
解算 -> 记录三维位置误差随观测数/时间的收敛曲线。

全部调用 mbdsdr_ai.orbit_determination 既有真实零件（leosat_state_from_tle /
pseudorange_rate_and_jacobian / rangerate_to_fd / ExtendedKalmanFilter /
reference_epoch_rls_update / position_error_curve），不另写估计器。

口径：synthetic（仿真）。真值由 sgp4 从固定 TLE 生成，观测=真值+固定种子噪声。
无真实 IQ 录制时**不**生成任何 OTA/录制产物；真实过境 IQ 验证留待用户录制。

IQ 文件模式（观测 -> 定轨的最小入口，无 UI）
--------------------------------------------
传 ``--iq`` 即切到 IQ 文件模式：``IQ 文件 + --fs + --f0 + --t0 + TLE + 站址`` ->
``load_iq_file`` -> ``extract_doppler_observations`` -> ``leosat_state_from_tle``
取 7 维初值 -> ``doppler_orbit_determine``。输出估计轨道、相对 TLE 真值的偏差、
用了多少观测，并把输入（IQ sha256/大小、TLE 两行与历元、站址、中心频率、
提取参数）与结论落成 ``docs/evidence/`` 下的快照（结构与
``mbdsdr_ai.environment_evidence`` 一致：schema / captured_at_utc / inputs /
result / status）。

红线：缺 IQ / 缺 TLE / 缺站址 / 缺中心频率 / 缺观测起始时刻 / IQ 为空 /
观测不足，一律 stderr 报错并退出码 2，**不产轨道、不写快照**；不设默认轨道、
不拿缺值当良性。判据见 ``tests/test_orbit_determination_cli.py``。

用法::

    python experiments/exp_doppler_orbit.py --iq rec.cf32 --fs 48000 \
        --f0 435e6 --t0 1222351200 --tle-file iss.tle \
        --lat 39.9 --lon 116.4 --alt 50
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import os
import sys
from typing import List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai import orbit_determination as od  # noqa: E402
from mbdsdr_ai import environment_evidence as env_evidence  # noqa: E402
from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")

# 固定 TLE（ISS/ZARYA，公开发布）；真值传播用 sgp4。
TLE_L1 = ("1 25544U 98067A   24275.50000000  .00016717  00000-0  10270-3 0  9000")
TLE_L2 = ("2 25544  51.6400 292.0000 0001333  90.0000 270.0000 15.50000000    000")
# 过境窗口：仰角 0.8->71->2.6 deg（示例站 45.0N,120.0E，中性示例坐标）
T_BASE = datetime.datetime(2024, 10, 1, 12, 0, 0,
                           tzinfo=datetime.timezone.utc).timestamp() + 6240.0
RX_LAT, RX_LON, RX_ALT = 45.0, 120.0, 0.0


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


# ═══════════════════════════════════════════════════════════════════════════
# IQ 文件定轨模式：观测 -> 定轨的最小入口（无 UI；判据见
# tests/test_orbit_determination_cli.py）
# ═══════════════════════════════════════════════════════════════════════════
IQ_FILE_ORIGIN = "iq_file"
ORBIT_EVIDENCE_SCHEMA = "mbdsdr.orbit_determination_evidence/1"
DEFAULT_EVIDENCE_DIR = os.path.join(ROOT, "docs", "evidence")


def _err(msg: str) -> int:
    """缺输入 / 无法定轨：只在 stderr 说清缺什么，不产轨道、不写快照。"""
    print(f"[错误] {msg}", file=sys.stderr)
    return 2


def _iso_utc(unix_s: float) -> str:
    return datetime.datetime.fromtimestamp(
        float(unix_s), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tle_epoch_unix(line1: str) -> float:
    """TLE 第 1 行第 19-32 列（YYDDD.DDDDDDDD）-> unix 秒。"""
    field = line1[18:32].strip()
    yy = int(field[:2])
    year = 2000 + yy if yy < 57 else 1900 + yy
    base = datetime.datetime(year, 1, 1,
                             tzinfo=datetime.timezone.utc).timestamp()
    return base + (float(field[2:]) - 1.0) * 86400.0


def _validate_tle(line1: str, line2: str) -> Tuple[str, str]:
    line1, line2 = line1.strip(), line2.strip()
    if not (line1.startswith("1 ") and line2.startswith("2 ")) \
            or len(line1) < 32 or len(line2) < 7:
        raise ValueError("TLE 格式不对：需要以 '1 ' / '2 ' 开头的两行 TLE")
    return line1, line2


def _read_tle(args: argparse.Namespace) -> Tuple[str, str]:
    if args.tle_file:
        if not os.path.exists(args.tle_file):
            raise ValueError(f"TLE 文件不存在: {args.tle_file}")
        with open(args.tle_file, "r", encoding="utf-8") as fh:
            rows = [ln.strip() for ln in fh
                    if ln.strip() and not ln.lstrip().startswith("#")]
        if len(rows) < 2:
            raise ValueError(f"TLE 文件不足两行（实际 {len(rows)} 行）: "
                             f"{args.tle_file}")
        return _validate_tle(rows[0], rows[1])
    if not (args.tle_line1 and args.tle_line2):
        raise ValueError("缺少 TLE：请用 --tle-file 给两行 TLE 文件，"
                         "或同时传 --tle-line1/--tle-line2")
    return _validate_tle(args.tle_line1, args.tle_line2)


def _parse_t0(args: argparse.Namespace) -> float:
    if args.t0 is not None:
        return float(args.t0)
    if args.t0_utc:
        dt = datetime.datetime.fromisoformat(
            args.t0_utc.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return float(dt.timestamp())
    raise ValueError("缺少观测起始时刻：请传 --t0（unix 秒）或 --t0-utc（ISO8601）")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_recorded_iq(args: argparse.Namespace) -> int:
    """IQ + TLE + 站址 + 中心频率 -> 轨道估计 + 偏差 + 证据快照。

    任一必需输入缺失、IQ 为空、观测不足：退出码 2，不产轨道、不写快照。
    """
    try:
        return _run_recorded_iq(args)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"证据快照写入失败: {exc}")


def _run_recorded_iq(args: argparse.Namespace) -> int:
    if not args.iq:
        raise ValueError("缺少 IQ 文件：请传 --iq（.cf32/.raw/.iq/.wav）")
    if not os.path.exists(args.iq):
        raise ValueError(f"IQ 文件不存在: {args.iq}")
    if args.f0 is None or float(args.f0) <= 0.0:
        raise ValueError("缺少中心频率：请传 --f0（Hz，需 > 0）")
    if args.lat is None or args.lon is None or args.alt is None:
        raise ValueError("缺少观测站坐标：--lat/--lon/--alt 均为必填"
                         "（度/度/米）")
    t0 = _parse_t0(args)
    line1, line2 = _read_tle(args)
    if not args.iq.lower().endswith(".wav") and args.fs is None:
        raise ValueError("缺少采样率：裸 IQ 文件必须传 --fs（Hz）")

    iq, fs = od.load_iq_file(args.iq, sample_rate=args.fs)
    if iq.size == 0:
        raise ValueError("IQ 为空（0 样本）：不产轨道")

    obs = od.extract_doppler_observations(
        iq, fs, f0=float(args.f0), t0=t0,
        window_s=args.window_s, hop_s=args.hop_s,
        snr_threshold_db=args.snr_threshold_db,
        min_observations=args.min_observations)
    if len(obs) < 2 or len(obs) < int(args.min_observations):
        raise ValueError(
            f"观测不足：有效多普勒观测少于 {max(2, int(args.min_observations))} "
            "点（或 IQ 内未检出信号），不产轨道")

    epoch_unix = _tle_epoch_unix(line1)
    sat_number = line2[2:7].strip()
    tle_sha = hashlib.sha256(f"{line1}\n{line2}".encode("utf-8")).hexdigest()

    r0, v0 = od.leosat_state_from_tle(line1, line2, t0)
    res = od.doppler_orbit_determine(
        obs, rx_lat=float(args.lat), rx_lon=float(args.lon),
        rx_alt=float(args.alt),
        init_state=np.concatenate([r0, v0, [0.0]]),
        f0=float(args.f0), estimator=args.estimator)

    state = np.asarray(res.get("state", res.get("state_ref")), dtype=float)
    t_last = float(obs[-1][0])
    r_truth, _ = od.leosat_state_from_tle(line1, line2, t_last)
    deviation_km = float(np.linalg.norm(state[0:3] - r_truth))
    stats = {k: float(v) for k, v in dict(res.get("residual_stats") or {}).items()}

    captured = datetime.datetime.now(datetime.timezone.utc)
    snapshot = {
        "schema": ORBIT_EVIDENCE_SCHEMA,
        "captured_at_utc": captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "iq": {"path": os.path.abspath(args.iq),
                   "sha256": _sha256_file(args.iq),
                   "bytes": os.path.getsize(args.iq),
                   "n_samples": int(iq.size)},
            "sample_rate_hz": float(fs),
            "center_freq_hz": float(args.f0),
            "t0_unix": t0,
            "t0_utc": _iso_utc(t0),
            "tle": {"line1": line1, "line2": line2,
                    "satellite_number": sat_number,
                    "epoch_unix": epoch_unix,
                    "epoch_utc": _iso_utc(epoch_unix),
                    "age_hours_at_t0": (t0 - epoch_unix) / 3600.0,
                    "file": (os.path.abspath(args.tle_file)
                             if args.tle_file else None),
                    "sha256": tle_sha},
            "station": {"lat_deg": float(args.lat),
                        "lon_deg": float(args.lon),
                        "alt_m": float(args.alt)},
            "extraction": {"window_s": float(args.window_s),
                           "hop_s": float(args.hop_s),
                           "snr_threshold_db": float(args.snr_threshold_db),
                           "min_observations": int(args.min_observations)},
            "estimator": args.estimator,
        },
        "result": {
            "data_origin": IQ_FILE_ORIGIN,
            "status": "ok",
            "n_observations": len(obs),
            "observation_first_unix": float(obs[0][0]),
            "observation_last_unix": t_last,
            "observation_span_utc": [_iso_utc(obs[0][0]), _iso_utc(t_last)],
            "final_state_ecef": state.tolist(),
            "position_ecef_km": state[0:3].tolist(),
            "velocity_ecef_kms": state[3:6].tolist(),
            "tle_truth_at": {"unix": t_last, "utc": _iso_utc(t_last),
                             "position_ecef_km": r_truth.tolist()},
            "deviation_km": deviation_km,
            "residual_stats": stats,
        },
        "status": {"complete": True, "uncorrected": []},
    }
    out = args.evidence_out or os.path.join(
        DEFAULT_EVIDENCE_DIR,
        f"orbit-determine-{captured.strftime('%Y%m%d-%H%M%S')}.json")
    env_evidence.save(out, snapshot)

    print(f"[TLE] NORAD {sat_number} | epoch {_iso_utc(epoch_unix)} | "
          f"t0 处年龄 {(t0 - epoch_unix) / 3600.0:.2f} h | "
          f"sha256 {tle_sha[:16]} | 来源 {args.tle_file or '命令行两行'}")
    print(f"[站址] lat={float(args.lat):.4f} lon={float(args.lon):.4f} "
          f"alt={float(args.alt):.1f} m")
    print(f"[观测] N={len(obs)} | {_iso_utc(obs[0][0])} -> {_iso_utc(t_last)}"
          f"（{t_last - obs[0][0]:.1f} s）")
    print(f"[估计] ECEF x={state[0]:.3f} y={state[1]:.3f} z={state[2]:.3f} km | "
          f"v=({state[3]:.4f}, {state[4]:.4f}, {state[5]:.4f}) km/s")
    print(f"[偏差] {deviation_km:.3f} km（相对 TLE 真值 @ {_iso_utc(t_last)}）")
    print(f"[残差] mean={stats.get('mean', 0.0):.3e} "
          f"rms={stats.get('rms', 0.0):.3e} km/s")
    print(f"[快照] {out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="多普勒定轨：仿真收敛实验 / 录制 IQ 定轨（--iq）")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--n-obs", type=int, default=31)
    ap.add_argument("--pass-s", type=float, default=600.0)
    ap.add_argument("--sigmas", type=str, default="1,5,10,20,50",
                    help="多普勒观测噪声标准差序列（Hz）")
    ap.add_argument("--out", default=OUT_DIR)
    # —— IQ 文件定轨模式（传 --iq 即启用；无 --iq 时行为与仿真实验一致）——
    ap.add_argument("--iq", default=None,
                    help="IQ 文件（.cf32/.raw/.iq/.wav）；传了即走 IQ 定轨模式")
    ap.add_argument("--fs", type=float, default=None,
                    help="采样率 Hz（裸 IQ 必填；.wav 从文件头读取）")
    ap.add_argument("--f0", type=float, default=None,
                    help="中心频率 Hz（IQ 定轨模式必填）")
    ap.add_argument("--t0", type=float, default=None,
                    help="观测起始 unix 秒（与 --t0-utc 二选一）")
    ap.add_argument("--t0-utc", default=None,
                    help="观测起始 UTC，ISO8601（与 --t0 二选一）")
    ap.add_argument("--tle-file", default=None, help="两行 TLE 文件")
    ap.add_argument("--tle-line1", default=None)
    ap.add_argument("--tle-line2", default=None)
    ap.add_argument("--lat", type=float, default=None, help="观测站纬度（度）")
    ap.add_argument("--lon", type=float, default=None, help="观测站经度（度）")
    ap.add_argument("--alt", type=float, default=None, help="观测站海拔（米）")
    ap.add_argument("--window-s", type=float, default=0.5)
    ap.add_argument("--hop-s", type=float, default=0.25)
    ap.add_argument("--snr-threshold-db", type=float, default=8.0)
    ap.add_argument("--min-observations", type=int, default=5)
    ap.add_argument("--estimator", default="ekf", choices=["ekf", "rls", "ls"])
    ap.add_argument("--evidence-out", default=None,
                    help="证据快照路径（默认 docs/evidence/orbit-determine-<UTC>.json）")
    args = ap.parse_args(argv)

    if args.iq:
        return run_recorded_iq(args)

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
        r_sigma = od.C_LIGHT_KMPS / od.F0_DEFAULT_HZ * sigma

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
