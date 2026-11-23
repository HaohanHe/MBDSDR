# SPDX-License-Identifier: MIT
"""sky_panorama：让模型做判断前一次拿到「此刻全景」。

钉三件事：
1. **一块给全**：站址/时刻/太阳/月球/天气/空间天气/今夜 + missing 清单，
   模型不必自己猜该调哪些工具。
2. **不猜**：缺云量就不是晴天，缺 Kp/F10.7/SSN 就不是「传播平静」；
   别站快照不顶替本站数据。
3. **不联网副作用**：站址匹配的 15 分钟快照直接复用；allow_live=false
   时绝不现采。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(scope="module")
def agent():
    """模块级共享：MBDSDRAgent 构造会探测硬件，逐例新建太慢。"""
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    ag = MBDSDRAgent(AgentConfig())
    ag.observer.latitude_deg = 45.0
    ag.observer.longitude_deg = 120.0
    ag.observer.height_m = 200.0
    return ag


def _snap(*, cloud=30.0, kp=2.0, f107=120.0, ssn=60.0, xray=1.5e-7,
          lat=45.0, lon=120.0):
    def sample(key, value, measurements=None, error=""):
        return {
            "key": key, "value": value, "measurements": dict(measurements or {}),
            "error": error, "observed_at": "2026-11-23T12:00:00Z",
            "source": "test-source", "url": "https://example.invalid/" + key,
        }

    measurements = {"temperature_c": 3.0, "relative_humidity_pct": 55.0,
                    "surface_pressure_hpa": 1010.0}
    if cloud is not None:
        measurements["cloud_cover_pct"] = cloud
    return {
        "schema": 1,
        "taken_at": "2026-11-23T12:00:00Z",
        "station": {"lat": lat, "lon": lon},
        "samples": {
            "weather": sample("weather", 0.0, measurements),
            "kp": sample("kp", kp),
            "f107": sample("f107", f107),
            "ssn": sample("ssn", ssn),
            "xray": sample("xray", xray),
        },
    }


def _call(agent, **args):
    r = agent.tool_registry.call("sky_panorama", args)
    assert r.success is True, r.content
    return json.loads(r.content)


def test_panorama_returns_all_blocks_from_matching_snapshot(agent, monkeypatch):
    from mbdsdr_ai import space_weather as sw

    monkeypatch.setattr(sw, "latest_snapshot",
                        lambda **kw: (_snap(), "snap.json"))

    def _no_network(**kw):
        raise AssertionError("站址匹配的快照却去联网了")

    monkeypatch.setattr(sw, "collect_snapshot", _no_network)
    d = _call(agent)
    assert d["ok"] is True
    assert d["schema"] == "mbdsdr.sky-panorama/1"
    assert d["site"]["note"] == "取自 agent 观测站"
    assert d["weather"]["known"] is True
    assert d["weather"]["cloud_cover_pct"] == 30.0
    assert d["weather"]["temperature_c"] == 3.0
    assert d["space_weather"]["known"] is True
    assert d["space_weather"]["kp"] == 2.0
    assert d["space_weather"]["f107"] == 120.0
    assert d["space_weather"]["ssn"] == 60.0
    assert d["space_weather"]["xray_class"] == "B"
    assert d["space_weather"]["conclusion"]
    assert "站址匹配" in d["space_weather"]["source_note"]
    assert d["tonight"]["index"]["known"] is True
    assert 0.0 <= float(d["tonight"]["index"]["score"]) <= 100.0
    assert d["sun"]["phase"] in {
        "天文夜（天已全黑）", "天文暮光", "航海暮光", "民用暮光", "白天"}
    assert -90.0 <= float(d["sun"]["alt_deg"]) <= 90.0
    assert "space_weather.kp" not in d["missing"]


def test_panorama_missing_cloud_is_not_clear_sky(agent, monkeypatch):
    from mbdsdr_ai import space_weather as sw

    monkeypatch.setattr(sw, "latest_snapshot",
                        lambda **kw: (_snap(cloud=None), "snap.json"))
    d = _call(agent)
    assert d["weather"]["known"] is False
    assert d["weather"]["cloud_cover_pct"] is None
    assert "weather.cloud_cover_pct" in d["missing"]
    assert "cloud_cover" in d["weather"]["reason"]
    # 云量缺测不应让空间天气段跟着变未知
    assert d["space_weather"]["known"] is True


def test_panorama_missing_space_weather_is_not_quiet(agent, monkeypatch):
    from mbdsdr_ai import space_weather as sw

    monkeypatch.setattr(sw, "latest_snapshot",
                        lambda **kw: (_snap(kp=None, f107=None, ssn=None,
                                            xray=None), "snap.json"))
    d = _call(agent)
    assert d["space_weather"]["known"] is False
    assert "space_weather.kp" in d["missing"]
    assert "space_weather.f107" in d["missing"]
    assert "space_weather.ssn" in d["missing"]
    assert d["space_weather"]["advice"] == []
    assert "未知" in d["space_weather"]["conclusion"]


def test_panorama_rejects_foreign_station_snapshot(agent, monkeypatch):
    from mbdsdr_ai import space_weather as sw

    monkeypatch.setattr(sw, "latest_snapshot",
                        lambda **kw: (_snap(lat=39.9, lon=116.4), "other.json"))

    def _no_network(**kw):
        raise AssertionError("allow_live=false 却去联网了")

    monkeypatch.setattr(sw, "collect_snapshot", _no_network)
    d = _call(agent, allow_live=False)
    assert d["space_weather"]["known"] is False
    assert "站址不符" in d["space_weather"]["source_note"]
    assert "space_weather.snapshot" in d["missing"]
    assert d["weather"]["known"] is False


def test_panorama_live_collect_is_used_when_allowed(agent, monkeypatch):
    from mbdsdr_ai import space_weather as sw

    monkeypatch.setattr(sw, "latest_snapshot", lambda **kw: None)
    seen = {}

    def _collect(**kw):
        seen.update(kw)
        return _snap(cloud=0.0)

    monkeypatch.setattr(sw, "collect_snapshot", _collect)
    d = _call(agent)
    assert seen.get("lat") == 45.0 and seen.get("lon") == 120.0
    assert d["space_weather"]["source_note"].startswith("现采")
    assert d["weather"]["known"] is True
    assert d["weather"]["cloud_cover_pct"] == 0.0


def test_sun_phase_boundaries():
    from mbdsdr_ai.sky_tools import _sun_phase

    assert _sun_phase(-25.0) == "天文夜（天已全黑）"
    assert _sun_phase(-18.0) == "天文夜（天已全黑）"
    assert _sun_phase(-15.0) == "天文暮光"
    assert _sun_phase(-8.0) == "航海暮光"
    assert _sun_phase(-3.0) == "民用暮光"
    assert _sun_phase(0.0) == "民用暮光"
    assert _sun_phase(30.0) == "白天"


@pytest.mark.parametrize("question", [
    "今天传播怎么样？",
    "太阳活动强不强，有没有地磁暴",
    "今晚天气如何，云量多少",
    "现在能看到极光吗",
])
def test_tool_selection_surfaces_panorama(question):
    """问传播/太阳活动/天气时，sky_panorama 必须进候选（不然模型看不见）。"""
    from mbdsdr_ai.tool_budget import select_tools

    defs = [
        {"type": "function", "function": {
            "name": "sky_panorama",
            "description": "此刻全景：天气、空间天气、传播、今夜可看性"}},
        {"type": "function", "function": {
            "name": "rtl_decode_adsb", "description": "ADS-B 解码"}},
    ]
    cats = {"sky_panorama": "sky", "rtl_decode_adsb": "decode"}
    sel = select_tools(defs, question, budget_tokens=10000, categories=cats)
    assert "sky_panorama" in sel.kept_names, (question, sel.to_dict())
