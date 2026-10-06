# SPDX-License-Identifier: MIT
"""Phase57：频率 PPM / 电平 dBFS 校准（注入已知参考，确定性断言）。"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration as C  # noqa: E402


class TestFreqCal:
    def test_ppm_formula(self):
        # 测得比参考高 100Hz，参考 437.5MHz → +0.2286 ppm
        r = C.ppm_correction(437500100.0, 437500000.0)
        assert abs(r["ppm"] - 100.0 / 437500000.0 * 1e6) < 1e-6
        assert r["correction_hz"] == -100.0

    def test_peak_detect_injected(self):
        fs = 480000.0
        t = np.arange(48000) / fs
        iq = np.exp(1j * 2 * np.pi * 750 * t).astype(np.complex64)
        peak = C.measure_peak_hz(iq, fs)
        assert abs(peak - 750.0) < 1.0

    def test_bad_ref_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            C.ppm_correction(100.0, 0.0)


class TestLevelCal:
    def test_dbfs_half_amplitude(self):
        iq = (np.full(1000, 0.5, np.complex64))
        dbfs = C.measure_dbfs(iq)
        assert abs(dbfs - (-6.0206)) < 0.01  # 0.5 幅值 = -6.02 dBFS

    def test_level_offset_zero(self):
        iq = (np.full(1000, 0.5, np.complex64))
        r = C.level_offset_db(C.measure_dbfs(iq), -6.0206)
        assert abs(r["level_offset_db"]) < 0.01

    def test_empty_iq_honest(self):
        assert np.isnan(C.measure_dbfs(np.zeros(0, np.complex64)))
