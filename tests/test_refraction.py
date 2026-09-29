# SPDX-License-Identifier: MIT
"""确定性单测：大气折射 —— 天顶≈0、地平线≈34'、已知高度校正值。

参考标准测试值（P=1010 hPa, T=10°C）。
"""
import math
import pytest

from mbdsdr_ai.astronomy import refraction


def test_zenith_refraction_near_zero():
    # true alt=89° → 折射应非常小（< 0.05'）
    r = refraction.saemundsson_arcmin(89.0, pressure_hpa=1010.0, temperature_c=10.0)
    assert r < 0.05, f"zenith refraction {r}' too large"


def test_horizon_refraction_about_34_arcmin():
    # 反向 Bennett：观测高度 0° → 真高度下沉 ~34.5'
    r = refraction.bennett_arcmin(0.0, pressure_hpa=1010.0, temperature_c=10.0)
    assert 33.5 < r < 35.5, f"horizon refraction {r}' out of range"


def test_known_altitude_correction_values():
    # true alt=5° → 9.674'
    r5 = refraction.saemundsson_arcmin(5.0, pressure_hpa=1010.0, temperature_c=10.0)
    assert abs(r5 - 9.674) < 0.2, f"alt=5° expected 9.674', got {r5:.3f}'"
    # true alt=45° → 1.013'
    r45 = refraction.saemundsson_arcmin(45.0, pressure_hpa=1010.0, temperature_c=10.0)
    assert abs(r45 - 1.013) < 0.05, f"alt=45° expected 1.013', got {r45:.3f}'"


def test_roundtrip():
    for alt in [10.0, 30.0, 60.0]:
        obs = refraction.true_to_apparent(alt)
        back = refraction.apparent_to_true(obs)
        assert abs(back - alt) < 0.01, f"roundtrip failed at {alt}"


def test_disabled_refraction_is_identity():
    m = refraction.RefractionModel(enabled=False)
    assert abs(m.true_to_apparent(10.0) - 10.0) < 1e-9
    assert abs(m.apparent_to_true(10.0) - 10.0) < 1e-9
