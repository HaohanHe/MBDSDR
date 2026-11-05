# SPDX-License-Identifier: MIT
"""``mbdsdr_ai/web_bridge.py`` 行为测试（离线、无硬件、无网络）。

阶段 B 的对拍重点：
1. Python 的 ``CONTRACT_METHODS`` 必须与产品 JS 的 ``MB.CONTRACT`` 逐字一致；
2. 门字段名必须与 ``repo-facts.json`` 从内核代码抽出的事实一致；
3. 写工具绝不经过 ``registry.call()`` 直接执行（S13 红线）；
4. ``gated``（未获确认）与执行失败严格区分，任何路径都不伪造成功；
5. 离线 / 站址不可信 / 无 provider 时给诚实空值，不编造数据。

测试用 FakeRegistry 只模拟工具注册表的形状，**不声称任何硬件有输出**。
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.web_bridge import (  # noqa: E402
    CONTRACT_METHODS, SELF_REPORT_FIELDS, TOKEN_FIELDS, WebBridge, envelope,
)

CORE_JS = os.path.join(_ROOT, "web", "product", "src", "00-core.js")
FACTS_JSON = os.path.join(_ROOT, "web", "product", "build", "repo-facts.json")


class FakeResult:
    def __init__(self, **fields):
        self._d = dict(fields)

    def to_dict(self):
        return dict(self._d)


class FakeRegistry:
    def __init__(self):
        self.tools = {}
        self.calls = []

    def register(self, name, *, write=False, available=True):
        self.tools[name] = {
            "name": name, "description": "desc-" + name,
            "category": "test", "available": available,
            "parameters": {"type": "object", "properties": {}},
            "write": write,
        }

    def get_tool_names(self):
        return list(self.tools)

    def list_tools(self, include_unavailable=False):
        return [dict(t) for t in self.tools.values()
                if include_unavailable or t["available"]]

    def is_write_tool(self, name):
        return bool(self.tools.get(name, {}).get("write"))

    def call(self, name, args):
        self.calls.append((name, dict(args)))
        return FakeResult(success=True, content="ok:" + name, tool_name=name,
                          args=dict(args), data={"echo": dict(args)},
                          latency_ms=1.0, error="", truncated=False,
                          next_step="")


def make_registry():
    reg = FakeRegistry()
    reg.register("read_spectrum")
    reg.register("get_status")
    reg.register("set_frequency", write=True)
    return reg


# ---------- 1. 跨语言 / 跨事实源对拍 ----------

def test_contract_matches_product_js():
    with open(CORE_JS, encoding="utf-8") as f:
        js = f.read()
    m = re.search(r"MB\.CONTRACT\s*=\s*Object\.freeze\(\[(.*?)\]\)", js, re.S)
    assert m, "00-core.js 里找不到 MB.CONTRACT"
    names = tuple(re.findall(r'"([A-Za-z][A-Za-z0-9]*)"', m.group(1)))
    assert names == tuple(CONTRACT_METHODS), (
        f"产品 JS 契约 {names} 与 Python {tuple(CONTRACT_METHODS)} 不一致")


def test_gate_fields_match_repo_facts():
    if not os.path.isfile(FACTS_JSON):
        pytest.skip("repo-facts.json 缺席（未生成），诚实跳过事实源对拍")
    with open(FACTS_JSON, encoding="utf-8") as f:
        facts = json.load(f)
    gm = facts["gates"]["mcp"]
    assert list(TOKEN_FIELDS) == gm["tokenFields"]
    assert list(SELF_REPORT_FIELDS) == gm["selfReportFields"]
    assert gm["selfReportRejected"] is True
    assert facts["gates"]["frontEndPolicy"]["mayNotIssueToken"] is True
    assert facts["gates"]["controlHub"]["stateVar"] == "writeEnabled_"


def test_manifest_shape_and_honesty():
    man = WebBridge(make_registry()).manifest()
    assert man["contractMethods"] == list(CONTRACT_METHODS)
    assert man["gate"]["mcp"]["frontEndMayIssueToken"] is False
    assert man["gate"]["gatedIsNotFailure"] is True
    assert man["honesty"]["orbitDeterminationNotAFeature"] is True
    assert man["honesty"]["noFakeData"] is True
    assert man["notVerified"], "not_verified 不允许为空"


def test_no_orbit_determination_entry():
    man = WebBridge(make_registry()).manifest()
    # 只检查「入口」：契约方法 / 桥形状 / 方法映射里不能出现定轨能力。
    # honesty.orbitDeterminationNotAFeature 是红线声明本身，不是功能入口。
    entry_blob = json.dumps({
        "contractMethods": man["contractMethods"],
        "methodMap": man["methodMap"],
        "bridgeShape": man["bridgeShape"],
    }, ensure_ascii=False).lower()
    assert not any(k in entry_blob for k in ("orbit", "定轨"))
    assert all("orbit" not in m.lower() for m in CONTRACT_METHODS)


# ---------- 2. 读工具正常透传 ----------

def test_read_tool_forwarded_to_registry():
    reg = make_registry()
    b = WebBridge(reg)
    assert b.is_live() is True
    assert b.source_name() == "tool_registry.python"
    out = b.invoke_tool("read_spectrum", {"nfft": 1024})
    assert out["ok"] is True
    assert out["data"] == {"echo": {"nfft": 1024}}
    assert out.get("gated") is None
    assert reg.calls == [("read_spectrum", {"nfft": 1024})]


def test_capabilities_are_real_registered_tools():
    reg = make_registry()
    caps = WebBridge(reg).get_capabilities()
    assert {c["id"] for c in caps} == {"read_spectrum", "get_status", "set_frequency"}
    by_id = {c["id"]: c for c in caps}
    assert by_id["set_frequency"]["write"] is True
    assert by_id["read_spectrum"]["write"] is False


# ---------- 3. 写工具门（核心红线） ----------

def test_write_without_token_is_gated_and_never_calls_registry():
    reg = make_registry()
    b = WebBridge(reg)
    out = b.invoke_tool("set_frequency", {"frequency_hz": 145_000_000})
    assert out["ok"] is False
    assert out["gated"] is True
    assert out["notFailure"] is True
    assert out["error"] == "manual_gate_required"
    assert reg.calls == [], "写工具绝不能绕过门直接打到 registry.call"


def test_write_with_token_but_no_channel_is_still_gated():
    reg = make_registry()
    b = WebBridge(reg)
    out = b.invoke_tool("set_frequency", {"frequency_hz": 1, "confirm_token": "T"})
    assert out["gated"] is True
    assert out["error"] == "manual_gate_unavailable"
    assert out["confirmChannelWired"] is False
    assert reg.calls == [], "没有带门的内核层，本桥不得自行放行"


def test_write_with_channel_delegates_and_skips_registry():
    reg = make_registry()
    seen = {}

    def channel(name, args):
        seen["name"], seen["args"] = name, dict(args)
        return {"ok": True, "data": {"applied": True}, "content": "ok"}

    b = WebBridge(reg, confirm_channel=channel)
    out = b.invoke_tool("set_frequency", {"frequency_hz": 7, "tx_token": "T"})
    assert out["ok"] is True
    assert out["data"] == {"applied": True}
    assert seen == {"name": "set_frequency", "args": {"frequency_hz": 7, "tx_token": "T"}}
    assert reg.calls == [], "写工具必须走带门的通道，不走裸 registry.call"


def test_self_reported_arm_manual_is_stripped_and_rejected():
    reg = make_registry()
    b = WebBridge(reg)
    out = b.invoke_tool("set_frequency",
                        {"frequency_hz": 1, "arm_manual": True})
    assert out["gated"] is True
    assert out["selfReportRejected"] == ["arm_manual"]
    assert reg.calls == []

    # 读工具也不该把自报标记送进内核
    out2 = b.invoke_tool("get_status", {"arm_manual": True})
    assert out2["ok"] is True
    assert out2["selfReportRejected"] == ["arm_manual"]
    assert reg.calls == [("get_status", {})]


def test_confirm_channel_gated_reply_is_preserved():
    reg = make_registry()

    def channel(name, args):
        return {"ok": False, "gated": True, "code": "manual_gate_required",
                "error": "token rejected"}

    out = WebBridge(reg, confirm_channel=channel).invoke_tool(
        "set_frequency", {"frequency_hz": 1, "confirm_token": "BAD"})
    assert out["ok"] is False
    assert out["gated"] is True
    assert out["notFailure"] is True
    assert out["error"] == "manual_gate_required"
    assert reg.calls == []


def test_tool_failure_is_not_reported_as_gated():
    class FailingRegistry(FakeRegistry):
        def call(self, name, args):
            self.calls.append((name, dict(args)))
            return FakeResult(success=False, content="device missing",
                              tool_name=name, error="tool_unavailable",
                              data={}, next_step="check device")

    reg = FailingRegistry()
    reg.register("read_spectrum")
    out = WebBridge(reg).invoke_tool("read_spectrum", {})
    assert out["ok"] is False
    assert out["error"] == "tool_unavailable"
    assert "gated" not in out
    assert "notFailure" not in out
    assert out["nextStep"] == "check device"


# ---------- 4. 诚实空态 ----------

def test_offline_bridge_is_honest():
    b = WebBridge(None)
    assert b.is_live() is False
    assert b.source_name() == "bridge.offline"
    assert b.list_satellites() == []
    assert b.get_tle("25544") is None
    assert b.get_passes("25544") == []
    assert b.get_capabilities() == []
    assert b.read_spectrum() is None
    out = b.invoke_tool("read_spectrum", {})
    assert out["ok"] is False
    assert out["error"] == "not_connected"
    assert out["offline"] is True


def test_untrusted_site_blocks_passes_even_with_provider():
    b = WebBridge(make_registry(), site_trusted=False,
                  pass_provider=lambda n: [{"pass": 1}])
    assert b.get_passes("25544") == []
    trusted = WebBridge(make_registry(), site_trusted=True,
                        pass_provider=lambda n: [{"pass": 1}])
    assert trusted.get_passes("25544") == [{"pass": 1}]
    assert trusted.manifest()["honesty"]["siteUntrustedBlocksPasses"] is False


def test_providers_are_optional_and_never_synthesised():
    b = WebBridge(make_registry())
    assert b.list_satellites() == []
    assert b.get_tle("25544") is None
    assert b.read_spectrum() is None
    assert envelope("x")["ok"] is False and envelope("x")["data"] is None


def test_as_bridge_dict_matches_product_expectations():
    b = WebBridge(make_registry(), satellites=[{"name": "demo"}])
    d = b.as_bridge_dict()
    assert callable(d["invoke"])
    assert d["satellites"] == [{"name": "demo"}]
    assert callable(d["getTle"]) and callable(d["getPasses"])
    assert callable(d["readSpectrum"]) and callable(d["getCapabilities"])
    assert d["invoke"]("read_spectrum", {})["ok"] is True
    assert json.loads(b.manifest_json())["globalName"] == "mbdsdrBridge"
