# SPDX-License-Identifier: MIT
"""P11 站址可信度：来源优先级、0,0/海上/缺省拒绝、配置不冒充城市。

运行：python -m pytest mbdsdr_ai/tests/test_station_trust.py -v
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.station import (  # noqa: E402
    StationFix,
    coarse_land_status,
    evaluate_station,
    resolve_station,
    station_from_config,
    station_from_gnss,
    station_from_user,
)


class TestSourcePriority:
    def test_gnss_beats_user_and_config(self):
        fix = resolve_station(
            gnss=station_from_gnss(45.0, 120.0, accuracy_m=5),
            user=station_from_user(39.9, 116.4),
            config={"lat": 43.9, "lon": 125.3, "source": "config"},
        )
        assert fix.source == "gnss"
        assert (fix.lat, fix.lon) == (45.0, 120.0)
        assert fix.accuracy_m == 5.0

    def test_user_beats_config_when_no_gnss(self):
        fix = resolve_station(user=station_from_user(39.9, 116.4),
                              config={"lat": 43.9, "lon": 125.3,
                                      "source": "config"})
        assert fix.source == "user"
        assert fix.lon == 116.4

    def test_config_used_when_no_gnss_or_user(self):
        fix = resolve_station(config={"lat": 45.0, "lon": 120.0,
                                      "source": "config"})
        assert fix.source == "config"
        assert fix.lat == 45.0

    def test_no_input_never_invents_a_city(self):
        fix = resolve_station()
        assert fix.source == "none"
        assert fix.lat is None and fix.lon is None


class TestEvaluation:
    def test_valid_land_station_is_trusted(self):
        ev = evaluate_station(station_from_user(39.9, 116.4))
        assert ev["trusted"] is True
        assert ev["land"]["status"] == "land"
        assert ev["reasons"] == []

    def test_null_island_rejected(self):
        ev = evaluate_station(station_from_user(0.0, 0.0))
        assert ev["trusted"] is False
        assert any("null_island" in r for r in ev["reasons"])

    def test_open_ocean_rejected(self):
        ev = evaluate_station(station_from_user(0.0, -150.0))
        assert ev["trusted"] is False
        assert any("at_sea" in r for r in ev["reasons"])
        assert coarse_land_status(0.0, -150.0)["status"] == "sea"

    def test_missing_coordinates_rejected(self):
        ev = evaluate_station(StationFix(None, None, source="user"))
        assert ev["trusted"] is False
        assert any("missing_coordinates" in r for r in ev["reasons"])

    def test_default_and_none_sources_rejected(self):
        for src in ("default", "none"):
            ev = evaluate_station(StationFix(45.0, 120.0, source=src))
            assert ev["trusted"] is False
            assert any("source_missing" in r or "default_coordinates" in r
                       for r in ev["reasons"])

    def test_coarse_or_invalid_accuracy_rejected(self):
        ev = evaluate_station(station_from_user(45.0, 120.0, accuracy_m=100000))
        assert ev["trusted"] is False
        assert any("accuracy_too_coarse" in r for r in ev["reasons"])

    def test_manual_without_accuracy_is_trusted_with_warning(self):
        ev = evaluate_station(station_from_user(45.0, 120.0))
        assert ev["trusted"] is True
        assert any("accuracy_unprovided" in w for w in ev["warnings"])

    def test_out_of_range_rejected(self):
        ev = evaluate_station(station_from_user(95.0, 120.0))
        assert ev["trusted"] is False
        assert any("out_of_range" in r for r in ev["reasons"])

    def test_coastal_sea_boxes(self):
        assert coarse_land_status(15.0, 88.0)["status"] == "sea"    # 孟加拉湾
        assert coarse_land_status(35.0, 18.0)["status"] == "sea"    # 地中海
        assert coarse_land_status(35.7, 139.7)["status"] == "land"  # 东京
        assert coarse_land_status(1.35, 103.8)["status"] == "land"  # 新加坡


class TestConfigSource:
    def test_config_file_carries_source_and_accuracy(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({
            "ground_station_lat": 45.0, "ground_station_lon": 120.0,
            "ground_station_alt_m": 100.0, "ground_station_accuracy_m": 30,
        }), encoding="utf-8")
        fix = station_from_config(str(p))
        assert fix.source == "config"
        assert fix.alt_m == 100.0
        assert fix.accuracy_m == 30

    def test_missing_config_is_none_not_a_default_city(self, tmp_path):
        fix = station_from_config(str(tmp_path / "missing.json"))
        assert fix.source == "none"
        assert fix.lat is None and fix.lon is None

    def test_config_without_coordinates_is_none(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"sample_rate": 2.4}), encoding="utf-8")
        fix = station_from_config(str(p))
        assert fix.source == "none"
        assert fix.lat is None


class TestControllerGuard:
    def test_default_station_refuses_pass_and_tune(self, tmp_path):
        from mbdsdr_ai.core.controller import SDRController
        ctrl = SDRController(backend=None, config_dir=str(tmp_path))
        st = ctrl.station_status()
        assert st["trusted"] is False
        out = ctrl.get_satellite_pass(25544)
        assert out["error"] == "station_untrusted"
        assert any("null_island" in r or "at_sea" in r for r in out["reasons"])
        tune = ctrl.tune_satellite(25544)
        assert tune["error"] == "station_untrusted"

    def test_user_station_is_trusted(self, tmp_path):
        from mbdsdr_ai.core.controller import SDRController
        ctrl = SDRController(backend=None, config_dir=str(tmp_path))
        ctrl.set_ground_station(45.0, 120.0)
        assert ctrl.station_status()["trusted"] is True
        ctrl.set_ground_station(0.0, 0.0)
        assert ctrl.station_status()["trusted"] is False
