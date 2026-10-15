# SPDX-License-Identifier: MIT
"""sat_calibration 物理定标链测试（公开公式口径）。"""
import numpy as np
import pytest

from mbdsdr_ai.sat_calibration import (
    ChannelCal,
    CalibrationTable,
    counts_to_radiance,
    counts_to_reflectance,
    radiance_to_reflectance,
    reflectance_to_brightness,
)

# 用一条可手算的 cal：C = 20 + 3*count -> count=255 时 L=785
_CAL = ChannelCal(offset=20.0, scale=3.0)


def test_counts_to_radiance_linear():
    assert counts_to_radiance(0, _CAL) == 20.0
    assert counts_to_radiance(255, _CAL) == 785.0


def test_reflectance_formula_known_value():
    """rho = pi*L*d^2/(esun*cos(z))，手算钉死。"""
    rho = radiance_to_reflectance(785.0, esun=1400.0,
                                  solar_zenith_deg=60.0,
                                  earth_sun_distance_au=1.0)
    expect = np.pi * 785.0 / (1400.0 * np.cos(np.radians(60.0)))
    assert abs(float(rho) - float(expect)) < 1e-9


def test_night_time_is_zero_not_nan():
    """太阳天顶角 >=90 -> 反射率 0（诚实边界，非 NaN/负值）。"""
    r = radiance_to_reflectance(500.0, esun=1400.0, solar_zenith_deg=95.0)
    assert float(r) == 0.0


def test_distance_square_law():
    """d=2AU -> 反射率 x4（d^2 平方律）。"""
    a = radiance_to_reflectance(100.0, esun=1400.0, solar_zenith_deg=30.0,
                                earth_sun_distance_au=1.0)
    b = radiance_to_reflectance(100.0, esun=1400.0, solar_zenith_deg=30.0,
                                earth_sun_distance_au=2.0)
    assert abs(float(b) - 4 * float(a)) < 1e-9


def test_bad_esun_rejected():
    with pytest.raises(ValueError):
        radiance_to_reflectance(100.0, esun=0.0, solar_zenith_deg=30.0)


def test_counts_to_reflectance_chain(tmp_path):
    rho = counts_to_reflectance(255, _CAL, esun=1400.0, solar_zenith_deg=60.0)
    assert abs(float(rho) - np.pi * 785.0 / (1400.0 * 0.5)) < 1e-9


def test_reflectance_to_brightness_clips():
    r = reflectance_to_brightness(np.array([-50.0, 25.0, 100.0, 250.0]))
    assert list(r) == [0, 64, 255, 255]      # 25/100*255=63.75->64


def test_calibration_table_missing_channel_honest():
    tab = CalibrationTable(channels={1: _CAL}, esun={1: 1400.0})
    with pytest.raises(KeyError):
        tab.get(2)
    with pytest.raises(KeyError):
        tab.calibrate(2, [10], solar_zenith_deg=30.0)


def test_calibration_table_default_esun_overridable():
    tab = CalibrationTable(channels={1: _CAL}, esun={1: 999.0})
    _, rho = tab.calibrate(255, solar_zenith_deg=60.0) if False else tab.calibrate(
        1, 255, solar_zenith_deg=60.0)
    assert abs(float(rho) - np.pi * 785.0 / (999.0 * 0.5)) < 1e-9
