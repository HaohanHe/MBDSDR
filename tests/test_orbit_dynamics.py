# SPDX-License-Identifier: MIT
"""轨道动力学原子积木正确性（合成自洽，诚实标注 synthetic，非 OTA）。

验证 AGENTS.md「原子能力」积木的数学正确性：
1. STM 正确性：有限差分 vs 解析变分方程（列比对）。
2. 伪距率观测模型：已知几何下的多普勒 + 雅可比有限差分。
3. RLS 自洽：由已知轨道正演合成观测 -> RLS 复原状态、协方差单调下降。
4. 坐标转换：ECEF<->ECI<->ENU<->az/el 往返一致性。
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai import orbit_dynamics as od  # noqa: E402


def _leo_state(t: float) -> np.ndarray:
    """解析圆轨道参考状态（ECI，再转 ECEF）——测试真值发生器。"""
    r = 7.0e6
    n = np.sqrt(od.MU_EARTH / r**3)
    pos_eci = np.array([r * np.cos(n * t), r * np.sin(n * t), 0.0])
    vel_eci = np.array([-r * n * np.sin(n * t), r * n * np.cos(n * t), 0.0])
    p, v = od.eci_to_ecef(pos_eci, vel_eci, t)
    return np.hstack((p, v, 1e-9))


class TestSTM:
    def test_stm_matches_finite_difference(self):
        """STM 列 = 状态对初值的偏导（中心差分验证）。"""
        t0 = 1.7e9
        dt = 120.0
        x0 = _leo_state(t0)
        _, stm = od.propagate_state_and_stm(x0, t0, t0 + dt)

        eps_pos, eps_vel = 1.0, 1e-3
        fd = np.zeros((6, 6))
        for i in range(6):
            eps = eps_pos if i < 3 else eps_vel
            xp = x0.copy(); xp[i] += eps
            xm = x0.copy(); xm[i] -= eps
            fp, _ = od.propagate_state_and_stm(xp, t0, t0 + dt)
            fm, _ = od.propagate_state_and_stm(xm, t0, t0 + dt)
            fd[:, i] = (fp[:6] - fm[:6]) / (2.0 * eps)

        analytic = stm[:6, :6]
        denom = np.maximum(np.abs(fd), 1e-6)
        rel = np.max(np.abs(analytic - fd) / denom)
        assert rel < 1e-3, f"STM 与有限差分偏离: max_rel={rel:.3e}"

    def test_zero_dt_identity(self):
        x0 = _leo_state(1.7e9)
        state, stm = od.propagate_state_and_stm(x0, 1.7e9, 1.7e9)
        assert np.allclose(state, x0)
        assert np.allclose(stm, np.eye(7))


class TestObservationModel:
    def test_pseudorange_rate_known_geometry(self):
        """沿视线速度即伪距率（钟漂 0）。"""
        rec = np.array([od.WGS72_A, 0.0, 0.0])
        sat = np.array([od.WGS72_A + 5.0e5, 0.0, 0.0])
        vel = np.array([0.0, 0.0, 7500.0])  # 垂直视线 -> 径向速度 0
        st = np.hstack((sat, vel, 0.0))
        rate, _ = od.pseudorange_rate_and_jacobian(st, rec)
        assert abs(rate) < 1e-6

    def test_jacobian_finite_difference(self):
        rec = np.array([-2.63785e6, 3.50512e6, 4.6147e6])
        st = _leo_state(1.7e9)
        _, jac = od.pseudorange_rate_and_jacobian(st, rec)

        fd = np.zeros(7)
        for i in range(7):
            eps = 1e-3 if i < 3 else (1e-6 if i < 6 else 1e-12)
            sp = st.copy(); sp[i] += eps
            sm = st.copy(); sm[i] -= eps
            rp, _ = od.pseudorange_rate_and_jacobian(sp, rec)
            rm, _ = od.pseudorange_rate_and_jacobian(sm, rec)
            fd[i] = (rp - rm) / (2.0 * eps)
        assert np.allclose(jac, fd, rtol=1e-4, atol=1e-6)

    def test_doppler_roundtrip(self):
        fc = 1626.270833e6
        rr = 250.0
        f = fc - rr * fc / od.C_LIGHT  # range_rate = -c*(f-fc)/fc
        assert abs(od.observed_pseudorange_rate(f, fc) - rr) < 1e-6


class TestRLS:
    def test_truth_init_zero_noise_residual_zero(self):
        """真值初值 + 零噪声 -> 后验残差 ~0（动力学+观测模型自洽）。"""
        t0 = 1.7e9
        rec = np.array([-2.63785e6, 3.50512e6, 4.6147e6])
        true0 = _leo_state(t0)
        sig = 20.0
        x0 = true0.copy()
        cov = np.diag(np.array([3e3, 3e3, 3e3, 3.0, 3.0, 3.0, 3e-8]) ** 2)
        worst = 0.0
        for k in range(1, 20):
            tk = t0 + 8.0 * k
            true_k, _ = od.propagate_state_and_stm(true0, t0, tk)
            obs, _ = od.pseudorange_rate_and_jacobian(true_k, rec)  # 零噪声
            out = od.rls_update(x0, cov, t0, tk, obs, rec, sig)
            x0, cov = out["ref_state"], out["ref_cov"]
            worst = max(worst, abs(out["postfit_residual"]))
        assert worst < 1e-6, f"真值初值零噪声后验残差应 ~0，实测 {worst:.3e}"

    def test_reference_covariance_monotonic_decrease(self):
        """参考历元位置协方差单调下降（真实收敛签名，参照真实 Iridium-107 数据）。

        判据依据：单站纯多普勒存在沿迹位置+钟漂弱可观测性，绝对位置误差
        不保证归零；收敛的正确签名是参考历元协方差单调下降（真实数据实测
        44655 -> 30930 m 单调降）。
        """
        t0 = 1.7e9
        rec = np.array([-2.63785e6, 3.50512e6, 4.6147e6])
        true0 = _leo_state(t0)
        sig = 20.0
        rng = np.random.default_rng(42)
        x0 = true0.copy()
        x0[:3] += rng.normal(0, 3e3, 3)
        x0[3:6] += rng.normal(0, 3.0, 3)
        cov = np.diag(np.array([3e3, 3e3, 3e3, 3.0, 3.0, 3.0, 3e-8]) ** 2)

        prev_sigma = np.inf
        first_sigma = None
        for k in range(1, 40):
            tk = t0 + 8.0 * k
            true_k, _ = od.propagate_state_and_stm(true0, t0, tk)
            obs, _ = od.pseudorange_rate_and_jacobian(true_k, rec)
            obs += rng.normal(0, sig)
            out = od.rls_update(x0, cov, t0, tk, obs, rec, sig)
            x0, cov = out["ref_state"], out["ref_cov"]
            ref_sigma = float(np.sqrt(max(0.0, np.trace(cov[:3, :3]))))
            assert ref_sigma <= prev_sigma + 1e-6, "参考历元位置协方差须单调下降"
            if first_sigma is None:
                first_sigma = ref_sigma
            prev_sigma = ref_sigma

        assert prev_sigma < first_sigma, (
            f"协方差应净下降: {first_sigma:.0f} -> {prev_sigma:.0f}"
        )

    def test_validate_state_rejects_bad(self):
        with pytest.raises(ValueError):
            od.validate_state(np.array([1e5, 0, 0, 0, 0, 0, 0]))


class TestCoordinates:
    def test_ecef_eci_roundtrip(self):
        t = 1.7e9
        p = np.array([6.5e6, 1.2e6, 2.3e6])
        v = np.array([100.0, -200.0, 300.0])
        pe, ve = od.ecef_to_eci(p, v, t)
        p2, v2 = od.eci_to_ecef(pe, ve, t)
        assert np.allclose(p2, p, atol=1e-6)
        assert np.allclose(v2, v, atol=1e-6)

    def test_geodetic_ecef_roundtrip(self):
        lat, lon, alt = 39.9, 116.4, 50.0
        ecef = od.geodetic_to_ecef(lat, lon, alt)
        lon2, lat2, alt2 = od.ecef_to_geodetic(ecef)
        assert abs(lat - lat2) < 1e-6 and abs(lon - lon2) < 1e-6
        assert abs(alt - alt2) < 1e-3

    def test_enu_az_el_zenith(self):
        """正上方目标：仰角 90°。"""
        rec = od.geodetic_to_ecef(39.9, 116.4, 0.0)
        lon, lat, _ = od.ecef_to_geodetic(rec)
        lon_r, lat_r = np.radians(lon), np.radians(lat)
        up = np.array([
            np.cos(lat_r) * np.cos(lon_r),
            np.cos(lat_r) * np.sin(lon_r),
            np.sin(lat_r),
        ]) * 1e5 + rec
        az, el = od.enu_to_az_el(od.ecef_to_enu(up, rec))
        assert el > 89.0
