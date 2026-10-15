# SPDX-License-Identifier: MIT
"""tdr_modes 测试：LP-Impulse/LP-Step/阻抗剖面。"""
import numpy as np
import pytest

from mbdsdr_ai.tdr_modes import (
    _hermitian_extend,
    full_profile,
    impedance_profile,
    tdr_impulse_response,
    tdr_step_response,
)

C = 299792458.0
VF = 0.66
Z0 = 50.0
SPAN = 100e6
N = 512
F = np.linspace(0, SPAN, N, endpoint=False)


def _line_step_s11(l_m: float, z1: float):
    """阻抗阶跃线模型：前 l_m 为 Z0，之后 Z1。Γ(f)=ρ·exp(−j2βl)。"""
    rho = (z1 - Z0) / (z1 + Z0)
    beta = 2 * np.pi * F / (VF * C)
    return rho * np.exp(-2j * beta * l_m)


def test_hermitian_extend_doubles_with_projection():
    x = np.random.default_rng(2).standard_normal(64) + \
        1j * np.random.default_rng(3).standard_normal(64)
    ext = _hermitian_extend(x)
    assert len(ext) == 128
    # 2N conj extend（非精确厄米，DC 不对称残差由 API 取实部投影消除——
    # 见 tdr_modes 模块 docstring）；钉两件事：长度翻倍 + 未加窗时
    # 阶跃渐近值 == 实测 DC（矩形窗物理精确性）。
    t, rho = tdr_step_response(np.full(64, 0.25 + 0j),
                               np.linspace(0, 1e6, 64, endpoint=False))
    assert rho[-1] == pytest.approx(0.25, abs=1e-9)


def test_impulse_response_is_real():
    s11 = _line_step_s11(2.0, 75.0)
    t, imp = tdr_impulse_response(s11, F)
    assert np.all(np.isreal(imp))
    assert len(t) == 2 * N
    # 时间轴步长 = 1/(2·span)；span 按实际频率网格（endpoint=False 时
    # span = SPAN·(N−1)/N），不硬编码 SPAN。
    dt_expect = 1.0 / (2.0 * (F[-1] - F[0]))
    assert t[1] - t[0] == pytest.approx(dt_expect)


def test_step_response_recovers_impedance():
    """矩形窗 LP-Step：剖面远端渐近 Z1——物理口径钉死。矩形窗吉布斯
    振铃可短暂触 ±1（unbounded 如实标注），只钉渐近值不钉中间过程。"""
    z1 = 75.0
    l = 2.0
    s11 = _line_step_s11(l, z1)
    prof = full_profile(s11, F, Z0)
    tail = prof.z_ohm[-1]
    assert tail == pytest.approx(z1, rel=0.05)


def test_step_finds_discontinuity_time():
    """阶跃跳变沿应在双程时延 2l/v 附近（越过参考面脉冲区后首个
    进入目标带内的采样）。"""
    z1, l = 100.0, 1.5
    s11 = _line_step_s11(l, z1)
    t, rho = tdr_step_response(s11, F)
    rho_target = (z1 - Z0) / (z1 + Z0)
    dt = t[1] - t[0]
    # 跳过参考面脉冲区（前 8 个采样），找首个 |ρ−ρt|<0.05 的采样
    k = next(i for i in range(8, len(rho))
             if abs(rho[i] - rho_target) < 0.05)
    tau = 2 * l / (VF * C)
    # 矩形窗上升时间 ~1/span = 10ns，容忍 6 个采样（30ns）定位带宽
    assert abs(t[k] - tau) < 6 * dt


def test_open_short_unbounded_honest():
    """ρ=+1（开路）Z=inf 标无界；ρ=−1（短路）Z=0 有界——两极限分开钉。"""
    res = impedance_profile(np.array([0.5, 1.0, -1.0]), Z0)
    assert res.z_ohm[0] == pytest.approx(150.0)      # 50·1.5/0.5
    assert np.isinf(res.z_ohm[1]) and res.unbounded[1]
    assert res.z_ohm[2] == pytest.approx(0.0) and not res.unbounded[2]


def test_impedance_bad_z0_honest():
    with pytest.raises(ValueError):
        impedance_profile(np.zeros(4), 0.0)


def test_impulse_degenerate_inputs():
    with pytest.raises(ValueError):
        tdr_impulse_response(np.ones(2), [1, 2])
    with pytest.raises(ValueError):
        tdr_impulse_response(np.ones(8), np.linspace(1e6, 1e6, 8))
    with pytest.raises(ValueError):
        tdr_impulse_response(np.ones(8), np.linspace(1e6, 2e6, 9))
