# SPDX-License-Identifier: MIT
"""能力账本的探测源（S2）：让"有/无/未知"来自查过，而不是模型自述。

修的是什么
----------
`sky_negotiation.CapabilityLedger` 以前的**唯一**写入方是
`sky_capability_report` 工具——模型把"用户说的"或"它以为的"写成
available/missing。于是"你有机/无机"是**转述**，不是探测。后果很具体：

* 模型会劝用户去买他其实已经插着的设备；
* 或者拒绝一个本来能做的动作（"你没有 SDR，所以不能调谐"）。

修法：`probe_capabilities()` 用真实探测结果写账本，只写三种结论：

    True  -> available（查到了）
    False -> missing（查过，没有）
    None  -> **不写**，保持 unknown（这个进程没有探测手段）

第三种是这层存在的全部理由。把"我没法查"写成 missing 是撒谎。

本文件覆盖
----------
1. 纯函数语义（三种输入各自的后果）
2. 探测失败 ≠ 没有（异常时保持 unknown）
3. agent 侧真实探测能写 sdr，且其余槽位诚实留 unknown
4. 缓存生效（不重复扫设备）
5. `sky_capability_state` 工具返回里带 probe 段
6. 接线守卫：`_state` 必须真的调探测（防回退成"纯自述"）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_negotiation import (  # noqa: E402
    CAPABILITY_SLOTS,
    CapabilityLedger,
    probe_capabilities,
)


class TestPureSemantics:
    def test_true_means_available(self):
        ledger = CapabilityLedger()
        ok, rejected = probe_capabilities(ledger, {"sdr": True})
        assert (ok, rejected) == (1, [])
        assert ledger.state("sdr") == "available"
        assert ledger.evidence["sdr"].endswith("found")

    def test_false_means_missing(self):
        ledger = CapabilityLedger()
        ok, _ = probe_capabilities(ledger, {"gimbal": False})
        assert ok == 1
        assert ledger.state("gimbal") == "missing"
        assert "not_found" in ledger.evidence["gimbal"]

    def test_none_keeps_unknown(self):
        """没有探测手段 ≠ 没有硬件。这是本层最重要的一条。"""
        ledger = CapabilityLedger()
        ok, _ = probe_capabilities(ledger, {"imu": None})
        assert ok == 0
        assert ledger.state("imu") == "unknown"
        assert "imu" not in ledger.evidence

    def test_none_does_not_overwrite_previous_truth(self):
        """探测不到不该把用户明确说过的"我有 IMU"擦掉。"""
        ledger = CapabilityLedger()
        ledger.update("imu", "available", "用户说手机有 IMU")
        probe_capabilities(ledger, {"imu": None})
        assert ledger.state("imu") == "available"
        assert ledger.evidence["imu"] == "用户说手机有 IMU"

    def test_unknown_slot_rejected_not_silently_ignored(self):
        """非法槽位要能被调用方看见（rejected），不能悄悄丢掉。"""
        ledger = CapabilityLedger()
        ok, rejected = probe_capabilities(ledger, {"teleporter": True})
        assert ok == 0
        assert rejected and "teleporter" in rejected[0]

    def test_mixed_batch(self):
        ledger = CapabilityLedger()
        ok, rejected = probe_capabilities(ledger, {
            "sdr": True, "gnss": False, "imu": None, "bogus": True})
        assert ok == 2
        assert ledger.state("sdr") == "available"
        assert ledger.state("gnss") == "missing"
        assert ledger.state("imu") == "unknown"
        assert rejected


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    return MBDSDRAgent(AgentConfig())


class TestAgentProbe:
    def test_probes_sdr_and_reports_it(self, agent):
        agent.probe_hardware_capabilities(force=True)
        # 本机是否插着 SDR 不确定，但**结论必须来自探测**：
        # 要么 available（枚举到了）、要么 missing（枚举过了没有），
        # 不许停在 unknown。
        assert agent.sky_ledger.state("sdr") in ("available", "missing")
        assert agent.sky_ledger.evidence["sdr"].startswith("auto_probe:")

    def test_untouched_slots_stay_unknown(self, agent):
        """桌面侧才有探测手段的槽位，agent 不许替它们下结论。"""
        agent.probe_hardware_capabilities(force=True)
        for slot in ("imu", "antenna"):
            assert agent.sky_ledger.state(slot) == "unknown", \
                f"{slot} 被 agent 越权写了结论；它没有探测手段"

    def test_probe_reports_what_it_did(self, agent):
        r = agent.probe_hardware_capabilities(force=True)
        assert "sdr" in r["probed"]
        assert r["written"] >= 1
        assert isinstance(r["rejected"], list)

    def test_cache_avoids_rescan(self, agent):
        agent.probe_hardware_capabilities(force=True)
        first_at = agent._capability_probe_at
        agent.probe_hardware_capabilities()  # 不带 force
        assert agent._capability_probe_at == first_at, "缓存没生效，又扫了一遍设备"

    def test_force_rescans(self, agent):
        agent.probe_hardware_capabilities(force=True)
        first_at = agent._capability_probe_at
        import time as _t

        _t.sleep(0.01)
        agent.probe_hardware_capabilities(force=True)
        assert agent._capability_probe_at > first_at


class TestStateToolIncludesProbe:
    def test_snapshot_carries_probe_section(self, agent):
        import json

        r = agent.tool_registry.call("sky_capability_state", {})
        payload = json.loads(r.content)
        assert "probe" in payload, \
            "状态工具没带探测结果——模型又只能看到自己上次写的东西"
        assert "probed" in payload["probe"]
        assert "skipped" in payload["probe"]


class TestWiringGuard:
    def test_state_handler_calls_probe(self):
        """缺陷 S2 的形态是"函数有了但没人调"，任何纯函数单测都会绿。"""
        import ast

        import mbdsdr_ai.agent as mod

        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_state")
        called = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
        assert "probe_hardware_capabilities" in called, \
            "状态工具没先探测——账本里又只有模型自述"

    def test_note_explains_skipped_not_absent(self):
        """提示语必须说清"skipped ≠ 没有"，否则模型会把未知读成缺硬件。"""
        import ast

        import mbdsdr_ai.agent as mod

        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_state")
        strings = {n.value for n in ast.walk(fn)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert any(("没有探测手段" in text) or ("不等于没有" in text)
                   for text in strings), \
            "提示语必须写明 skipped ≠ 没有（否则模型会把未知读成缺硬件）"
