# SPDX-License-Identifier: MIT
"""P1-8 干扰生成/评估闭环锚点测试。

合成口径：已知样式/参数的干扰 → 生成 → 施加到合成信号 →
评估降级量与注入强度单调一致（闭环自洽），等长/参数硬校验如实报错。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.interference_eval import (
    interference_assess, jammer_generate, register_tool_registry,
)

FS = 1e6
N = 8192
_RNG = np.random.default_rng(20261101)


def _signal(snr_db: float = 20.0) -> np.ndarray:
    """合成 QPSK 样信号（根升余弦外的简单 NRZ，测试评估口径足够）。"""
    sps = 8
    n_sym = N // sps
    syms = _RNG.choice([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j],
                       size=n_sym) / np.sqrt(2)
    base = np.repeat(syms, sps)[:N]
    base *= np.exp(2j * np.pi * 50e3 * np.arange(N) / FS)
    p_sig = np.mean(np.abs(base) ** 2)
    p_noise = p_sig / 10 ** (snr_db / 10)
    noise = np.sqrt(p_noise / 2) * (
        _RNG.standard_normal(N) + 1j * _RNG.standard_normal(N))
    return base + noise


# ── jammer_generate ──────────────────────────────────────────
def test_tone_frequency_and_power():
    res = jammer_generate("tone", N, FS, freq_hz=100e3, power_db=0.0,
                          seed=7)
    iq = np.array([complex(v) for v in res["iq"]])
    assert iq.shape == (N,)
    assert np.all(np.isfinite(iq))
    assert np.mean(np.abs(iq) ** 2) == pytest.approx(1.0, rel=0.05)
    peak_f = np.fft.fftshift(np.fft.fftfreq(N, 1.0 / FS))[
        int(np.argmax(np.abs(np.fft.fftshift(np.fft.fft(iq)))))]
    assert peak_f == pytest.approx(100e3, abs=FS / N * 2)


def test_noise_band_limited():
    res = jammer_generate("noise", N, FS, freq_hz=0.0, power_db=0.0,
                          bandwidth_hz=100e3, seed=11)
    iq = np.array([complex(v) for v in res["iq"]])
    assert np.mean(np.abs(iq) ** 2) == pytest.approx(1.0, rel=0.1)
    psd = np.abs(np.fft.fftshift(np.fft.fft(iq * np.hanning(N)))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(N, 1.0 / FS))
    inb = psd[np.abs(freqs) <= 50e3].sum()
    outb = psd[np.abs(freqs) > 120e3].sum()
    assert inb / (outb + 1e-30) > 100       # 能量集中在 100 kHz 带内


def test_chirp_sweeps_full_range():
    res = jammer_generate("chirp", N, FS, freq_hz=-200e3, power_db=0.0,
                          seed=3)
    iq = np.array([complex(v) for v in res["iq"]])
    # 短时窗峰值频率随时间单调移动（起点 -200 kHz 侧）
    win = 512
    peaks = []
    for s in range(0, N - win, win):
        seg = iq[s:s + win] * np.hanning(win)
        f = np.fft.fftshift(np.fft.fftfreq(win, 1.0 / FS))
        peaks.append(f[int(np.argmax(np.abs(np.fft.fftshift(
            np.fft.fft(seg)))))])
    assert peaks[0] < -150e3
    assert peaks[-1] > 150e3
    assert np.all(np.diff(peaks) > 0)       # 单调上扫


def test_pulse_duty_cycle():
    res = jammer_generate("pulse", N, FS, freq_hz=0.0, power_db=0.0,
                          duty_cycle=0.25, seed=5)
    iq = np.array([complex(v) for v in res["iq"]])
    active = np.abs(iq) > 1e-6
    frac = active.mean()
    assert 0.20 < frac < 0.30               # 导通占比 ≈ 0.25


def test_jammer_validation():
    with pytest.raises(ValueError):
        jammer_generate("laser", N, FS)
    with pytest.raises(ValueError):
        jammer_generate("noise", N, FS)      # 缺 bandwidth_hz
    with pytest.raises(ValueError):
        jammer_generate("tone", N, FS, freq_hz=FS)   # 超奈奎斯特
    with pytest.raises(ValueError):
        jammer_generate("tone", 4, FS)
    with pytest.raises(ValueError):
        jammer_generate("pulse", N, FS, duty_cycle=1.5)


def test_seed_reproducibility():
    a = jammer_generate("noise", 512, FS, bandwidth_hz=50e3, seed=42)
    b = jammer_generate("noise", 512, FS, bandwidth_hz=50e3, seed=42)
    assert a["iq"] == b["iq"]
    c = jammer_generate("noise", 512, FS, bandwidth_hz=50e3)
    assert c["iq"] != a["iq"] and c["reproducible"] is False


# ── interference_assess ──────────────────────────────────────
def test_assess_closed_loop_monotonic():
    """闭环：干扰功率 ↑ → SINAD 降级 ↑（同一信号对偶快照）。"""
    sig = _signal(snr_db=25.0)
    degr = []
    for p_db in (0.0, 10.0, 20.0):
        jam = jammer_generate("tone", N, FS, freq_hz=0.0, power_db=p_db,
                              seed=9)
        jammed = sig + np.array([complex(v) for v in jam["iq"]])
        res = interference_assess(sig.tolist(), jammed.tolist(), FS)
        assert res["sinad_degradation_db"] > 0.0
        degr.append(res["sinad_degradation_db"])
    assert degr[0] < degr[1] < degr[2]      # 单调恶化
    assert degr[2] > 10.0                   # 强干扰显著降级


def test_assess_noise_jammer_degrades():
    sig = _signal(snr_db=25.0)
    jam = jammer_generate("noise", N, FS, bandwidth_hz=200e3,
                          power_db=10.0, seed=13)
    jammed = sig + np.array([complex(v) for v in jam["iq"]])
    res = interference_assess(sig.tolist(), jammed.tolist(), FS)
    assert res["sinad_degradation_db"] > 5.0
    # 占用带宽被噪声顶宽
    assert res["occ_bw_change_hz"] > 0.0


def test_assess_identical_inputs_no_degradation():
    sig = _signal()
    res = interference_assess(sig.tolist(), sig.tolist(), FS)
    assert res["sinad_degradation_db"] == pytest.approx(0.0, abs=0.5)


def test_assess_validation():
    sig = _signal()
    with pytest.raises(ValueError):
        interference_assess(sig.tolist(), sig[:100].tolist(), FS)
    with pytest.raises(ValueError):
        interference_assess([0j] * 10, [0j] * 10, FS)
    bad = sig.tolist()
    bad[0] = complex(float("nan"), 0.0)
    with pytest.raises(ValueError):
        interference_assess(bad, sig.tolist(), FS)


# ── 工具注册 ─────────────────────────────────────────────────
def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 2
    for name in ("jammer_generate", "interference_assess"):
        assert name in r.tools

    res = r.tools["jammer_generate"]["handler"](
        {"style": "tone", "n_samples": 256, "sample_rate_hz": FS,
         "freq_hz": 10e3, "power_db": 0.0, "seed": 1})
    assert res.success and len(res.data["iq"]) == 256

    sig = _signal()
    res = r.tools["interference_assess"]["handler"](
        {"before": sig.tolist(), "after": sig.tolist(),
         "sample_rate_hz": FS})
    assert res.success
    assert res.data["estimate"] == "psd_percentile_fixed_band"
