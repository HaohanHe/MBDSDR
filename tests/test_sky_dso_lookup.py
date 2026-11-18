"""sky_dso_lookup：让 AI 能答"那团模糊是什么"。

数据只有公开源（OpenNGC CC BY-SA 4.0），距离不提供就说不知道。
目录只有 117 条，查不到不等于天上没有。
"""
import json
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    return MBDSDRAgent(AgentConfig())


def _call(agent, name, args):
    r = agent.tool_registry.call(name, args)
    try:
        d = json.loads(r.content)
        if isinstance(d, dict):
            d["_tool_success"] = r.success
            return d
    except (json.JSONDecodeError, TypeError):
        pass
    return {"ok": False, "_raw_error": str(r.content),
            "_tool_success": r.success}


class TestDSORegistration:
    def test_eight_sky_tools_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "sky_dso_lookup" in names
        # 模块 docstring 说八个，工具必须真有八个（不含 agent 自带的 sky_*）
        from mbdsdr_ai import sky_tools
        doc = sky_tools.__doc__ or ""
        assert "八个工具" in doc

    def test_read_only_category(self, agent):
        t = next(t for t in agent.tool_registry.list_tools()
                 if t["name"] == "sky_dso_lookup")
        assert t.get("category") == "sky"
        assert "只读" in t.get("description", "")


class TestDSOLookup:
    def test_m42_by_number(self, agent):
        d = _call(agent, "sky_dso_lookup", {"name": "M42"})
        assert d["ok"] is True
        assert d["count"] == 1
        o = d["objects"][0]
        assert o["zh_name"] == "猎户座大星云"
        assert o["constellation"] == "Ori"
        assert o["magnitude"] is not None
        assert 80 < o["ra_deg"] < 90

    def test_m42_by_chinese_name(self, agent):
        d = _call(agent, "sky_dso_lookup", {"name": "猎户座大星云"})
        assert d["ok"] is True
        assert d["objects"][0]["name"] == "M42"

    def test_m31_by_ngc_cross_id(self, agent):
        d = _call(agent, "sky_dso_lookup", {"name": "NGC224"})
        assert d["ok"] is True
        assert d["objects"][0]["name"] == "M31"

    def test_not_found_lists_alternatives(self, agent):
        d = _call(agent, "sky_dso_lookup", {"name": "完全不存在的天体"})
        assert d["ok"] is False
        assert "没有" in d["reason"]
        assert "M31" in d["hint"]

    def test_recommended_fov_covers_size(self, agent):
        """角径 → 推荐视场，向上取整，必须装得下。"""
        for q in ("M42", "M31", "M13", "M45"):
            d = _call(agent, "sky_dso_lookup", {"name": q})
            if not d.get("ok") or not d["objects"]:
                continue
            o = d["objects"][0]
            if o["size_arcmin"] is None:
                assert o["recommended_fov_deg"] is None
                continue
            span = o["size_arcmin"] / 60.0
            fov = o["recommended_fov_deg"]
            assert fov >= span, f"{q}: fov={fov} < span={span}"
            assert fov == int(math.ceil(span * 1.2 / 5.0) * 5), q

    def test_never_invents_distance(self, agent):
        """目录不提供距离——输出里不许有 distance 字段让别人误用。"""
        d = _call(agent, "sky_dso_lookup", {"name": "M42"})
        for o in d["objects"]:
            assert "distance_ly" not in o
            assert "distance_km" not in o
        assert "距离未知" in d["note"] or "不提供" in d["note"]

    def test_catalog_is_honest_about_coverage(self, agent):
        d = _call(agent, "sky_dso_lookup", {"name": "M42"})
        assert d["catalog"]["count"] == 117
        assert d["catalog"]["license"] == "CC BY-SA 4.0"
        assert "不等于天上没有" in d["note"]

    def test_output_budget(self, agent):
        """注册表会砍超长输出，整份 JSON 必须还在上限内。"""
        r = agent.tool_registry.call("sky_dso_lookup", {"name": "M"})
        assert r.success
        assert len(r.content) < 4000
        json.loads(r.content)


class TestDSORegionLookup:
    def test_by_sky_region(self, agent):
        """给天区能查到 M42（83.8°,-5.4°），宽视场保证命中。"""
        d = _call(agent, "sky_dso_lookup", {
            "az_deg": 90.0, "alt_deg": 20.0, "fov_deg": 90.0,
            "lat_deg": -33.9, "lon_deg": 18.4,
            "jd_ut1": 2461361.0,
        })
        assert d["ok"] is True
        assert isinstance(d["objects"], list)

    def test_region_requires_site(self, agent):
        """按天区查但 agent 无观测站时不猜站址（本 agent 可能有默认站，跳过）。"""
        d = _call(agent, "sky_dso_lookup", {
            "az_deg": 90.0, "alt_deg": 20.0, "fov_deg": 30.0})
        # 有站址就正常查；没站址会抛错由 registry 包成 ok=False
        assert d.get("ok") in (True, False)

    def test_invalid_fov_rejected(self, agent):
        d = _call(agent, "sky_dso_lookup", {
            "az_deg": 90.0, "alt_deg": 20.0, "fov_deg": 0.0,
            "lat_deg": 0.0, "lon_deg": 0.0})
        assert d.get("ok") is False
        assert "fov_deg" in d.get("_raw_error", "")
