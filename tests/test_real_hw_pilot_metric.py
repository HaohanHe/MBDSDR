# SPDX-License-Identifier: MIT
"""真机验收 R4 的 19 kHz 导频测量：只在导频窗口找峰，不被邻带强峰带偏。"""
import importlib.util
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "real_hw_acceptance.py")
_spec = importlib.util.spec_from_file_location("real_hw_acceptance", _SCRIPT)
hw = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("real_hw_acceptance", hw)
_spec.loader.exec_module(hw)

FS = 2_400_000.0
N = int(FS * 0.8)


def _mpx(pilot_amp=0.08, spur_amp=0.0, noise=0.02, seed=7):
    rng = np.random.default_rng(seed)
    t = np.arange(N) / FS
    sig = 0.3 * np.sin(2 * np.pi * 1000 * t)
    sig += pilot_amp * np.sin(2 * np.pi * 19000 * t)
    if spur_amp:
        sig += spur_amp * np.sin(2 * np.pi * 22700 * t)
    return sig + noise * rng.standard_normal(N)


def test_pilot_found_and_reported_near_19k():
    pm = hw.pilot_metrics(_mpx(), FS)
    assert abs(pm["pilot_hz"] - 19000) <= 200
    assert pm["pilot_above_neigh_db"] > 6.0
    assert pm["pilot_ok"] is True


def test_neighbour_spur_does_not_steal_pilot_detection():
    """22.7 kHz 强峰不能把「导频」报成 22.7 kHz（旧全带 argmax 的缺陷）。"""
    pm = hw.pilot_metrics(_mpx(spur_amp=0.5), FS)
    assert abs(pm["pilot_hz"] - 19000) <= 200, pm
    assert pm["pilot_ok"] is True


def test_noise_without_pilot_is_rejected():
    pm = hw.pilot_metrics(_mpx(pilot_amp=0.0, noise=0.2), FS)
    assert pm["pilot_ok"] is False


def test_axis_peak_check_ignores_stronger_neighbour_station():
    """频轴判据只盯期望位置：目标 -397.9 kHz，邻台 +505 kHz 更强也不能带偏。

    旧口径（±600 kHz 质心）在这组谱上会落到 -350 kHz 以内，正是 2026-11-22
    真机 R3 误判 BAD（实测 -268 kHz）的成因。
    """
    n = 65536
    fs = 2_400_000.0
    bin_hz = fs / n
    c = n // 2
    pdb = np.full(n, -60.0)

    def put(off_hz, db, half_bins=6):
        i = c + int(round(off_hz / bin_hz))
        pdb[i - half_bins:i + half_bins + 1] = db

    put(-397_900.0, 34.0)
    put(+505_000.0, 35.0)

    off = hw._peak_off_hz(pdb, c, -400e3, bin_hz, half_hz=40e3)
    assert abs(off + 400_000) <= 2_500, off

    span = int(600e3 / bin_hz)
    cen = hw._centroid_off_hz(pdb, c, span, bin_hz)
    assert cen > -350_000, f"回归对照：旧质心应被邻台带偏，实际 {cen}"


def test_rank_peaks_dedups_close_peaks_and_keeps_prominence_order():
    """同一台的多条谱线只留一条；候选按峰底比降序，供 R4 逐个验导频。"""
    peaks = [
        (106_290_000.0, 25.0),
        (106_300_000.0, 24.0),   # 与 106.29 太近，应被合并
        (88_100_000.0, 20.0),
        (91_600_000.0, 19.0),
        (95_300_000.0, 18.5),
    ]
    picked = hw.rank_peaks(peaks, limit=3)
    assert [round(f / 1e6, 2) for f, _ in picked] == [106.29, 88.1, 91.6]
    assert hw.rank_peaks(peaks, limit=1) == [(106_290_000.0, 25.0)]
