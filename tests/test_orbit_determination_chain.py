# SPDX-License-Identifier: MIT
"""观测 → 定轨链路：可复跑、缺输入必报错。

链路（全部为确定性函数，不联网、不随机）：

    TLE ──leosat_state_from_tle──▶ ECEF 7 维初值
    IQ  ──extract_doppler_observations──▶ [(t_unix, f_d), ...]
    obs + 初值 + 站点 ──doppler_orbit_determine──▶ 轨道估计

判据：
1. 同一段 IQ、同一初值，跑两次结果逐位一致（不是「接近」）；
2. 观测为空 / 只有 1 点，必须抛错，不得返回看似合理的轨道；
3. 空 IQ 提取不到观测，返回空列表（调用方据此拒绝定轨）。
无 UI、无网络；把本文件当链路的可复跑入口。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.orbit_determination import (
    OMEGA_E,
    doppler_orbit_determine,
    extract_doppler_observations,
    geodetic_to_ecef,
    leosat_state_from_tle,
)

ISS_L1 = ("1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
          ).ljust(69)
ISS_L2 = ("2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
          ).ljust(69)

LAT_DEG, LON_DEG, ALT_M = 39.9, 116.4, 50.0
F0_HZ = 435.0e6
FS_HZ = 48000.0
DURATION_S = 20.0
EPOCH_UNIX = 1222351200.0     # 2008-09-25 附近，与 TLE 历元同量级


def _station_geometry():
    rx = geodetic_to_ecef(LAT_DEG, LON_DEG, ALT_M / 1000.0)
    rx_vel = np.array([-OMEGA_E * rx[1], OMEGA_E * rx[0], 0.0])
    return rx, rx_vel


def _doppler_series(t0: float, fs: float, duration_s: float) -> np.ndarray:
    """按 TLE 真实轨迹算逐样本多普勒（Hz），用于合成 IQ。

    几何按 20 Hz 粗采样再插值到样本率——逐样本传播 96 万次没有意义，
    多普勒在秒级尺度上变化平缓。
    """
    rx, rx_vel = _station_geometry()
    n = int(round(duration_s * fs))
    grid_dt = 0.05
    grid_t = t0 + np.arange(0.0, duration_s + grid_dt, grid_dt)
    grid_fd = np.empty(len(grid_t), dtype=np.float64)
    for i, ti in enumerate(grid_t):
        r, v = leosat_state_from_tle(ISS_L1, ISS_L2, float(ti))
        los = r - rx
        u = los / np.linalg.norm(los)
        rr = float(np.dot(u, v - rx_vel))
        grid_fd[i] = -rr * 1000.0 / 299792458.0 * F0_HZ
    return np.interp(t0 + np.arange(n) / fs, grid_t, grid_fd)


def _synthesize_iq(fds: np.ndarray, fs: float) -> np.ndarray:
    """把瞬时频偏合成为复基带 IQ（相位连续、无随机数）。"""
    phase = 2.0 * np.pi * np.cumsum(fds) / fs
    return np.exp(1j * phase).astype(np.complex64)


@pytest.fixture(scope="module")
def chain_inputs():
    t0 = EPOCH_UNIX
    fds = _doppler_series(t0, FS_HZ, DURATION_S)
    iq = _synthesize_iq(fds, FS_HZ)
    obs = extract_doppler_observations(
        iq, FS_HZ, f0=F0_HZ, t0=t0, window_s=0.4, hop_s=0.2,
        snr_threshold_db=8.0, min_observations=5)
    r0, v0 = leosat_state_from_tle(ISS_L1, ISS_L2, t0)
    init_state = np.concatenate([r0, v0, [0.0]])
    return {"t0": t0, "obs": obs, "init_state": init_state}


def test_iq_to_observations_is_nonempty(chain_inputs):
    assert len(chain_inputs["obs"]) >= 20, "合成 IQ 应能检出足够观测"
    # 观测频偏应落在合成曲线量级内（不是常数 0）
    vals = np.array([v for _, v in chain_inputs["obs"]])
    assert abs(vals.mean()) < 12000.0
    assert vals.std() > 1.0


def test_chain_runs_twice_bit_identical(chain_inputs):
    obs = chain_inputs["obs"]
    init = chain_inputs["init_state"]
    kw = dict(rx_lat=LAT_DEG, rx_lon=LON_DEG, rx_alt=ALT_M,
              init_state=init, f0=F0_HZ, estimator="ekf")
    a = doppler_orbit_determine(obs, **kw)
    b = doppler_orbit_determine(obs, **kw)

    assert np.array_equal(np.asarray(a["state"]), np.asarray(b["state"]))
    assert np.array_equal(a["positions_ecef"], b["positions_ecef"])
    assert np.array_equal(a["residuals"], b["residuals"])
    assert a["residual_stats"] == b["residual_stats"]
    assert np.all(np.isfinite(a["state"]))


def test_estimate_stays_near_tle_truth(chain_inputs):
    """20s 弧段只能弱约束，这里只要求不跑到轨道外（量级校验）。"""
    obs = chain_inputs["obs"]
    res = doppler_orbit_determine(
        obs, rx_lat=LAT_DEG, rx_lon=LON_DEG, rx_alt=ALT_M,
        init_state=chain_inputs["init_state"], f0=F0_HZ, estimator="ekf")
    t_last = obs[-1][0]
    r_true, _ = leosat_state_from_tle(ISS_L1, ISS_L2, t_last)
    err_km = float(np.linalg.norm(np.asarray(res["state"])[0:3] - r_true))
    assert err_km < 50.0, f"估计偏离 TLE 真值 {err_km:.1f} km"


# ── 缺输入必须报错，不许给「看似合理」的结果 ──────────────────
def test_empty_observations_raise():
    init = np.zeros(7)
    with pytest.raises(ValueError, match="观测不足"):
        doppler_orbit_determine([], rx_lat=LAT_DEG, rx_lon=LON_DEG, rx_alt=ALT_M,
                                init_state=init, f0=F0_HZ)


def test_single_observation_raises():
    init = np.zeros(7)
    with pytest.raises(ValueError, match="观测不足"):
        doppler_orbit_determine([(EPOCH_UNIX, 123.0)],
                                rx_lat=LAT_DEG, rx_lon=LON_DEG, rx_alt=ALT_M,
                                init_state=init, f0=F0_HZ)


def test_empty_iq_yields_no_observations():
    obs = extract_doppler_observations(np.array([], dtype=np.complex64), FS_HZ,
                                       f0=F0_HZ, t0=EPOCH_UNIX)
    assert obs == []
