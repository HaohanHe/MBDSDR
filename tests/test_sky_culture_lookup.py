# SPDX-License-Identifier: MIT
"""`sky_culture_lookup` 工具面测试（Task 68）。

判据
----
1. 同一 HIP 在各文化下的名称**分开返回**，绝不被合并成一个名字；
2. 按名称能查到组（Ursa Minor）与成员 HIP，也能查到西名（Polaris）；
3. 未知 HIP / 未知名称返回 ``matched=False`` 与原因，不返回最近邻凑数；
4. ``culture`` 过滤只留下指定文化；未知文化 id 如实拒绝并列出可用 id；
5. 工具已注册，且属于只读工具（不进写门）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def agent():
    """模块级共享：MBDSDRAgent 构造会探测设备，逐例新建太慢。"""
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    return MBDSDRAgent(AgentConfig())


def _call(agent, args):
    """调用工具并解析 JSON；业务性失败同样优先解析 content。"""
    r = agent.tool_registry.call("sky_culture_lookup", args)
    data = json.loads(r.content)
    data["_tool_success"] = r.success
    return data


class TestRegistration:
    def test_tool_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "sky_culture_lookup" in names

    def test_tool_is_read_only(self, agent):
        from mbdsdr_ai.tool_registry import _auto_write_by_name

        assert not _auto_write_by_name("sky_culture_lookup")


class TestHipLookup:
    def test_hip_returns_multiple_cultures_without_merging(self, agent):
        """北极星 HIP 11767 在多文化下各有名字，必须逐文化分开。"""
        d = _call(agent, {"hip": 11767})
        assert d["ok"] is True and d["matched"] is True
        assert d["query"] == {"hip": 11767}

        cultures = {c["culture"]: c for c in d["cultures"]}
        assert {"chinese_xingguan", "japanese", "indian",
                "norse", "seri"} <= set(cultures)
        # 每个文化自己的名字表，不许合并成一份。
        assert cultures["japanese"]["names"]
        assert cultures["indian"]["names"]
        distinct = {json.dumps(c["names"], ensure_ascii=False, sort_keys=True)
                    for c in cultures.values() if c["names"]}
        assert len(distinct) > 1

        # 每个文化自带 id/显示名/许可；许可 URL 是 CC 官方地址。
        for item in cultures.values():
            assert item["display_name"]
            assert item["license"]
            assert item["license_url"].startswith("https://creativecommons.org/")

        # 所属组随文化一起返回（中国的星官组 id 是中文名）。
        assert cultures["chinese_xingguan"]["groups"]
        assert cultures["chinese_xingguan"]["groups"][0]["kind"] == "xingguan"

    def test_hip_groups_are_scoped_to_each_culture(self, agent):
        d = _call(agent, {"hip": 11767, "culture": "iau_88"})
        assert d["ok"] is True
        assert d["culture_count"] == 1
        item = d["cultures"][0]
        assert item["culture"] == "iau_88"
        assert item["groups"][0]["group_id"] == "UMi"

    def test_culture_items_are_capped_with_core_first(self, agent):
        """文化包扩到 30 个后，北极星命中 21 个文化，必须截断且如实说明。"""
        d = _call(agent, {"hip": 11767})
        assert d["culture_count"] >= 21
        assert d["cultures_shown"] <= 10
        assert d["cultures_shown"] == len(d["cultures"])
        assert d["cultures_truncated"] is True

        # 核心体系先出现；其余按 id 排序补齐。
        ordered = [c["culture"] for c in d["cultures"]]
        core = ["iau_88", "chinese_xingguan", "japanese", "indian",
                "norse", "indian_nakshatras", "seri"]
        present = [c for c in core if c in ordered]
        assert ordered[:len(present)] == present

        for item in d["cultures"]:
            assert "groups_omitted" in item
            assert len(item["groups"]) <= 2
            assert item["groups_omitted"] == (
                item["group_count"] - len(item["groups"]))
        # 截断时必须让模型知道还有哪些文化没返回、怎么拿。
        assert "culture" in d["hint"]
        assert "筛选" in d["hint"]

    def test_unknown_hip_is_honest_not_nearest_neighbour(self, agent):
        d = _call(agent, {"hip": 999999})
        assert d["ok"] is False
        assert d["matched"] is False
        assert "不在" in d["reason"]
        assert "最近邻" in d["hint"]

    def test_output_fits_tool_budget(self, agent):
        r = agent.tool_registry.call("sky_culture_lookup", {"hip": 11767})
        assert not r.truncated
        assert len(r.content) <= agent.tool_registry.tool_output_max_chars
        json.loads(r.content)

        # 模型通道走同一个 call()，同样不得被截成非法 JSON。
        r2 = agent.tool_registry.call_from_model({
            "id": "call_culture_budget",
            "type": "function",
            "function": {"name": "sky_culture_lookup",
                         "arguments": json.dumps({"hip": 11767})},
        })
        assert not r2.truncated
        assert len(r2.content) <= agent.tool_registry.tool_output_max_chars
        json.loads(r2.content)


class TestNameLookup:
    def test_group_name_returns_members_and_hips(self, agent):
        d = _call(agent, {"name": "Ursa Minor"})
        assert d["ok"] is True and d["matched"] is True
        assert d["matched_by"] == "group_name"
        hit = d["matches"][0]
        assert hit["culture"] == "iau_88"
        assert hit["group"]["group_id"] == "UMi"
        assert 11767 in hit["hips"]
        assert hit["hip_count"] == len(hit["hips"])

    def test_star_name_returns_owning_group(self, agent):
        d = _call(agent, {"name": "Polaris"})
        assert d["ok"] is True and d["matched"] is True
        assert d["matched_by"] == "star_name"
        assert d["matches"][0]["hip"] == 11767
        assert d["matches"][0]["group"]["group_id"] == "UMi"

    def test_no_match_is_honest(self, agent):
        d = _call(agent, {"name": "__no_such_culture_name__"})
        assert d["ok"] is False
        assert d["matched"] is False
        assert "不在" in d["reason"]


class TestCultureFilter:
    def test_filter_keeps_only_requested_culture(self, agent):
        d = _call(agent, {"hip": 11767, "culture": "japanese"})
        assert d["ok"] is True
        assert d["culture_count"] == 1
        assert d["cultures"][0]["culture"] == "japanese"

    def test_filter_on_name_lookup(self, agent):
        d = _call(agent, {"name": "Ursa Minor", "culture": "norse"})
        # norse 包没有 Ursa Minor 这个组名；过滤后必须诚实答“没有”。
        assert d["matched"] is False

    def test_unknown_culture_lists_available_ids(self, agent):
        d = _call(agent, {"hip": 11767, "culture": "not_a_culture"})
        assert d["matched"] is False
        assert "未知天空文化" in d["reason"]
        assert "iau_88" in d["available_cultures"]

    def test_hip_and_name_are_mutually_exclusive(self, agent):
        r = agent.tool_registry.call("sky_culture_lookup",
                                     {"hip": 11767, "name": "Polaris"})
        assert r.success is False
        assert "二选一" in r.content
