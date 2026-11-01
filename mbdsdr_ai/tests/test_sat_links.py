# SPDX-License-Identifier: MIT
"""卫星链路目录（sat_links v1）钉扎层。

红线（与 sat_profile 同一套「算法进工具、皮进配置」纪律）：
  * sat_links.json 里的下行频点（≥1e7）不得出现在任何 Python 源码——
    grep 级防回归，命中即红；
  * 每条带 source 可追溯；只含 downlink（上行走 arm_manual，永不入表）；
  * 未知星名/负容差诚实报错；无命中如实空态，不猜。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai import sat_links as SL

PKG_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PKG_DIR / "data"


def _link_literals() -> set:
    """sat_links.json 里的下行频点（字符串形），用于源码扫描。"""
    table = json.loads((DATA_DIR / "sat_links.json").read_text("utf-8"))
    lits: set = set()
    for name, entry in table.items():
        if name.startswith("_"):
            continue
        for link in entry.get("links", []):
            v = link.get("freq_hz")
            if isinstance(v, (int, float)) and abs(v) >= 1e7:
                lits.add(repr(v))
                lits.add(repr(int(v)))
    return lits


def test_no_link_literals_in_python_source():
    """下行频点只存在于配置 JSON，源码 grep 级零命中。"""
    lits = _link_literals()
    assert lits, "链路表里应至少有一个频点"
    hits = []
    for py in sorted(PKG_DIR.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        for lit in lits:
            if lit in src:
                hits.append(f"{py.name}: {lit}")
    assert not hits, (
        "卫星下行频点泄漏进源码（应只存在于 data/sat_links.json）：\n  "
        + "\n  ".join(hits))


def test_every_entry_has_source_norad_and_downlink():
    table = json.loads((DATA_DIR / "sat_links.json").read_text("utf-8"))
    for name, entry in table.items():
        if name.startswith("_"):
            continue
        assert entry.get("source"), f"{name} 缺 source"
        assert entry.get("norad_name"), f"{name} 缺 norad_name"
        assert entry.get("culture"), f"{name} 缺 culture 星性注记"
        links = entry.get("links", [])
        assert links, f"{name} 至少一条链路"
        for link in links:
            assert link.get("direction") == "downlink", \
                f"{name} 含非下行链路（上行严禁入表）"
            assert link.get("mode"), f"{name} 链路缺 mode"
            assert float(link["freq_hz"]) > 1e6


def test_unknown_link_honest_error():
    with pytest.raises(ValueError, match="未知卫星链路"):
        SL.load_sat_link("no_such_sat_xyz")
    with pytest.raises(ValueError, match="容差必须为正"):
        SL.identify_by_freq(137.1e6, tolerance_hz=-1.0)


def test_identify_by_freq_hits_and_empty():
    hits = SL.identify_by_freq(137.1e6, tolerance_hz=3000.0)
    names = {h["_link_name"] for h in hits}
    assert "noaa_19" in names
    assert "meteor_m_n2" in names   # 同频共存：诚实并列，不强行裁决
    empty = SL.identify_by_freq(500.0e6, tolerance_hz=1000.0)
    assert empty == []


def test_identify_respects_tolerance_boundary():
    # 137.100 MHz + 3.1 kHz 超出默认 3 kHz 容差 → 不命中 NOAA-19
    assert SL.identify_by_freq(137.100e6 + 3100.0, tolerance_hz=3000.0) == []
    # 放宽容差即命中
    assert SL.identify_by_freq(137.100e6 + 3100.0, tolerance_hz=4000.0)


def test_find_tuning_for_by_norad_name():
    found = SL.find_tuning_for("ISS (ZARYA)")
    assert found is not None
    assert found["downlink"]["mode"] == "nfm"
    # TLE 名大小写不敏感；未知名如实 None
    assert SL.find_tuning_for("iss (zarya)") is not None
    assert SL.find_tuning_for("NO SUCH SAT") is None


def test_find_tuning_for_alias_and_spelling():
    """TLE 拼法差异都要命中：tle_aliases 与空白/连字符归一。"""
    # 目录 norad_name 是 "METEOR-M 2"，轨道工具手里的 TLE 名是 "METEOR M2"。
    via_alias = SL.find_tuning_for("METEOR M2")
    assert via_alias is not None
    assert via_alias["entry"]["norad_name"] == "METEOR-M 2"
    # 连字符/大小写归一："NOAA-19"、"noaa19" 都应命中 NOAA 19。
    a = SL.find_tuning_for("NOAA-19")
    b = SL.find_tuning_for("noaa19")
    assert a is not None and b is not None
    assert a["entry"]["_link_name"] == b["entry"]["_link_name"] == "noaa_19"
    # 空白名归一：ISS 带不带空格都中。
    assert SL.find_tuning_for("ISS(ZARYA)") is not None


def test_decoders_freq_table_derives_from_catalog():
    """decoders.SATELLITE_FREQUENCIES 皮进自目录：同值、键经 tle_aliases 桥接。"""
    from mbdsdr_ai.decoders import SATELLITE_FREQUENCIES
    expect: dict = {}
    for name in SL.list_link_sats():
        entry = SL.load_sat_link(name)
        links = [l for l in entry["links"] if l["direction"] == "downlink"]
        mhz = float(links[0]["freq_hz"]) / 1e6
        for tle_name in [entry["norad_name"]] + entry.get("tle_aliases", []):
            expect[tle_name] = mhz
    assert SATELLITE_FREQUENCIES == expect
    # TLE 拼法桥（BUILTIN_TLE 用 "METEOR M2"）必须命中。
    assert SATELLITE_FREQUENCIES["METEOR M2"] == \
        expect["METEOR-M 2"]
    # FENGYUN 3D 是诚实缺口：其 AHRPT 在 1698–1710 MHz L 波段，不在目录
    # 收听面内；旧源码硬记的 137.100 系讹误，迁移后不得复活。
    assert "FENGYUN 3D" not in SATELLITE_FREQUENCIES


def test_tool_registry_smoke():
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    SL.register_tool_registry(reg)
    for tool in ("sat_link_list", "sat_link_get",
                 "sat_link_identify", "sat_link_tuning"):
        r = reg.call(tool, {})
        assert r is not None
    ok = reg.call("sat_link_get", {"name": "noaa_19"})
    assert getattr(ok, "success", False)
    miss = reg.call("sat_link_get", {"name": "nope"})
    assert not getattr(miss, "success", True)
    tune = reg.call("sat_link_tuning", {"norad_name": "NOAA 19"})
    assert getattr(tune, "success", False)
    assert tune.data["found"]
    no_tune = reg.call("sat_link_tuning", {"norad_name": "XYZ"})
    assert no_tune.data["found"] is False
