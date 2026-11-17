# SPDX-License-Identifier: MIT
"""Task 80：把文化星名接入「指向即识别」与 `sky_whats_here`。

判据
----
1. 显式请求文化时，命中的星带 ``culture_names``，且只含请求的文化；
2. 某文化没有该 HIP 的名字 → 不出现该键，**不许**拿别家名字/默认名冒充；
3. 不传 ``culture_ids`` → 输出与旧版逐字兼容（无 ``culture_names`` /
   ``unknown_cultures`` 键）；
4. 未知文化 id 不静默吞掉：在顶层 ``unknown_cultures`` 列出，其余正常；
5. 工具面默认与显式两条路径行为一致。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.skyengine.sky_culture import (  # noqa: E402
    list_packs,
    names_for_hip,
)
from mbdsdr_ai.sky_pointing import identify_direction  # noqa: E402

_LAT = 45.0
_LON = 120.0
_JD = 2461359.5  # 与 test_sky_pointing 同一固定时刻
_POLARIS_HIP = 11767


def _pointing_for_hip(hip: int):
    """用随仓星表算出该 HIP 此刻的地平 az/alt（不另造坐标链）。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    idx = {int(h): i for i, h in enumerate(cat.hip) if h > 0}
    v = vec_from_radec(float(cat.ra_deg[idx[hip]]), float(cat.dec_deg[idx[hip]]))
    gs = GroundStation(_LON, _LAT, 0.0, "test")
    az, alt = azalt_from_vec(j2000_to_altaz(v, gs, _JD))
    return float(az), float(alt)


@pytest.fixture(scope="module")
def polaris_pointing():
    return _pointing_for_hip(_POLARIS_HIP)


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    ag = MBDSDRAgent(AgentConfig())
    ag.observer.latitude_deg = _LAT
    ag.observer.longitude_deg = _LON
    ag.observer.height_m = 0.0
    return ag


def _star_with_hip(out, hip):
    return next(s for s in out["stars"] if s["hip"] == hip)


def _missing_culture_for(hip: int) -> str:
    """挑一个当前确实没有该 HIP 名字的文化包 id。"""
    named = set(names_for_hip(hip))
    for cid in list_packs():
        if cid not in named:
            return cid
    pytest.skip(f"所有文化包都给 HIP {hip} 起了名字，缺名分支无法构造")


class TestIdentifyDirection:
    def test_returns_only_requested_culture_names(self, polaris_pointing):
        az, alt = polaris_pointing
        out = identify_direction(az, alt, 3.0, _JD, _LAT, _LON,
                                 limit=10,
                                 culture_ids=["iau_88", "chinese_xingguan"])
        assert out["matched"] is True
        star = _star_with_hip(out, _POLARIS_HIP)
        expected = {cid: names_for_hip(_POLARIS_HIP)[cid]
                    for cid in ("iau_88", "chinese_xingguan")}
        assert star["culture_names"] == expected
        # 每个文化的名字分开保留，不合并成一个字符串。
        assert star["culture_names"]["iau_88"]["en"] == "Polaris"
        assert star["culture_names"]["chinese_xingguan"]["zh"]
        assert out["unknown_cultures"] == []

    def test_missing_culture_key_is_absent_not_borrowed(self, polaris_pointing):
        az, alt = polaris_pointing
        missing = _missing_culture_for(_POLARIS_HIP)
        out = identify_direction(az, alt, 3.0, _JD, _LAT, _LON,
                                 limit=10, culture_ids=["iau_88", missing])
        star = _star_with_hip(out, _POLARIS_HIP)
        assert "iau_88" in star["culture_names"]
        # 缺名语义：键不存在，而不是空 dict，也不是别家名字。
        assert missing not in star["culture_names"]
        assert star["culture_names"] != {"iau_88": {"en": "Polaris"},
                                          missing: {"en": "Polaris"}}

    def test_default_output_has_no_culture_fields(self, polaris_pointing):
        az, alt = polaris_pointing
        out = identify_direction(az, alt, 3.0, _JD, _LAT, _LON, limit=10)
        assert "unknown_cultures" not in out
        assert out["stars"]
        for star in out["stars"]:
            assert "culture_names" not in star

    def test_unknown_culture_is_listed_and_rest_still_works(self, polaris_pointing):
        az, alt = polaris_pointing
        out = identify_direction(az, alt, 3.0, _JD, _LAT, _LON, limit=10,
                                 culture_ids=["iau_88", "not_a_culture"])
        assert out["matched"] is True
        assert out["unknown_cultures"] == ["not_a_culture"]
        star = _star_with_hip(out, _POLARIS_HIP)
        assert set(star["culture_names"]) == {"iau_88"}


class TestWhatsHereCultureNames:
    @staticmethod
    def _call(agent, args):
        r = agent.tool_registry.call("sky_whats_here", args)
        return json.loads(r.content)

    def test_default_has_no_culture_fields(self, agent, polaris_pointing):
        az, alt = polaris_pointing
        d = self._call(agent, {"az_deg": az, "alt_deg": alt,
                               "fov_deg": 3.0, "jd_ut1": _JD})
        assert d["ok"] is True
        assert "unknown_cultures" not in d
        assert d["stars"]
        for star in d["stars"]:
            assert "culture_names" not in star

    def test_explicit_culture_names_and_unknown_listing(self, agent,
                                                        polaris_pointing):
        az, alt = polaris_pointing
        missing = _missing_culture_for(_POLARIS_HIP)
        d = self._call(agent, {
            "az_deg": az, "alt_deg": alt, "fov_deg": 3.0, "jd_ut1": _JD,
            "culture_ids": ["iau_88", missing, "not_a_culture"],
        })
        assert d["ok"] is True
        assert d["unknown_cultures"] == ["not_a_culture"]
        star = next(s for s in d["stars"] if s["hip"] == _POLARIS_HIP)
        assert star["culture_names"] == {
            "iau_88": names_for_hip(_POLARIS_HIP)["iau_88"]}
        assert missing not in star["culture_names"]
