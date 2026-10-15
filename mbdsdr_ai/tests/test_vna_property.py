# SPDX-License-Identifier: MIT
"""新数学模块确定性随机健壮性（固定种子，可复现）。

性质断言（不是快照对比）：
- lc_match：凡返回解集，每个解接回负载后输入阻抗必须≈R0（与具体
  随机值无关的普遍性质）；拒绝路径（R<=0.5/Q>100）显式抛错不返回垃圾。
- Biquad.lowpass/highpass/bandpass/notch：随机 fc/Q 下极点必须全部
  在单位圆内（IIR 稳定性普遍性质；peak 型允许 |H|>1 不振荡发散）。
- coax_loss_s11：随机复迹线不崩溃；检出 bin 时 cleaned 与原始同长度。
- SweepAverager：随机帧推入超过深度 3 倍后缓冲不增长、len 封顶。
"""
import numpy as np
import pytest

from mbdsdr_ai.tests.test_vna_match import _input_impedance
from mbdsdr_ai.vna_avg import SweepAverager
from mbdsdr_ai.vna_dsp import Biquad, coax_loss_s11
from mbdsdr_ai.vna_match import LcSolution, lc_match

RNG = np.random.default_rng(20261015)


def test_lc_match_random_loads_verify():
    for _ in range(300):
        rl = float(RNG.uniform(0.6, 1000.0))
        xl = float(RNG.uniform(-500.0, 500.0))
        r0 = float(RNG.uniform(20.0, 75.0))
        if abs(xl) / rl > 100.0:
            with pytest.raises(ValueError):
                lc_match(rl + 1j * xl, r0)
            continue
        sols = lc_match(rl + 1j * xl, r0)
        for s in sols:
            zin = _input_impedance(s, rl + 1j * xl)
            assert abs(zin.real - r0) < 1e-6 * r0
            assert abs(zin.imag) < 1e-6 * r0


def test_biquad_random_stability():
    for _ in range(300):
        fs = float(RNG.uniform(1e3, 10e6))
        fc = float(RNG.uniform(1.0, fs * 0.45))
        q = float(RNG.uniform(0.2, 20.0))
        for fac in (Biquad.lowpass, Biquad.highpass,
                    Biquad.bandpass, Biquad.notch):
            bq = fac(fc, fs, q)
            # 极点 = a1/a2 二次方程的根
            r1 = np.roots([1.0, bq.a1, bq.a2])
            assert np.all(np.abs(r1) < 1.0 - 1e-9), \
                f"{fac.__name__} 极点出单位圆 fc={fc} fs={fs} q={q}"


def test_coax_loss_random_no_crash():
    for _ in range(200):
        n = int(RNG.integers(8, 2048))
        trend = RNG.standard_normal() * 1e-3 * np.arange(n)
        x = (0.5 - 0.01j) + trend * (1 + 0.2j) \
            + 0.1 * (RNG.standard_normal(n) + 1j * RNG.standard_normal(n))
        clean, det = coax_loss_s11(x)
        assert len(clean) == n
        if det is not None:
            assert 1 <= det < n // 2


def test_averager_capacity_bounded():
    avg = SweepAverager(32, max_depth=8)
    for k in range(40):
        avg.push(np.full(32, k + 0j))
    assert len(avg) == 8
    # 最旧帧已被覆盖：history(7) 是第 33 帧（40-8+... 首帧=第33）
    assert avg.history(0)[0] == complex(39)
    assert avg.history(7)[0] == complex(32)
