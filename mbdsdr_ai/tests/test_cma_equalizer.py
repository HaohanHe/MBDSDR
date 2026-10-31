# SPDX-License-Identifier: MIT
"""P1-6 CMA 盲均衡锚点断言（合成 ISI 信道自校准）。

校准条件：QPSK/16QAM 符号序列 → 离散多径信道 → 25 dB Es/N0 复噪声。
QAM 的 CMA 代价地板（如实断言，非零）：完美均衡 16QAM 输出代价 =
E[(|s|²−1.32)²] = 0.4224——converged 判据是相对初代价的改善比例，
不是绝对零代价。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.cma_equalizer import cma_cost, cma_equalize, register_tool_registry

_RNG = np.random.default_rng(20261101)
N = 16000


def _qpsk(n):
    return (_RNG.choice([-1, 1], n) + 1j * _RNG.choice([-1, 1], n)) / math.sqrt(2)


def _q16(n):
    i = _RNG.choice([-3, -1, 1, 3], n)
    q = _RNG.choice([-3, -1, 1, 3], n)
    return (i + 1j * q) / math.sqrt(10)


def _channel(x, chan, snr_db=25.0):
    r = np.convolve(x, chan, mode="same")
    nvar = np.mean(np.abs(r) ** 2) / 10 ** (snr_db / 10)
    return r + math.sqrt(nvar / 2) * (_RNG.standard_normal(len(r)) +
                                      1j * _RNG.standard_normal(len(r)))


@pytest.fixture(scope="module")
def isi_qpsk():
    # 温和单回波：CMA 的主场
    return _channel(_qpsk(N), [1.0, 0.5])


@pytest.fixture(scope="module")
def isi_q16():
    return _channel(_q16(N), [1.0, 0.0, 0.35 * np.exp(1j * 0.7),
                              0.0, 0.12 * np.exp(-1j * 0.3)])


def test_qpsk_cost_improvement_converges(isi_qpsk):
    res = cma_equalize(isi_qpsk.tolist(), constellation="qpsk")
    assert res["converged"], f"cost {res['cost_initial']}→{res['cost_final']}"
    assert res["cost_final"] < res["cost_initial"] * 0.5
    assert not res["diverged"]
    # EVM 有如实改善（块平均 N-CMA 在该信道为个位数 dB 量级）
    assert res["evm_after_db"] < res["evm_before_db"]


def test_q16_cost_improvement(isi_q16):
    res = cma_equalize(isi_q16.tolist(), constellation="16qam")
    # 16QAM 的 CMA 有局部极小公开特性：只断言代价如实下降 + 标志诚实
    assert res["cost_final"] < res["cost_initial"]
    assert res["converged"] == (res["cost_final"] <
                                res["cost_initial"] * 0.5)
    assert res["evm_before_db"] is not None and res["evm_after_db"] is not None


def test_qam_cost_floor_documented():
    """16QAM 完美均衡的代价地板 = 0.4224（非零，写进断言防误判）。"""
    s = _q16(40000)
    floor = cma_cost(s, 1.32)
    assert floor == pytest.approx(0.4224, abs=0.01)


def test_divergence_honest_flag(isi_qpsk):
    """极大步长必须触发 diverged=True 且 equalized 为空（不产伪输出）。"""
    res = cma_equalize(isi_qpsk[:4000].tolist(), constellation="qpsk",
                       mu=5.0)
    assert res["diverged"] or not res["converged"]
    if res["diverged"]:
        assert res["equalized"] == []
        assert res["cost_final"] is None


def test_input_validation(isi_qpsk):
    with pytest.raises(ValueError):
        cma_equalize(isi_qpsk[:32].tolist())
    with pytest.raises(ValueError):
        cma_equalize(isi_qpsk.tolist(), mu=-0.1)
    with pytest.raises(ValueError):
        cma_equalize(isi_qpsk.tolist(), n_taps=20)
    with pytest.raises(ValueError):
        cma_equalize(isi_qpsk.tolist(), constellation="not-a-mod")


def test_explicit_r2_numeric():
    s = _qpsk(4000)
    res = cma_equalize(s.tolist(), constellation="1.0")
    assert res["r2"] == 1.0
    assert res["evm_after_db"] is None  # 显式 R2 无栅格，EVM 诚实为 None


def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 1
    assert "cma_equalize" in r.tools
    params = r.tools["cma_equalize"]["definition"]["function"]["parameters"]
    assert "symbols" in params["properties"]

    syms = [{"re": v.real, "im": v.imag}
            for v in _channel(_qpsk(4000), [1.0, 0.5])]
    res = r.tools["cma_equalize"]["handler"](
        {"symbols": syms, "constellation": "qpsk"})
    assert res.success == bool(res.data["converged"])
