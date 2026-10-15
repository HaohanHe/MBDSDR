# SPDX-License-Identifier: MIT
"""tdr_length_py 双曲峰值精修测试（NanoVNA-App TDR 精度链销账）。

用纯时延构造（物理上可积）：S11(f) = A*exp(-j*2*pi*f*tau)，tau=2L/(vf*c)，
其时域为 delta(tau)——峰位恰在 tau*span，是 TDR 峰位口径的天然基准。
"""
import numpy as np
import pytest

from mbdsdr_ai.fill_evidence_example import TDR_WINDOWS, tdr_length_py

C = 299792458.0


def _fixture(L: float, span: float = 100e6, n: int = 512, vf: float = 0.66):
    tau = 2 * L / (vf * C)
    f = np.linspace(0, span, n, endpoint=False)
    s11 = 0.5 * np.exp(-2j * np.pi * f * tau)
    return list(f), [[z.real, z.imag] for z in s11]


@pytest.mark.parametrize("L", [1.0, 2.0, 3.0])
def test_pure_delay_precision(L):
    """纯时延构造：峰位误差 <0.5%（100MHz 带宽物理可分辨）。"""
    f, s11 = _fixture(L)
    got = tdr_length_py(f, s11, vf=0.66)
    assert abs(got - L) / L < 0.005


def test_sub_bin_outperforms_no_refinement():
    """亚 bin 峰位：双曲精修显著优于 bin 量化（rect 窗无精修口径）。"""
    L = 1.37
    f, s11 = _fixture(L)
    kaiser = tdr_length_py(f, s11, vf=0.66, window="kaiser6")
    plain = tdr_length_py(f, s11, vf=0.66, window="rect")
    assert abs(kaiser - L) < abs(plain - L) * 0.5


def test_window_param_still_validated():
    f, s11 = _fixture(1.0)
    with pytest.raises(ValueError):
        tdr_length_py(f, s11, window="hann")


def test_degenerate_inputs_unchanged():
    assert tdr_length_py([], []) == 0.0
    assert tdr_length_py([1e6, 1e6, 1e6, 1e6], [[0.0, 0.0]] * 4) == 0.0


def test_windows_table_intact():
    assert set(TDR_WINDOWS) == {"rect", "kaiser6", "kaiser13"}
    assert TDR_WINDOWS["rect"] == 0.0
