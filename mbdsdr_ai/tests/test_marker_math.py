# SPDX-License-Identifier: MIT
"""marker_math 测试。"""
import numpy as np
import pytest

from mbdsdr_ai.marker_math import (
    delta_reading,
    marker_value,
    peak_search,
    snap_to_nearest,
)

F = np.linspace(1e6, 101e6, 201)


def test_snap_exact_hit():
    idx, fr = snap_to_nearest(F, 51e6)
    assert fr == 51e6 and F[idx] == 51e6


def test_snap_between_picks_closer():
    target = F[10] + 0.4 * (F[11] - F[10])
    idx, _ = snap_to_nearest(F, target)
    assert idx == 10
    target = F[10] + 0.6 * (F[11] - F[10])
    idx, _ = snap_to_nearest(F, target)
    assert idx == 11


def test_snap_edges():
    assert snap_to_nearest(F, -1e9)[0] == 0
    assert snap_to_nearest(F, 1e12)[0] == len(F) - 1


def test_snap_honest_inputs():
    with pytest.raises(ValueError):
        snap_to_nearest([], 1.0)
    with pytest.raises(ValueError):
        snap_to_nearest(F, np.nan)
    with pytest.raises(ValueError):
        snap_to_nearest([3e6, 1e6, 2e6], 2e6)      # 非降序


def test_marker_value_and_bounds():
    tr = np.ones(len(F), dtype=complex)
    f, v = marker_value(F, tr, 42)
    assert f == F[42] and v == 1.0
    with pytest.raises(IndexError):
        marker_value(F, tr, 999)
    with pytest.raises(IndexError):
        marker_value(F, tr[:-1], 0)                 # 长度不齐


def test_delta_reading_db():
    tr = np.ones(len(F), dtype=complex)
    tr[100] = 10.0                                  # +20dB
    out = delta_reading(F, tr, 0, 100)
    assert out["df_hz"] == F[100] - F[0]
    assert out["dv"] == pytest.approx(9.0)
    assert out["db"] == pytest.approx(20.0)


def test_delta_reading_zero_ref_honest():
    tr = np.zeros(len(F), dtype=complex)
    out = delta_reading(F, tr, 0, 5)
    assert out["db"] is None                        # va=0 无定义，不伪造


def test_peak_search_orders_by_magnitude():
    tr = np.ones(101)
    tr[20] = 5.0
    tr[80] = 3.0
    assert peak_search(F[:101], tr, 2) == [20, 80]
    assert peak_search(F[:101], tr, 1) == [20]


def test_peak_search_flat_region_no_false_peak():
    tr = np.ones(50)
    tr[10:20] = 2.0                                 # 平顶
    assert peak_search(np.linspace(1, 50, 50), tr) == []


def test_peak_search_honest_inputs():
    with pytest.raises(ValueError):
        peak_search(F, np.ones(len(F)), 0)
    with pytest.raises(ValueError):
        peak_search(F[:2], np.ones(2))
