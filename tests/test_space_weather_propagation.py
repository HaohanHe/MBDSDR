# SPDX-License-Identifier: MIT
"""传播条件实时驱动：快照、缺值诚实、回放一致性。

判据（对应任务）：
- 高 Kp 与低 Kp 的建议文本必须不同（不许一句静日文案走天下）；
- 数据源失败 → 「当前传播条件未知（原因）」，不给「平静」建议；
- 同一快照回放两次结论一致；快照落盘再读回，时刻/来源不丢。
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai import space_weather as sw
from mbdsdr_ai.acquisition import register_acquisition_tools
from mbdsdr_ai.tool_registry import ToolRegistry


def _sample(key, value, display=None, observed="2026-11-14T03:00:00Z"):
    meta = sw.SOURCES[key]
    return {
        "key": key, "label": meta["label"], "value": value,
        "display": display if display is not None else (f"{value:g}" if value is not None else "未取到"),
        "unit": "", "observed_at": observed, "fetched_at": "2026-11-14T03:20:00Z",
        "source": meta["source"], "url": meta["url"],
        "error": "" if value is not None else "URLError: 网络不可达",
    }


def _snap(kp=2.0, f107=120.0, ssn=80, xray=1e-7, weather=0.0,
          broken=()):
    samples = {
        "kp": _sample("kp", kp),
        "f107": _sample("f107", f107),
        "ssn": _sample("ssn", ssn),
        "xray": _sample("xray", xray),
        "weather": _sample("weather", weather, "降水 0 mm"),
    }
    for key in broken:
        samples[key] = _sample(key, None)
    return {"schema": sw.SNAPSHOT_SCHEMA, "taken_at": "2026-11-14T03:20:00Z",
            "station": {"lat": 39.9, "lon": 116.4}, "samples": samples}


@pytest.fixture()
def reg():
    r = ToolRegistry()
    register_acquisition_tools(r)
    return r


# ── 判定必须随输入变化 ────────────────────────────────────────
def test_high_kp_advice_differs_from_low_kp():
    calm = sw.assess(_snap(kp=1.3))
    storm = sw.assess(_snap(kp=6.7))
    assert calm["known"] and storm["known"]
    assert calm["conclusion"] != storm["conclusion"]
    assert calm["advice"] != storm["advice"]
    assert "地磁扰动" in storm["advice"][0]
    assert "地磁平静" in calm["advice"][0]


def test_conclusion_carries_observed_time_and_source():
    text = sw.format_propagation_section(sw.assess(_snap(kp=6.7)))
    assert "Kp" in text
    assert "2026-11-14T03:00:00Z" in text      # 观测时刻
    assert "NOAA SWPC" in text                  # 来源


# ── 缺值必须诚实：不设「默认平静」兜底 ────────────────────────
@pytest.mark.parametrize("missing", ["kp", "f107", "ssn"])
def test_missing_required_value_is_unknown_not_calm(missing):
    a = sw.assess(_snap(broken=(missing,)))
    assert a["known"] is False
    assert a["advice"] == []
    assert "未知" in a["conclusion"]
    assert "地磁平静" not in a["conclusion"]
    assert a["unknown_reasons"]


def test_no_snapshot_is_unknown():
    a = sw.assess(None)
    assert a["known"] is False
    assert a["advice"] == []
    assert "未知" in a["conclusion"]


def test_source_failure_shows_unknown_in_tool_output(reg):
    res = reg.call("fe_diagnose", {"symptom": "无信号",
                                   "propagation": _snap(broken=("kp", "f107", "ssn"))})
    assert res.success
    assert "当前传播条件未知" in res.content
    assert "地磁平静" not in res.content
    assert res.data["propagation"]["known"] is False


def test_tool_output_shows_time_and_source(reg):
    res = reg.call("fe_diagnose", {"symptom": "无信号", "propagation": _snap(kp=6.7)})
    assert res.success
    assert "2026-11-14T03:00:00Z" in res.content
    assert "NOAA SWPC" in res.content
    assert "地磁扰动" in res.content
    assert res.data["propagation"]["known"] is True


# ── 快照：落盘回放一致 ────────────────────────────────────────
def test_snapshot_roundtrip_keeps_time_and_source(tmp_path):
    snap = _snap(kp=5.3)
    path = sw.save_snapshot(snap, save_dir=str(tmp_path))
    loaded = sw.load_snapshot(path)
    assert loaded["samples"]["kp"]["observed_at"] == "2026-11-14T03:00:00Z"
    assert loaded["samples"]["kp"]["source"] == sw.SOURCES["kp"]["source"]
    assert sw.assess(loaded) == sw.assess(snap)


def test_replay_twice_is_identical():
    snap = _snap(kp=4.5)
    a1 = sw.assess(snap)
    a2 = sw.assess(snap)
    assert a1 == a2
    assert (sw.format_propagation_section(a1) == sw.format_propagation_section(a2))


# ── 采集：单项失败不拖垮其他项，失败写原因 ────────────────────
def test_collect_snapshot_records_per_sample_failure():
    def boom(url):
        raise OSError("网络不可达")

    snap = sw.collect_snapshot(lat=39.9, lon=116.4, fetcher=boom)
    for key in ("kp", "f107", "ssn", "xray", "weather"):
        assert snap["samples"][key]["value"] is None
        assert "网络不可达" in snap["samples"][key]["error"]
    assert sw.assess(snap)["known"] is False


def test_collect_snapshot_parses_known_payloads():
    payloads = {
        sw.SOURCES["kp"]["url"]: [
            ["time_tag", "Kp", "a_running", "station_count"],
            ["2026-11-14 00:00:00.000", "1.67", "5", "8"],
            ["2026-11-14 03:00:00.000", "5.33", "48", "8"],
        ],
        sw.SOURCES["f107"]["url"]: [
            {"time_tag": "2026-11-13T20:00:00Z", "flux": 168.0},
        ],
        sw.SOURCES["ssn"]["url"]: [
            {"time-tag": "2026-11-01T00:00:00Z", "ssn": 112.0},
        ],
        sw.SOURCES["xray"]["url"]: [
            {"time_tag": "2026-11-14T03:18:00Z", "energy": "0.1-0.8nm", "flux": 2.0e-6},
            {"time_tag": "2026-11-14T03:18:00Z", "energy": "0.05-0.4nm", "flux": 9.0e-7},
        ],
    }

    def fake(url):
        if url.startswith(sw.SOURCES["weather"]["url"]):
            return {"current": {"time": "2026-11-14T03:20", "temperature_2m": 6.0,
                                "relative_humidity_2m": 40, "precipitation": 0.0,
                                "weather_code": 1}}
        return payloads[url]

    snap = sw.collect_snapshot(lat=39.9, lon=116.4, fetcher=fake,
                               now=__import__("datetime").datetime(
                                   2026, 11, 14, 3, 20, tzinfo=__import__("datetime").timezone.utc))
    assert snap["samples"]["kp"]["value"] == 5.33
    assert snap["samples"]["kp"]["observed_at"] == "2026-11-14 03:00:00.000"
    assert snap["samples"]["f107"]["value"] == 168.0
    assert "C（" in snap["samples"]["xray"]["display"]
    a = sw.assess(snap)
    assert a["known"] is True
    assert "地磁扰动" in a["advice"][0]
