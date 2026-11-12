# SPDX-License-Identifier: MIT
"""A03：定轨/LRO 工具不再把「配置坏了」误诊为「没配置」（第二批）。

覆盖 _latlon（doppler_orbit_determine）与 _lro_track 两个入口。

反证：把任一入口的配置读取改回 ``except Exception: pass`` 后，
对应用例会拿到「未配置/未提供」文案 → 变红。
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)


def _registry():
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.register_orbit_determination_tools()
    return reg


def _point_home_config(monkeypatch, path):
    real = os.path.expanduser
    cfg = str(path)

    def fake(p):
        if p in ("~/.mbdsdr/config.json", os.path.join("~", ".mbdsdr", "config.json")):
            return cfg
        return real(p)

    monkeypatch.setattr(os.path, "expanduser", fake)


def test_lro_track_reports_corrupt_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ not json", encoding="utf-8")
    _point_home_config(monkeypatch, cfg)

    r = _registry().call("lro_track", {})

    assert r.success is False
    assert "地面站配置读取失败" in r.content
    assert r.error == "station_config_unreadable"


def test_lro_track_missing_config_keeps_original_hint(tmp_path, monkeypatch):
    _point_home_config(monkeypatch, tmp_path / "missing.json")
    r = _registry().call("lro_track", {})
    assert r.success is False
    assert "未配置地面站坐标" in r.content


def test_doppler_reports_corrupt_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ not json", encoding="utf-8")
    _point_home_config(monkeypatch, cfg)

    r = _registry().call("doppler_orbit_determine", {
        "observations": [[1.0, 100.0]],
        "init_state": [7000.0, 0.0, 0.0, 0.0, 7.5, 0.0, 0.0],
    })

    assert r.success is False
    assert "地面站配置读取失败" in r.content
    assert "未提供地面站坐标" not in r.content
