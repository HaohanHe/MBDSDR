# SPDX-License-Identifier: MIT
"""ADS-B 面板地面站配置读取：未配置与「坏了」必须可分。

红线（docs/pending-patches/2026-11-06-desktop-adsb-1090-truthfulness.md）：
地图未定位时显示「未设置观测地点」。该文案本身没错，但配置**存在且损坏**
时用户会去设置页重填一个其实已经填过的地点——因此降级必须留一条可查记录，
且「文件不存在」不得被记成故障（首运行是正常空态）。
"""
import json
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import adsb_panel  # noqa: E402


def _point_expanduser(monkeypatch, tmp_path, cfg_path):
    """把 _load_station 的 ~/.mbdsdr/config.json 指向临时文件。"""

    def _fake(path):
        if path == "~/.mbdsdr/config.json":
            return str(cfg_path)
        return os.path.expanduser(path)

    monkeypatch.setattr(adsb_panel.os.path, "expanduser", _fake)
    return cfg_path


def test_valid_config_returns_station_without_noise(monkeypatch, tmp_path, caplog):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"ground_station_lat": 31.2,
                               "ground_station_lon": 121.5}), encoding="utf-8")
    _point_expanduser(monkeypatch, tmp_path, cfg)

    with caplog.at_level(logging.WARNING, logger="adsb_panel"):
        lat, lon = adsb_panel._load_station()

    assert (lat, lon) == (31.2, 121.5)
    assert caplog.records == []


def test_missing_config_is_normal_empty_state(monkeypatch, tmp_path, caplog):
    """文件不存在＝本来就没配：返回空，但不得记成一次故障。"""
    _point_expanduser(monkeypatch, tmp_path, tmp_path / "not-there.json")

    with caplog.at_level(logging.WARNING, logger="adsb_panel"):
        assert adsb_panel._load_station() == (None, None)

    assert caplog.records == []


def test_broken_config_is_attributable(monkeypatch, tmp_path, caplog):
    """文件在但 JSON 坏了：行为不变（空站址），但必须留下原因。"""
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json", encoding="utf-8")
    _point_expanduser(monkeypatch, tmp_path, cfg)

    with caplog.at_level(logging.WARNING, logger="adsb_panel"):
        assert adsb_panel._load_station() == (None, None)

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "地面站配置不可用" in msg
    assert "JSONDecodeError" in msg
    assert str(cfg) in msg


def test_illegal_coordinate_types_are_attributable(monkeypatch, tmp_path, caplog):
    """字段类型非法（字符串 "abc"）：同样按坏配置记录，而不是当成未配置。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"ground_station_lat": "abc",
                               "ground_station_lon": 121.5}), encoding="utf-8")
    _point_expanduser(monkeypatch, tmp_path, cfg)

    with caplog.at_level(logging.WARNING, logger="adsb_panel"):
        assert adsb_panel._load_station() == (None, None)

    assert len(caplog.records) == 1
    assert "ValueError" in caplog.records[0].getMessage()
