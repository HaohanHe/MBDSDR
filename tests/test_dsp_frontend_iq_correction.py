"""I/Q 不平衡参数化校正测试。

对照 mbdsdr_ai/iq_correction.py（g 增益 + φ 相位校正矩阵）。
"""
import numpy as np
from mbdsdr_ai.iq_correction import (
    IQCorrector, inject_imbalance, estimate_imbalance,
)


def test_iq_correction_gain_phase():
    """注入 g=1.2, φ=10° 的不平衡，校正后 imbalance < 0.1dB / 0.5°。"""
    fs = 1_000_000.0
    n = 100_000
    t = np.arange(n) / fs
    # 理想各向同性信号：两个不相关的单音（I/Q 平面近似各向同性）
    ideal = (np.exp(1j * 2 * np.pi * 100_000 * t)
             + 0.5 * np.exp(1j * 2 * np.pi * 150_000 * t))

    bad = inject_imbalance(ideal, gain_ratio=1.2, phase_error_deg=10.0)

    before = estimate_imbalance(bad)
    assert abs(before.gain_ratio - 1.2) < 0.02
    assert abs(before.phase_error_deg - 10.0) < 0.5

    corr = IQCorrector()
    corr.set_imbalance(before.gain_ratio, before.phase_error_deg)
    out = corr.process(bad)

    after = estimate_imbalance(out)
    # 增益不平衡 < 0.1 dB
    gain_db = abs(20 * np.log10(after.gain_ratio))
    assert gain_db < 0.1, f"残余增益不平衡 {gain_db:.3f} dB"
    # 相位误差 < 0.5°
    assert abs(after.phase_error_deg) < 0.5, \
        f"残余相位误差 {after.phase_error_deg:.3f}°"


def test_iq_correction_default_identity():
    """未校准时为单位阵，不改动数据（不硬编码假校准）。"""
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(1000) + 1j * rng.standard_normal(1000)).astype(np.complex64)
    c = IQCorrector()
    y = c.process(x)
    assert np.allclose(x, y, atol=1e-6)


def test_iq_correction_fit_then_correct():
    """从数据 fit() 后能校正已知不平衡。"""
    fs = 1_000_000.0
    n = 100_000
    t = np.arange(n) / fs
    ideal = np.exp(1j * 2 * np.pi * 120_000 * t)
    bad = inject_imbalance(ideal, 1.1, 5.0)
    c = IQCorrector()
    est = c.fit(bad)
    assert abs(est.gain_ratio - 1.1) < 0.02
    out = c.process(bad)
    after = estimate_imbalance(out)
    assert abs(20 * np.log10(after.gain_ratio)) < 0.1
