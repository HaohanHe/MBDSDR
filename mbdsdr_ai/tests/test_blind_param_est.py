# SPDX-License-Identifier: MIT
"""P1-3 盲参数估计原子工具的锚点断言（合成向量自校准）。

校准注释（特征阈值的真实来源，_score_class 的阈值以此为准）：
  合成条件 fs=1 MHz、sps=20、α=0.35、Es/N0 20 dB 下的观测值——

  | 信号   | amp_std | phase_var | amp_kurt | mu4_re | line_bins  |
  |--------|---------|-----------|----------|--------|------------|
  | CW     | 0.07    | 0.25      | 2.97     | ~0     | ~4(载波线) |
  | FM     | 0.07    | 5.3       | 2.98     | ~0     | 数千(连续) |
  | 2FSK   | 0.07    | 13.9      | 3.03     | ~0     | ~8(双谱线) |
  | BPSK   | 0.47    | 88        | 2.05     | ≈+1    | 数千(连续) |
  | QPSK   | 0.33    | 34.6      | 3.51     | ≈−1    | 数千(连续) |
  | 8PSK   | 0.31    | 26.5      | 2.99     | ≈0     | 数千(连续) |
  | 16QAM  | 0.41    | 31.6      | 2.36     | ≈−0.68 | 数千(连续) |

重要工程教训（本轮实测踩出）：符号成形必须用**冲激串 ⊛ 成形滤波器**；
对 np.repeat（NRZ hold）后的序列成形会额外乘 sinc(f/sr) 权重，
谱形整体变陡 ~30%，α 估计系统性偏低——测试发生器错则 golden 全错。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.blind_param_est import (
    alpha_estimate, modulation_classify, register_tool_registry,
    symbol_rate_estimate,
)

FS = 1e6
SR = 50e3
SPS = int(FS / SR)
N_SYMS = 4000
N = N_SYMS * SPS

_RNG = np.random.default_rng(20261031)


def _rc_taps(alpha: float, sps: int, span: int = 8) -> np.ndarray:
    n = span * sps
    t = np.arange(n) / sps - span / 2
    h = np.sinc(t) * np.cos(math.pi * alpha * t) / (1 - (2 * alpha * t) ** 2)
    t0 = 1.0 / (2 * alpha)  # 奇异点极限 (π/4)·sinc(t0)（l'Hôpital）
    h = np.where(np.abs(np.abs(t) - t0) < 1e-9, (math.pi / 4) * np.sinc(t0), h)
    return h / np.sqrt(np.sum(h ** 2))


def _shape(syms: np.ndarray, alpha: float) -> np.ndarray:
    """冲激串 ⊛ 成形滤波器（不可对 NRZ hold 序列成形，sinc 污染谱形）。"""
    u = np.zeros(len(syms) * SPS, dtype=complex)
    u[::SPS] = syms
    return np.convolve(u, _rc_taps(alpha, SPS), mode="same")


def _noise(x: np.ndarray, esn0_db: float) -> np.ndarray:
    e = np.mean(np.abs(x) ** 2)
    nvar = e / 10 ** (esn0_db / 10)
    return x + math.sqrt(nvar / 2) * (_RNG.standard_normal(len(x)) +
                                      1j * _RNG.standard_normal(len(x)))


@pytest.fixture(scope="module")
def signals() -> dict:
    t = np.arange(N) / FS
    sig = {}
    sig["bpsk"] = _noise(_shape(_RNG.choice([-1, 1], N_SYMS), 0.35), 20)
    q = (_RNG.choice([-1, 1], N_SYMS) + 1j * _RNG.choice([-1, 1], N_SYMS))
    sig["qpsk"] = _noise(_shape(q / math.sqrt(2), 0.35), 20)
    k = _RNG.integers(0, 8, N_SYMS)
    sig["8psk"] = _noise(_shape(np.exp(1j * (2 * math.pi * k / 8 + math.pi / 8)), 0.35), 20)
    i = _RNG.choice([-3, -1, 1, 3], N_SYMS)
    q16 = _RNG.choice([-3, -1, 1, 3], N_SYMS)
    sig["16qam"] = _noise(_shape((i + 1j * q16) / math.sqrt(10), 0.35), 20)
    bits = _RNG.choice([0, 1], N_SYMS)
    ph = np.cumsum(2 * math.pi * ((bits.repeat(SPS) * 2 - 1) * SR / 2) / FS)
    sig["2fsk"] = _noise(np.exp(1j * ph), 20)
    mf = np.sin(2 * math.pi * 1e3 * t) + 0.5 * np.sin(2 * math.pi * 3e3 * t)
    ph = np.cumsum(2 * math.pi * 75e3 * mf / FS)
    sig["fm"] = _noise(np.exp(1j * ph), 20)
    sig["am"] = _noise((1 + 0.6 * np.sin(2 * math.pi * 3e3 * t)) *
                       np.exp(1j * 2 * math.pi * 1e4 * t), 20)
    sig["cw"] = _noise(np.exp(1j * 2 * math.pi * 1e5 * t), 20)
    sig["noise_only"] = _noise(np.zeros(N, dtype=complex), 0)
    return sig


# ─────────────────────────────────────────────────────────────
# 调制识别
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["cw", "fm", "2fsk", "bpsk", "qpsk", "8psk", "16qam"])
def test_classify_top1_correct(signals, name):
    r = modulation_classify(signals[name].tolist(), FS)
    assert r["candidates"][0]["modulation"] == name, (
        f"{name} 被识别为 {r['candidates'][0]['modulation']}，"
        f"features={r['features']}")


def test_classify_features_present(signals):
    r = modulation_classify(signals["qpsk"].tolist(), FS)
    for key in ("amp_std", "amp_kurt", "phase_var", "line_bins",
                "occ_bw_hz", "mu4_re", "mu4_im"):
        assert key in r["features"]


def test_classify_too_few_samples_rejected(signals):
    with pytest.raises(ValueError):
        modulation_classify(signals["qpsk"][:256].tolist(), FS)


def test_classify_bad_sample_rate_rejected(signals):
    with pytest.raises(ValueError):
        modulation_classify(signals["qpsk"].tolist(), -1e6)


# ─────────────────────────────────────────────────────────────
# 盲符号率
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["bpsk", "qpsk", "16qam"])
def test_symbol_rate_exact(signals, name):
    r = symbol_rate_estimate(signals[name].tolist(), FS,
                             search_range=(30e3, 70e3))
    assert r["symbol_rate_hz"] is not None
    assert abs(r["symbol_rate_hz"] - SR) / SR < 0.01
    assert r["line_snr_db"] > 15.0


def test_symbol_rate_pure_noise_honest_none(signals):
    r = symbol_rate_estimate(signals["noise_only"].tolist(), FS,
                             search_range=(30e3, 70e3))
    assert r["symbol_rate_hz"] is None
    assert r["reason"]  # 必须给诚实理由


def test_symbol_rate_below_threshold_rejected(signals):
    """门限抬高到不可能达标的值：即使有线谱也必须按门限拒绝。"""
    r = symbol_rate_estimate(signals["bpsk"].tolist(), FS,
                             search_range=(30e3, 70e3),
                             snr_threshold_db=80.0)
    assert r["symbol_rate_hz"] is None


# ─────────────────────────────────────────────────────────────
# 滚降系数
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("alpha_true", [0.2, 0.35, 0.5])
def test_alpha_estimate_grid(alpha_true):
    q = (_RNG.choice([-1, 1], N_SYMS) + 1j * _RNG.choice([-1, 1], N_SYMS))
    x = _noise(_shape(q / math.sqrt(2), alpha_true), 20)
    r = alpha_estimate(x.tolist(), FS, SR)
    assert abs(r["alpha"] - alpha_true) <= 0.06, (
        f"α 真值 {alpha_true}，估计 {r['alpha']}（resid={r['residual_rms_db']}）")
    assert not r["ambiguous"]


def test_alpha_non_rc_spectrum_flagged(signals):
    """2FSK 谱形不符升余弦模型：必须标 ambiguous，不给确定 α。"""
    r = alpha_estimate(signals["2fsk"].tolist(), FS, SR)
    assert r["ambiguous"]


def test_alpha_bad_rates_rejected(signals):
    with pytest.raises(ValueError):
        alpha_estimate(signals["qpsk"].tolist(), FS, FS)  # sr 超 Nyquist


# ─────────────────────────────────────────────────────────────
# 工具注册面
# ─────────────────────────────────────────────────────────────
def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 3
    for name in ("modulation_classify", "symbol_rate_estimate", "alpha_estimate"):
        assert name in r.tools
        params = r.tools[name]["definition"]["function"]["parameters"]
        assert "iq" in params["properties"]

    # 走一遍 handler：符号率工具端到端
    sig = _noise(_shape(_RNG.choice([-1, 1], N_SYMS), 0.35), 20)
    iq = [{"re": v.real, "im": v.imag} for v in sig[:N_SYMS * SPS]]
    res = r.tools["symbol_rate_estimate"]["handler"](
        {"iq": iq, "sample_rate_hz": FS, "search_range": [30e3, 70e3]})
    assert res.success
    assert abs(res.data["symbol_rate_hz"] - SR) / SR < 0.01

    # 纯噪声走 handler：success=False 但 data 带诚实理由
    nz = _noise(np.zeros(2048, dtype=complex), 0)
    res = r.tools["symbol_rate_estimate"]["handler"](
        {"iq": [{"re": v.real, "im": v.imag} for v in nz],
         "sample_rate_hz": FS, "search_range": [30e3, 70e3]})
    assert not res.success
    assert res.data["symbol_rate_hz"] is None
