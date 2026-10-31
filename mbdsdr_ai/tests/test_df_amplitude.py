# SPDX-License-Identifier: MIT
"""P1-5 比幅测向锚点测试。

合成口径：真偏轴角 θ_true 经高斯斜射模型生成两通道功率（+噪声），
反演须收敛回 θ_true；模型单调区单解、表模型非单调区双解如实上报。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.df_amplitude import (
    _model_delta, df_amplitude_compare, register_tool_registry,
)

_RNG = np.random.default_rng(20261101)
SQUINT = 15.0
BEAMW = 30.0


def _powers(theta_deg: float, noise_db: float = 0.0):
    """按高斯斜射模型生成两通道功率（含独立检波噪声）。"""
    d = _model_delta(theta_deg, SQUINT, BEAMW, 0.0)
    na = _RNG.normal(0.0, noise_db)
    nb = _RNG.normal(0.0, noise_db)
    return d / 2.0 + na, -d / 2.0 + nb


def test_recovers_boresight_and_offaxis():
    for theta in (0.0, 5.0, -8.0, 12.5):
        pa, pb = _powers(theta)
        res = df_amplitude_compare(pa, pb, squint_deg=SQUINT,
                                   beamwidth_deg=BEAMW)
        assert res["off_boresight_deg"] is not None, res["reason"]
        assert res["off_boresight_deg"] == pytest.approx(theta, abs=0.05)
        assert res["n_solutions"] == 1
        assert res["residual_db"] < 1e-3
        assert res["model"] == "gaussian_squint"


def test_recovers_with_noise():
    errs = []
    for _ in range(30):
        theta = float(_RNG.uniform(-10.0, 10.0))
        pa, pb = _powers(theta, noise_db=0.3)
        res = df_amplitude_compare(pa, pb, squint_deg=SQUINT,
                                   beamwidth_deg=BEAMW)
        assert res["off_boresight_deg"] is not None
        errs.append(abs(res["off_boresight_deg"] - theta))
    # 误差传递：θ_err = ΔP_err/slope，slope=0.2007 dB/°，双通道 0.3 dB
    # 独立噪声 → ΔP 噪声 σ≈0.42 dB → θ σ≈2.1°
    assert np.mean(errs) < 2.5
    assert max(errs) < 7.0


def test_channel_imbalance_offset():
    # B 通道增益低 2 dB：不经标定反演偏 2/0.2007 ≈ 10°
    theta = 3.0
    pa, pb = _powers(theta)
    res = df_amplitude_compare(pa, pb - 2.0, squint_deg=SQUINT,
                               beamwidth_deg=BEAMW)
    assert res["off_boresight_deg"] == pytest.approx(theta + 2.0 / 0.200687,
                                                     abs=0.2)
    # 标定后回到真值（offset_db = A−B 不平衡量 = +2.0，模型侧补偿）
    res = df_amplitude_compare(pa, pb - 2.0, squint_deg=SQUINT,
                               beamwidth_deg=BEAMW, offset_db=+2.0)
    assert res["off_boresight_deg"] == pytest.approx(theta, abs=0.1)


def test_pattern_table_priority_and_no_extrapolation():
    # 非单调表模型：正弦形 ΔP → 一个测量值两个解
    angles = np.linspace(-90.0, 90.0, 37)
    table = {"angles_deg": angles.tolist(),
             "delta_db": (10.0 * np.sin(np.radians(2.0 * angles))).tolist()}
    res = df_amplitude_compare(5.0, 0.0, pattern_table=table)
    assert res["model"] == "pattern_table"
    assert res["n_solutions"] == 2      # sin 2θ 双解如实上报
    assert res["off_boresight_deg"] == pytest.approx(
        math.degrees(math.asin(0.5)) / 2.0, abs=0.2)
    # 表范围外测量 → 无解 + 诚实理由
    res = df_amplitude_compare(50.0, 0.0, pattern_table=table)
    assert res["off_boresight_deg"] is None and res["reason"]


def test_table_validation():
    with pytest.raises(ValueError):
        df_amplitude_compare(0.0, 0.0, pattern_table={
            "angles_deg": [0.0, -1.0], "delta_db": [0.0, 0.0]})
    with pytest.raises(ValueError):
        df_amplitude_compare(0.0, 0.0, pattern_table={
            "angles_deg": [0.0], "delta_db": [0.0, 1.0]})


def test_param_validation():
    with pytest.raises(ValueError):
        df_amplitude_compare(0.0, 0.0, beamwidth_deg=0.0)
    with pytest.raises(ValueError):
        df_amplitude_compare(0.0, 0.0, squint_deg=-1.0)


def test_tool_registration():
    from mbdsdr_ai.tool_registry import ToolRegistry

    r = ToolRegistry()
    n_before = len(r.tools)
    register_tool_registry(r)
    assert len(r.tools) == n_before + 1
    assert "df_amplitude_compare" in r.tools

    pa, pb = _powers(4.0)
    res = r.tools["df_amplitude_compare"]["handler"](
        {"power_a_db": pa, "power_b_db": pb,
         "squint_deg": SQUINT, "beamwidth_deg": BEAMW})
    assert res.success
    assert res.data["off_boresight_deg"] == pytest.approx(4.0, abs=0.1)
