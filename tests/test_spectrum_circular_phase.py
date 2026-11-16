# SPDX-License-Identifier: MIT
"""#109 spectrum_processor.extract_modulation_features 相位圆统计确定性符合测试。

相位是圆周量（np.angle 已 wrap 到 [-π,π]）。跨 ±π 回绕信号（如扫过 ±180° 的
复正弦/PSK）线性 mean/std 错：+135°/-135° 两样本线性 mean=0°/std=135°，
真值圆 mean=180°/圆 std=45°。

修复：phase_mean = atan2(mean(sin), mean(cos))；
      phase_std  = sqrt(mean(wrap(phase - mean)²))。
与 amr.py(#107) / signal_quality.py 同口径。

纪律：
- 固定输入，不绑墙钟/网络（测试确定性）。
- 退化（均匀分布、零合矢量）输入圆均值数学上无定义，只断言有限/域，
  绝不钉具体角度，也不做跨实现一致性比较（实现返回任意浮点残差方向）。
- 非回绕 identity 用关于 0 对称的组态：圆均值==线性均值==0（精确），
  圆 std==线性 std（精确），容差 1e-12 是真实数学相等而非放松。
"""
import math

import numpy as np
import pytest

from mbdsdr_ai.spectrum_processor import SpectrumProcessor
from mbdsdr_ai.signal_quality import signal_quality
from mbdsdr_ai import amr

SR = 1e6


def _samples(deg_pattern, n=256):
    """把相位图案（度）平铺到 n 个等幅复样本（method 要求 n>=64）。"""
    reps = (n + len(deg_pattern) - 1) // len(deg_pattern)
    degs = np.tile(np.asarray(deg_pattern, dtype=float), reps)[:n]
    return np.exp(1j * np.radians(degs))


def _wrap_pi(x):
    return (x + math.pi) % (2 * math.pi) - math.pi


def _ref_circ_mean(ph):
    """独立 numpy 参考：合矢量方向。"""
    return math.atan2(float(np.mean(np.sin(ph))), float(np.mean(np.cos(ph))))


def _ref_circ_std(ph):
    """独立 numpy 参考：对圆均值偏差 wrap 到 [-π,π) 后 RMS。"""
    m = _ref_circ_mean(ph)
    devs = np.array([_wrap_pi(float(p) - m) for p in ph])
    return float(np.sqrt(np.mean(devs ** 2)))


def _circ_dist(a, b):
    return abs(_wrap_pi(a - b))


@pytest.fixture
def spec():
    return SpectrumProcessor()


# 跨 ±π 回绕组态（均非退化：合矢量非零）
WRAP_CONFIGS = [
    [135.0, -135.0],
    [170.0, 175.0, 180.0, -175.0, -170.0],
    [150.0, -150.0, 180.0],
    [179.0, -179.0],
    [-170.0, 170.0, 180.0, -180.0],
]

# 非回绕、关于 0 对称组态：圆均值==线性均值==0（精确）
NONWRAP_SYMMETRIC = [
    [10.0, -10.0, 5.0, -5.0],
    [30.0, -30.0, 10.0, -10.0],
    [1.0, -1.0],
    [45.0, -45.0, 20.0, -20.0],
]

# 跨实现一致性组态（非退化）
CONSISTENCY_CONFIGS = [
    [135.0, -135.0],
    [170.0, 175.0, 180.0, -175.0, -170.0],
    [150.0, -150.0, 180.0],
    [10.0, -10.0, 5.0, -5.0],
    [179.0, -179.0],
]


# ── 对独立圆参考逐值相等 ──────────────────────────────────────────────
@pytest.mark.parametrize("degs", WRAP_CONFIGS)
def test_phase_mean_matches_circular_reference(spec, degs):
    s = _samples(degs)
    f = spec.extract_modulation_features(s, SR)
    ph = np.angle(s)
    assert _circ_dist(f["phase_mean"], _ref_circ_mean(ph)) < 1e-9


@pytest.mark.parametrize("degs", WRAP_CONFIGS)
def test_phase_std_matches_circular_reference(spec, degs):
    s = _samples(degs)
    f = spec.extract_modulation_features(s, SR)
    ph = np.angle(s)
    assert abs(f["phase_std"] - _ref_circ_std(ph)) < 1e-9


# ──  headline 缺陷：+135°/-135° 真值 180°/45°，绝非旧线性 0°/135° ──────
def test_wrap_mean_is_180_not_zero(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0]), SR)
    assert _circ_dist(f["phase_mean"], math.pi) < 1e-9      # 180°
    assert _circ_dist(f["phase_mean"], 0.0) > 3.0           # 绝不是旧缺陷 0°


def test_wrap_std_is_45_not_135(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0]), SR)
    assert abs(f["phase_std"] - math.radians(45.0)) < 1e-9   # 45°
    assert abs(f["phase_std"] - math.radians(135.0)) > 1.0   # 绝不是旧缺陷 135°


def test_near_dateline_sweep_mean(spec):
    f = spec.extract_modulation_features(
        _samples([170.0, 175.0, 180.0, -175.0, -170.0]), SR)
    # 真值近 180°，旧线性给 36.5°
    assert _circ_dist(f["phase_mean"], math.pi) < math.radians(1.0)
    assert _circ_dist(f["phase_mean"], math.radians(36.5)) > math.radians(90.0)


# ── 非回绕 identity（对称组态，精确相等）──────────────────────────────
@pytest.mark.parametrize("degs", NONWRAP_SYMMETRIC)
def test_non_wrap_equals_linear(spec, degs):
    s = _samples(degs)
    f = spec.extract_modulation_features(s, SR)
    ph = np.angle(s)
    assert abs(f["phase_mean"] - float(np.mean(ph))) < 1e-12
    assert abs(f["phase_std"] - float(np.std(ph))) < 1e-12


# ── 跨实现一致性：signal_quality（mean_phase_deg）─────────────────────
@pytest.mark.parametrize("degs", CONSISTENCY_CONFIGS)
def test_consistent_with_signal_quality(spec, degs):
    s = _samples(degs)
    f = spec.extract_modulation_features(s, SR)
    sq = signal_quality(s)
    # signal_quality.mean_phase_deg 圆均值（度，round 2）；spectrum 弧度全精度
    assert abs(math.degrees(f["phase_mean"]) - sq["mean_phase_deg"]) < 0.01


# ── 跨实现一致性：amr(#107) mean_phase/std_phase（弧度全精度）─────────
@pytest.mark.parametrize("degs", CONSISTENCY_CONFIGS)
def test_consistent_with_amr(spec, degs):
    s = _samples(degs)
    f = spec.extract_modulation_features(s, SR)
    feat = amr.AMRClassifier().extract_features_from_iq(list(s), SR)
    # circ_dist 处理 ±π 表示歧义（纯 Python vs numpy 浮点残差符号）
    assert _circ_dist(f["phase_mean"], feat.mean_phase) < 1e-9
    assert abs(f["phase_std"] - feat.std_phase) < 1e-9


# ── 域：phase_mean ∈ [-π,π]，phase_std ∈ [0,π] ────────────────────────
@pytest.mark.parametrize("degs", WRAP_CONFIGS + NONWRAP_SYMMETRIC
                         + [[0.0, 90.0, 180.0, -90.0]])
def test_phase_mean_domain(spec, degs):
    f = spec.extract_modulation_features(_samples(degs), SR)
    assert -math.pi - 1e-12 <= f["phase_mean"] <= math.pi + 1e-12


@pytest.mark.parametrize("degs", WRAP_CONFIGS + NONWRAP_SYMMETRIC
                         + [[0.0, 90.0, 180.0, -90.0]])
def test_phase_std_domain(spec, degs):
    f = spec.extract_modulation_features(_samples(degs), SR)
    assert 0.0 <= f["phase_std"] <= math.pi + 1e-12


# ── 退化：均匀分布零合矢量 → 圆均值无定义，只断言有限/域 ──────────────
def test_degenerate_uniform_spread_is_finite(spec):
    f = spec.extract_modulation_features(_samples([0.0, 90.0, 180.0, -90.0]), SR)
    assert math.isfinite(f["phase_mean"])
    assert math.isfinite(f["phase_std"])
    assert -math.pi - 1e-9 <= f["phase_mean"] <= math.pi + 1e-9
    assert 0.0 <= f["phase_std"] <= math.pi + 1e-9
    # 不钉具体角度：零合矢量圆均值数学上无定义，实现返回任意浮点残差方向


# ── 入参边界 ──────────────────────────────────────────────────────────
def test_too_few_samples_returns_error(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0], n=32), SR)
    assert f == {"error": "样本太少"}


def test_exactly_64_samples_ok(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0], n=64), SR)
    assert "error" not in f
    assert "phase_mean" in f and "phase_std" in f


# ── 回归守卫：其余特征不受相位修复影响 ────────────────────────────────
def test_other_features_present(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0]), SR)
    for k in ("num_samples", "amplitude_mean", "amplitude_std",
              "amplitude_kurtosis", "amplitude_max", "frequency_mean",
              "frequency_std", "frequency_max_abs", "zero_crossing_rate",
              "spectral_centroid", "spectral_flatness", "modulation_guess"):
        assert k in f, f"缺特征 {k}"


def test_constant_amplitude_features(spec):
    # 等幅复样本：amplitude_mean≈1，amplitude_std≈0（相位修复不动幅度维）
    f = spec.extract_modulation_features(_samples([135.0, -135.0]), SR)
    assert abs(f["amplitude_mean"] - 1.0) < 1e-9
    assert f["amplitude_std"] < 1e-9


def test_frequency_features_unaffected_by_phase_fix(spec):
    # 纯复正弦 f0=1kHz：inst_freq 已先 np.unwrap，frequency_mean≈f0。
    # 守卫相位修复未误伤频率维（unwrap 路径不变）。
    n = 256
    f0 = 1000.0
    t = np.arange(n) / SR
    s = np.exp(1j * 2 * np.pi * f0 * t)
    f = spec.extract_modulation_features(s, SR)
    assert abs(f["frequency_mean"] - f0) < 1.0


def test_modulation_guess_is_string(spec):
    f = spec.extract_modulation_features(_samples([135.0, -135.0]), SR)
    assert isinstance(f["modulation_guess"], str)