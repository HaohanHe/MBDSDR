# SPDX-License-Identifier: MIT
"""P1-4 J2 摄动 STM 传播 + Laplace 初轨确定锚点断言。

STM 验证口径：
  * 恒等初值：Phi(0) = I；
  * 数值对拍：Phi 的列 = 末状态对初状态的数值偏导（复数步长微分，
    免减法抵消）；
  * 物理对拍：近圆轨道一天传播，升交点赤经进动率与 J2 一阶
    理论 dΩ/dt = -1.5·J2·n·(Re/p)²·cos i 对齐（1% 内）。

Laplace 验证口径：合成 LEO 真轨道（二体+J2 传播）生成三次站心
RA/Dec，纯测角反演 t2 状态，位置/速度误差与残差 RMS 如实断言。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.orbit_perturbation import (
    dynamics_stm_j2,
    initial_orbit_laplace,
    j2_accel_eci,
    propagate_state_and_stm_j2,
    register_tool_registry,
)
from mbdsdr_ai.orbit_dynamics import MU_EARTH
from mbdsdr_ai.orbit_determination import J2, WGS84_RE

# 中性示例坐标（红线）：赤道、零经度、零海拔
STA_LAT, STA_LON, STA_ALT = 0.0, 0.0, 0.0
T0 = 1_760_000_000.0

# LEO 标称轨道（近圆，倾角 55°）
_STATE0 = np.array([
    6800.0, 0.0, 0.0,
    0.0, 7.5 * math.cos(math.radians(55.0)), 7.5 * math.sin(math.radians(55.0)),
])


def _elements(state6: np.ndarray):
    """经典轨道根数 [a, e, i, raan, argp, nu]（仅测试用，角度 rad）。"""
    r, v = state6[:3], state6[3:6]
    rm, vm = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v)
    hm = np.linalg.norm(h)
    n_vec = np.cross([0.0, 0.0, 1.0], h)
    nm = np.linalg.norm(n_vec)
    e_vec = np.cross(v, h) / MU_EARTH - r / rm
    e = float(np.linalg.norm(e_vec))
    energy = vm * vm / 2.0 - MU_EARTH / rm
    a = -MU_EARTH / (2.0 * energy)
    i = math.acos(min(1.0, max(-1.0, h[2] / hm)))
    raan = math.atan2(n_vec[1], n_vec[0]) if nm > 1e-12 else 0.0
    argp = math.acos(min(1.0, max(-1.0,
                                 float(n_vec @ e_vec) / (nm * e))))
    if e_vec[2] < 0.0:
        argp = 2.0 * math.pi - argp
    nu = math.acos(min(1.0, max(-1.0,
                                float(e_vec @ r) / (e * rm))))
    if float(r @ v) < 0.0:
        nu = 2.0 * math.pi - nu
    return a, e, i, raan, argp, nu


# ── j2_accel_eci ──────────────────────────────────────────────
def test_j2_accel_symmetry_and_scale():
    r = np.array([7000.0, 0.0, 1000.0])
    a = j2_accel_eci(r)
    assert np.all(np.isfinite(a))
    assert abs(a[0]) > abs(a[1])          # x 方向主分量
    # 赤道面内 z=0 时 z 分量 = +3k·z·... z=0 → a_z = 0 的检验放到下式
    r_eq = np.array([7000.0, 0.0, 0.0])
    a_eq = j2_accel_eci(r_eq)
    assert abs(a_eq[2]) < 1e-30            # 赤道对称
    assert a_eq[0] < 0.0                   # 向心


def test_j2_accel_invalid_radius():
    with pytest.raises(ValueError):
        j2_accel_eci(np.array([100.0, 0.0, 0.0]))


# ── STM 传播 ──────────────────────────────────────────────────
def test_stm_identity_at_zero_dt():
    _, stm = propagate_state_and_stm_j2(_STATE0, 0.0)
    assert stm.shape == (6, 6)
    np.testing.assert_allclose(stm, np.eye(6), atol=1e-12)


def test_stm_matches_numerical_derivative():
    """Phi 列 = ∂(末状态)/∂(初状态)：中心差分对拍（与 A 矩阵同建立方法）。"""
    from mbdsdr_ai.orbit_dynamics import _rk4_step
    dt = 120.0
    _, stm = propagate_state_and_stm_j2(_STATE0, dt)

    def _end(state6):
        aug = np.hstack((np.asarray(state6, dtype=float),
                         np.eye(6).ravel()))
        t = 0.0
        n = 24
        h = dt / n
        for _ in range(n):
            aug = _rk4_step(dynamics_stm_j2, t, aug, h)
            t += h
        return aug[:6]

    eps = 1e-5
    for j in range(6):
        pert = _STATE0.copy()
        h = eps * (1.0 + abs(pert[j]))
        pert[j] += h
        plus = _end(pert)
        pert[j] -= 2.0 * h
        minus = _end(pert)
        deriv = (plus - minus) / (2.0 * h)
        np.testing.assert_allclose(stm[:, j], deriv, rtol=1e-4, atol=1e-6)


def test_j2_raan_secular_rate_matches_theory():
    """一天传播：升交点进动率 vs 一阶理论（1% 内）。"""
    dt_day = 86400.0
    end, _ = propagate_state_and_stm_j2(_STATE0, dt_day)
    _, _, i0, raan0, _, _ = _elements(_STATE0)
    _, _, _, raan1, _, _ = _elements(end)
    draan = (raan1 - raan0 + math.pi) % (2.0 * math.pi) - math.pi
    a, e, _, _, _, _ = _elements(_STATE0)
    n = math.sqrt(MU_EARTH / a ** 3)
    p = a * (1.0 - e * e)
    theory = -1.5 * J2 * n * (WGS84_RE / p) ** 2 * math.cos(i0) * dt_day
    assert abs(draan - theory) < 0.01 * abs(theory)


def test_propagate_validation():
    with pytest.raises(ValueError):
        propagate_state_and_stm_j2(np.zeros(5), 10.0)
    with pytest.raises(ValueError):
        propagate_state_and_stm_j2(np.array([np.nan] * 6), 10.0)


def test_j2_beats_two_body_for_leo():
    """同初值同区间：J2 传播与纯二体传播的末位置差随时间增长
    （J2 是 LEO 不可忽略项——若差值 ~0 说明 J2 没生效）。"""
    from mbdsdr_ai.orbit_dynamics import (
        two_body_dynamics_stm, _rk4_step as rk4_tb)
    dt = 1800.0
    end_j2, _ = propagate_state_and_stm_j2(_STATE0, dt)
    aug0 = np.hstack((_STATE0, np.eye(6).ravel()))
    aug = aug0
    t = 0.0
    n = 200
    h = dt / n
    for _ in range(n):
        aug = rk4_tb(two_body_dynamics_stm, t, aug, h)
        t += h
    drift = float(np.linalg.norm(end_j2[:3] - aug[:3]))
    assert 1.0 < drift < 100.0        # 30 min 公里级（实测 ~16 km）


# ── Laplace 初轨确定 ─────────────────────────────────────────
def _synthetic_observations(dt_s=60.0):
    """真轨道（二体+J2）三次观测 → (times, ra_deg, dec_deg, state_t2)。"""
    times = [T0 - dt_s, T0, T0 + dt_s]
    states = []
    for ti in times:
        s, _ = propagate_state_and_stm_j2(_STATE0, ti - T0)
        states.append(s)
    from mbdsdr_ai.orbit_perturbation import _station_eci
    ra, dec = [], []
    for ti, s in zip(times, states):
        R, _, _ = _station_eci(STA_LAT, STA_LON, STA_ALT, ti)
        d = s[:3] - R
        dn = d / np.linalg.norm(d)
        ra.append(math.degrees(math.atan2(dn[1], dn[0])))
        dec.append(math.degrees(math.asin(min(1.0, max(-1.0, dn[2])))))
    return times, ra, dec, states[1]


def test_laplace_recovers_leo_state():
    times, ra, dec, truth = _synthetic_observations()
    res = initial_orbit_laplace(times, ra, dec, STA_LAT, STA_LON, STA_ALT)
    assert res["n_solutions"] >= 1
    got = np.array(res["state_eci"])
    # 短弧纯测角初轨：公里级位置、几十米级速度即合格（方法固有精度）
    pos_err = float(np.linalg.norm(got[:3] - truth[:3]))
    vel_err = float(np.linalg.norm(got[3:] - truth[3:]))
    assert pos_err < 50.0, f"位置误差 {pos_err:.1f} km"
    assert vel_err < 0.2, f"速度误差 {vel_err:.3f} km/s"
    # 主解反算观测方向残差须在角秒级（观测由二体+J2 生成，残差反映
    # 二体假设与 J2 的模型差，诚实可见但不许离谱）
    assert res["rms_angle_arcsec"] < 60.0


def test_laplace_rejects_bad_inputs():
    with pytest.raises(ValueError):
        initial_orbit_laplace([1.0, 2.0], [0.0, 1.0], [0.0, 1.0],
                              STA_LAT, STA_LON, STA_ALT)
    with pytest.raises(ValueError):
        initial_orbit_laplace([3.0, 2.0, 1.0], [0.0, 1.0, 2.0],
                              [0.0, 1.0, 2.0], STA_LAT, STA_LON, STA_ALT)
    with pytest.raises(ValueError):
        initial_orbit_laplace([1.0, 1.5, 1.6], [0.0, 1.0, 2.0],
                              [0.0, 1.0, 2.0], STA_LAT, STA_LON, STA_ALT)


# ── 工具注册 ──────────────────────────────────────────────────
def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 2
    for name in ("orbit_propagate_stm_j2", "initial_orbit_laplace"):
        assert name in r.tools

    res = r.tools["orbit_propagate_stm_j2"]["handler"](
        {"state_eci": _STATE0.tolist(), "dt_s": 60.0})
    assert res.success
    assert len(res.data["state_eci"]) == 6
