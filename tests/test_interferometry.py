# -*- coding: utf-8 -*-
"""射电干涉测量：互相关测向 + 角分辨率/可见度 的门禁。

材料《通信对抗材料包》射电干涉讲义的三件事，本文件逐条钉住：
  · 几何时延 τ = D·sinθ/c 与相关测向（双站互相关 → 滞后 → 方位角）；
  · 角分辨率 θ_res ≈ λ/D——用讲义自己的对照表数字做基准：
      人眼 500nm/2mm → ≈1 arcmin；VLA 单碟 5GHz/25m → ≈8 arcmin；
      CygA 5GHz/25km → 0.5 arcsec；
  · 可见度 I·cos(2πf·D·sinθ/c)——θ=0 时应为 ±I（cos(0)=1），
    且几何时延随 sinθ 单调。

诚实语义：相关峰不显著时**不给方位角**（只回原因）。
红线：合成向量，非真实 OTA 样本。
"""
import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.interferometry import (  # noqa: E402
    C_M_S,
    angular_resolution,
    tdoa_bearing,
    visibility,
)

SR = 1_000_000.0        # 采样率 1 MHz
BASELINE_M = 20_000.0   # 基线 20 km


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def _two_stations(theta_deg: float, n: int = 40000, seed: int = 5):
    """造两站同源信号：站 B 收到延迟 τ = D·sinθ/c 的同一信号。"""
    rng = np.random.default_rng(seed)
    sig = (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    theta = math.radians(theta_deg)
    delay_s = BASELINE_M * math.sin(theta) / C_M_S
    lag = int(round(delay_s * SR))
    assert lag >= 2, f"时延太小（{lag} 样点），换更大的基线或采样率"
    b = np.concatenate([np.zeros(lag, dtype=complex), sig[:-lag]])
    return sig, b, delay_s, lag


def test_tdoa_recovers_incidence_angle():
    """双站互相关 → 滞后 → sinθ → 与真实几何一致。"""
    theta_true = 20.0
    sig, b, delay_s, lag = _two_stations(theta_true)
    res = tdoa_bearing(sig, b, SR, BASELINE_M, baseline_bearing_deg=90.0)
    assert res["azimuth_deg"] is not None, res
    assert res["peak"] > 0.5, res
    # 滞后误差应在半样点内 → 时延误差 → sinθ 误差很小
    # 构造时延取整到样点，故真值以"构造出的滞后"为准；
    # 与几何真值的差应落在半样点的量化界内
    quant = 0.5 * C_M_S / (BASELINE_M * SR)
    assert abs(res["lag_samples"] - lag) <= 0.5, (res["lag_samples"], lag)
    sin_constructed = lag * C_M_S / (BASELINE_M * SR)
    assert abs(res["sin_theta"] - sin_constructed) < 1e-4
    assert abs(res["sin_theta"] - math.sin(math.radians(theta_true))) <= quant + 1e-6
    # 绝对方位 = 基线方位 + 相对角（左侧约定）
    assert 0.0 <= res["azimuth_deg"] < 360.0
    assert res["side"] in ("left", "right")


def test_tdoa_absolute_bearing_uses_baseline_bearing():
    """基线方位参与合成：换基线方位，绝对方位随之平移。"""
    sig, b, _, _ = _two_stations(15.0)
    r1 = tdoa_bearing(sig, b, SR, BASELINE_M, baseline_bearing_deg=0.0)
    r2 = tdoa_bearing(sig, b, SR, BASELINE_M, baseline_bearing_deg=90.0)
    assert r1["azimuth_deg"] is not None and r2["azimuth_deg"] is not None
    assert abs(((r2["azimuth_deg"] - r1["azimuth_deg"]) % 360.0) - 90.0) < 1e-6


def test_tdoa_unrelated_signals_give_no_bearing():
    """两路不同源 → 相关峰不显著 → **不给方位角**（不编造）。"""
    rng = np.random.default_rng(99)
    a = rng.standard_normal(20000) + 1j * rng.standard_normal(20000)
    b = rng.standard_normal(20000) + 1j * rng.standard_normal(20000)
    res = tdoa_bearing(a, b, SR, BASELINE_M, min_peak=0.2)
    assert res["peak"] < 0.2, res
    assert res["azimuth_deg"] is None
    assert res["reason"], res


def test_tdoa_tool_end_to_end(tmp_path):
    """工具层：两站 IQ 文件 → 方位角。"""
    reg = MBDSDRAgent().tool_registry
    theta_true = 30.0
    sig, b, _, _ = _two_stations(theta_true)
    pa = str(tmp_path / "a.iq")
    pb = str(tmp_path / "b.iq")
    sig.astype(np.complex64).tofile(pa)
    b.astype(np.complex64).tofile(pb)
    r = reg.call("interferometry_tdoa", {
        "sig_a_path": pa, "sig_b_path": pb,
        "sample_rate_hz": SR, "baseline_m": BASELINE_M,
        "baseline_bearing_deg": 45.0,
    })
    assert r.success, (r.content or "")[:300]
    assert r.data["azimuth_deg"] is not None, r.data
    assert r.data["peak"] > 0.5
    quant = 0.5 * C_M_S / (BASELINE_M * SR)
    assert abs(r.data["sin_theta"] - math.sin(math.radians(theta_true))) <= quant + 1e-6


def test_angular_resolution_matches_handout_table():
    """角分辨率对照：讲义给的三个数（人眼 ~1 arcmin、CygA 0.5 arcsec、VLA ~8 arcmin）。"""
    eye = angular_resolution(500e-9, 2e-3)
    assert 0.8 <= eye["theta_arcmin"] <= 1.0, eye        # ≈1 arcmin
    cyg = angular_resolution(C_M_S / 5e9, 25e3)
    assert 0.45 <= cyg["theta_arcsec"] <= 0.55, cyg     # 0.5 arcsec
    vla = angular_resolution(C_M_S / 5e9, 25.0)
    assert 7.5 <= vla["theta_arcmin"] <= 9.0, vla       # ≈8 arcmin


def test_visibility_formula():
    """可见度 I·cos(2πf·D·sinθ/c)：θ=0 → I；几何时延随 sinθ 单调。"""
    v0 = visibility(2.0, 5e9, 1000.0, 0.0)
    assert v0["visibility"] == pytest.approx(2.0, abs=1e-9)
    assert v0["geometric_delay_s"] == pytest.approx(0.0, abs=1e-12)
    v1 = visibility(2.0, 5e9, 1000.0, math.radians(30.0))
    assert v1["geometric_delay_s"] > 0
    assert abs(v1["visibility"]) <= 2.0 + 1e-9
    # 几何时延 = D·sinθ/c
    assert v1["geometric_delay_s"] == pytest.approx(
        1000.0 * math.sin(math.radians(30.0)) / C_M_S, rel=1e-9)


def test_resolution_tool():
    """工具层：频点 + 基线 → 分辨率（波长由光速/频率换算）。"""
    reg = MBDSDRAgent().tool_registry
    r = reg.call("interferometry_resolution", {
        "freq_hz": 5e9, "baseline_m": 25e3, "theta_deg": 10.0})
    assert r.success, (r.content or "")[:200]
    res = r.data["resolution"]
    assert 0.45 <= res["theta_arcsec"] <= 0.55, res
    assert r.data["visibility"]["geometric_delay_s"] > 0
