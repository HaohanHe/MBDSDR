# SPDX-License-Identifier: MIT
"""P11-5 / D4 内置 TLE 来源/可信度登记：生产表不得含合成凑整值。

运行：python -m pytest mbdsdr_ai/tests/test_builtin_tle_meta.py -v
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

from mbdsdr_ai.builtin_tle_meta import (  # noqa: E402
    analyze_tle_provenance,
    builtin_provenance_table,
)
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from tle_samples import NOAA15_TLE  # noqa: E402


class TestProductionTableHasNoSynthetic:
    def test_decoders_builtin_table_is_empty(self):
        """D4：生产路径不得内置伪造/凑整 TLE。"""
        assert BUILTIN_TLE == {}
        assert builtin_provenance_table(include_real=False) == {}

    def test_fixture_entry_is_registered_as_synthetic(self):
        line1, line2 = NOAA15_TLE
        prov = analyze_tle_provenance(
            "NOAA 15", line1, line2,
            source="tests/fixtures/tle_samples.py（测试样本）")
        assert prov["source"].startswith("tests/fixtures")
        assert prov["epoch_utc"]
        assert prov["epoch_rounded"] is True
        assert prov["trust"] == "synthetic_rounded"
        assert prov["usable_for_tracking"] is False
        assert "凑整" in prov["note"] or "历元" in prov["note"]


class TestRealBuiltin:
    def test_sat_tracker_snapshot_is_external_snapshot(self):
        table = builtin_provenance_table(include_real=True)
        real = [p for p in table.values()
                if "sat_tracker" in p["source"]]
        assert real, "应能读取 sat_tracker 静态快照"
        for prov in real:
            assert prov["origin_kind"] == "external_snapshot"
            assert prov["provided_by"]
            assert prov["epoch_utc"]
            assert prov["epoch_rounded"] is False
            assert prov["trust"] == "verified"
            assert prov["usable_for_tracking"] is True

    def test_rounded_flag_detects_nonzero_fraction(self):
        line1, line2 = NOAA15_TLE
        # 把历元小数改为非 0（模拟真实抓取值），其余不动
        fake = line1[:18] + "26268.86268050" + line1[32:]
        prov = analyze_tle_provenance("NOAA 15", fake, line2)
        assert prov["epoch_rounded"] is False


class TestToolsSurfaceTrust:
    """工具输出对「可信度/无 TLE」不得静默。"""

    def test_helper_spares_verified_snapshot(self):
        from mbdsdr_ai.sdr_tools import _tle_provenance_lines
        # sat_tracker 快照里的 NOAA 19 是 verified，不应产生告警。
        assert _tle_provenance_lines(["NOAA 19"]) == []

    def test_predict_all_reports_no_tle(self, monkeypatch):
        from mbdsdr_ai import new_spacetime as ns
        from mbdsdr_ai import sdr_tools

        monkeypatch.setattr(ns, "predict_all_passes", lambda *a, **k: [])
        monkeypatch.setattr(sdr_tools, "any_tle_available", lambda: False)
        txt = sdr_tools._satellite_predict_all(
            {"observer_lat": 45.0, "observer_lon": 120.0,
             "hours_ahead": 24.0})
        assert "无 TLE，无法预报" in txt

    def test_sky_view_visible_reports_no_tle(self, monkeypatch):
        from mbdsdr_ai import new_spacetime as ns
        from mbdsdr_ai.sdr_tools import _sky_view_visible

        fake = {"total_visible": 0, "satellites": [], "no_tle": True,
                "note": "无 TLE，无法预报（测试）"}
        monkeypatch.setattr(ns, "compute_visible_satellite_count",
                            lambda *a, **k: fake)
        txt = _sky_view_visible({"observer_lat": 45.0,
                                 "observer_lon": 120.0})
        assert "无 TLE，无法预报" in txt


class TestBadChecksum:
    def test_checksum_mismatch_downgrades(self):
        line1, line2 = NOAA15_TLE
        # 先造一个历元不凑整的行，再破坏校验和 → 应判 invalid_checksum
        line1 = line1[:18] + "26268.86268050" + line1[32:]
        bad = line1[:-1] + ("0" if line1[-1] != "0" else "1")
        prov = analyze_tle_provenance("NOAA 15", bad, line2)
        assert prov["checksum_ok"] is False
        assert prov["trust"] == "invalid_checksum"
        assert prov["usable_for_tracking"] is False
