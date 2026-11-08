# SPDX-License-Identifier: MIT
"""站址单源的契约（2026-11-09）。

守的是三条：**GUI 权威、镜像单向、绝不回退到城市坐标**。
"""
import json
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys  # noqa: E402
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import station_sync as ss  # noqa: E402


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_read_missing_files_returns_none(tmp_path):
    assert ss.read_gui_site(str(tmp_path / "nope.json")) is None
    assert ss.read_config_site(str(tmp_path / "nope2.json")) is None


def test_read_gui_and_config(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    cfg = str(tmp_path / "config.json")
    _write(gui, {"observer_lat": 45.0, "observer_lon": 120.0})
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    assert ss.read_gui_site(gui) == {"lat": 45.0, "lon": 120.0}
    assert ss.read_config_site(cfg) == {"lat": 31.2, "lon": 121.5}


def test_zero_zero_is_a_legal_site(tmp_path):
    """0,0 是几内亚湾，不是"未设置"哨兵。"""
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": 0.0, "observer_lon": 0.0})
    assert ss.read_gui_site(gui) == {"lat": 0.0, "lon": 0.0}


@pytest.mark.parametrize("lat,lon", [
    (91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0),
])
def test_out_of_range_is_rejected(tmp_path, lat, lon):
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": lat, "observer_lon": lon})
    assert ss.read_gui_site(gui) is None


def test_non_numeric_is_rejected(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": "北京", "observer_lon": "东经"})
    assert ss.read_gui_site(gui) is None


def test_corrupt_json_is_none_not_crash(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    with open(gui, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert ss.read_gui_site(gui) is None


def test_merge_prefers_gui_then_falls_back_to_config():
    gui = {"lat": 45.0, "lon": 120.0}
    cfg = {"lat": 31.2, "lon": 121.5}
    assert ss.merge_sites(gui, cfg) == gui
    assert ss.merge_sites(None, cfg) == cfg
    assert ss.merge_sites(None, None) is None


def test_write_keeps_unknown_keys(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_alt_m": 12.0, "agent_model": "x",
                 "nested": {"keep": True}})
    assert ss.write_config_site(45.0, 120.0, alt_m=30.0, path=cfg) is True
    with open(cfg, encoding="utf-8") as f:
        data = json.load(f)
    # 镜像字段更新了
    assert data["ground_station_lat"] == 45.0
    assert data["ground_station_lon"] == 120.0
    assert data["ground_station_alt_m"] == 30.0
    # 别人的键一个都不能丢
    assert data["agent_model"] == "x"
    assert data["nested"] == {"keep": True}
    # 不留临时文件
    assert not os.path.exists(cfg + ".tmp")


def test_write_backs_up_conflicting_old_value(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    assert ss.write_config_site(45.0, 120.0, path=cfg) is True
    bak = cfg + ".bak"
    assert os.path.exists(bak), "覆盖不同的旧站址前必须留证据"
    with open(bak, encoding="utf-8") as f:
        assert json.load(f)["ground_station_lat"] == 31.2


def test_write_same_value_does_not_backup(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 45.0, "ground_station_lon": 120.0})
    assert ss.write_config_site(45.0, 120.0, path=cfg) is True
    assert not os.path.exists(cfg + ".bak"), "值没变不该制造 .bak 噪音"


def test_write_creates_missing_file(tmp_path):
    cfg = str(tmp_path / "sub" / "config.json")
    assert ss.write_config_site(-33.87, 151.21, path=cfg) is True
    assert ss.read_config_site(cfg) == {"lat": -33.87, "lon": 151.21}


def test_write_then_read_roundtrip(tmp_path):
    cfg = str(tmp_path / "config.json")
    ss.write_config_site(0.0, 0.0, path=cfg)
    assert ss.read_config_site(cfg) == {"lat": 0.0, "lon": 0.0}


def test_clear_removes_only_station_keys(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 45.0, "ground_station_lon": 120.0,
                 "ground_station_alt_m": 3.0, "agent_model": "keep-me"})
    assert ss.clear_config_site(cfg) is True
    with open(cfg, encoding="utf-8") as f:
        data = json.load(f)
    assert "ground_station_lat" not in data
    assert "ground_station_lon" not in data
    assert "ground_station_alt_m" not in data
    assert data["agent_model"] == "keep-me"


def test_clear_on_missing_file_is_ok(tmp_path):
    assert ss.clear_config_site(str(tmp_path / "nope.json")) is True
