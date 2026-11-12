# SPDX-License-Identifier: MIT
"""A03：风云过境工具不再把「配置坏了」误诊为「没配置」（第二批）。

反证：把 _pass_handler 的配置读取改回 ``except Exception: pass``，
本用例会拿到「未配置地面站坐标」文案且 error 码不同 → 变红。
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)


def _registry():
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.register_fengyun_tools()
    return reg


def _point_home_config(monkeypatch, path):
    real = os.path.expanduser
    cfg = str(path)

    def fake(p):
        if p in ("~/.mbdsdr/config.json", os.path.join("~", ".mbdsdr", "config.json")):
            return cfg
        return real(p)

    monkeypatch.setattr(os.path, "expanduser", fake)


def test_corrupt_config_is_reported_not_misdiagnosed(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ not json", encoding="utf-8")
    _point_home_config(monkeypatch, cfg)

    r = _registry().call("fy3_pass_predict", {})

    assert r.success is False
    assert "地面站配置读取失败" in r.content
    assert r.error == "station_config_unreadable"
    assert "未配置地面站坐标" not in r.content


def test_missing_config_keeps_original_hint(tmp_path, monkeypatch):
    _point_home_config(monkeypatch, tmp_path / "missing.json")

    r = _registry().call("fy3_pass_predict", {})

    assert r.success is False
    assert "未配置地面站坐标" in r.content
    assert r.error != "station_config_unreadable"
