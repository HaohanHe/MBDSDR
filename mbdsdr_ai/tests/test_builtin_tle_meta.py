# SPDX-License-Identifier: MIT
"""P11-5 内置 TLE 来源/可信度登记：凑整值不得当精确数据。

运行：python -m pytest mbdsdr_ai/tests/test_builtin_tle_meta.py -v
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.builtin_tle_meta import (  # noqa: E402
    analyze_tle_provenance,
    builtin_provenance_table,
)
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402


class TestRoundedBuiltin:
    def test_decoders_entries_are_marked_synthetic(self):
        table = builtin_provenance_table(include_real=False)
        assert table
        for name, prov in table.items():
            assert prov["epoch_rounded"] is True, name
            assert prov["trust"] == "synthetic_rounded", name
            assert prov["usable_for_tracking"] is False
            assert "凑整" in prov["note"] or "历元" in prov["note"]

    def test_single_entry_has_source_and_label(self):
        line1, line2 = BUILTIN_TLE["NOAA 15"][:2]
        prov = analyze_tle_provenance("NOAA 15", line1, line2,
                                      source="decoders.BUILTIN_TLE")
        assert prov["source"] == "decoders.BUILTIN_TLE"
        assert prov["epoch_utc"]
        assert prov["trust_label"]
        assert prov["usable_for_tracking"] is False


class TestRealBuiltin:
    def test_sat_tracker_real_entries_verified(self):
        table = builtin_provenance_table(include_real=True)
        real = [p for p in table.values()
                if "sat_tracker" in p["source"]]
        assert real, "应能读取 sat_tracker 真实内置 TLE"
        for prov in real:
            assert prov["epoch_rounded"] is False
            assert prov["trust"] == "verified"
            assert prov["usable_for_tracking"] is True

    def test_rounded_flag_detects_nonzero_fraction(self):
        line1, line2 = BUILTIN_TLE["NOAA 15"][:2]
        # 把历元小数改为非 0（模拟真实抓取值），其余不动
        fake = line1[:18] + "26268.86268050" + line1[32:]
        prov = analyze_tle_provenance("NOAA 15", fake, line2)
        assert prov["epoch_rounded"] is False


class TestToolsSurfaceTrust:
    """工具输出必须把凑整 TLE 的可信度透出，不许当精确值静默使用。"""

    def test_helper_flags_rounded_and_spares_verified(self):
        from mbdsdr_ai.sdr_tools import _tle_provenance_lines
        notes = "\n".join(_tle_provenance_lines(["METEOR M2", "NOAA 19"]))
        assert "METEOR M2" in notes
        assert "可信度" in notes
        assert "NOAA 19" not in notes

    def test_predict_all_reports_rounded_even_without_passes(self, monkeypatch):
        from mbdsdr_ai import new_spacetime as ns
        from mbdsdr_ai.sdr_tools import _satellite_predict_all

        monkeypatch.setattr(ns, "predict_all_passes", lambda *a, **k: [])
        txt = _satellite_predict_all({"observer_lat": 45.0,
                                      "observer_lon": 120.0,
                                      "hours_ahead": 24.0})
        assert "METEOR M2" in txt
        assert "可信度" in txt

    def test_sky_view_visible_flags_rounded_satellite(self, monkeypatch):
        from mbdsdr_ai import new_spacetime as ns
        from mbdsdr_ai.sdr_tools import _sky_view_visible

        fake = {"total_visible": 1, "satellites": [{
            "name": "METEOR M2", "elevation_deg": 30.0, "azimuth_deg": 90.0,
            "distance_km": 800.0, "doppler_hz": 0.0}]}
        monkeypatch.setattr(ns, "compute_visible_satellite_count",
                            lambda *a, **k: fake)
        txt = _sky_view_visible({"observer_lat": 45.0,
                                 "observer_lon": 120.0})
        assert "METEOR M2" in txt
        assert "可信度" in txt


class TestBadChecksum:
    def test_checksum_mismatch_downgrades(self):
        line1, line2 = BUILTIN_TLE["NOAA 15"][:2]
        # 先造一个历元不凑整的行，再破坏校验和 → 应判 invalid_checksum
        line1 = line1[:18] + "26268.86268050" + line1[32:]
        bad = line1[:-1] + ("0" if line1[-1] != "0" else "1")
        prov = analyze_tle_provenance("NOAA 15", bad, line2)
        assert prov["checksum_ok"] is False
        assert prov["trust"] == "invalid_checksum"
        assert prov["usable_for_tracking"] is False
