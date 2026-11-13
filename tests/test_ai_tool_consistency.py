# SPDX-License-Identifier: MIT
"""
工具 schema 一致性守卫（P7 收尾）
=================================

把 scripts/ai_tool_consistency.py 的扫描做成回归锁：

  1. 真实注册表（约 500 个工具）不得有结构性错误（fatal）。
  2. 扫描器能抓住人工构造的四类结构问题：
     空描述 / required 未声明 / minimum>maximum / 非法类型。
  3. 扫描器能报出 handler 读取未声明参数、声明但未读取（drift 线索）。
  4. 不误报：`**args` 整体转发、网关参数（arm_manual / confirm_token）、`_helper(args)`。

不联网、不调用任何工具。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

import ai_tool_consistency as atc  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


@pytest.fixture(scope="module")
def real_registry():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None  # 不发起网络请求
    try:
        agent = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original
    return agent.tool_registry


def _register(reg, name, description, properties, handler, required=None,
              category="test"):
    reg.register(
        name=name, description=description,
        parameters={"type": "object", "properties": properties,
                    **({"required": required} if required is not None else {})},
        handler=handler, category=category)


def test_real_registry_has_no_structural_errors(real_registry):
    report = atc.scan_registry(real_registry)
    assert report["total"] >= 400
    assert report["fatal_count"] == 0, report["fatal"]
    assert report["ok"] is True
    assert report["source_scanned"] >= 400
    # P11 集成体检：真实注册表不仅无结构错误，也不得出现 schema/handler 漂移
    # （声明未读取 / 读取未声明）。新增工具若带漂移，此守卫直接红。
    assert report["drift_count"] == 0, report["drift"]


def test_dict_result_with_ok_false_is_failed_at_source():
    """handler 回裸 dict 且只带 ok:false 时，产出端必须落 success:false。

    这是旧 is_failure_result 的 ①（success 缺席 + 正文 {"ok": false} → 失败）
    在「消费端拿不到 success」的真实形态上的兜底：源头把 ok 折算进 success，
    下游只认 success，不再扫正文。
    """
    reg = ToolRegistry()
    _register(reg, "gated_ptt", "手动模式受限的电台发话", {},
              lambda a: {"ok": False, "detail": "只收"})
    res = reg.call("gated_ptt", {})
    assert res.success is False
    assert res.to_dict()["success"] is False


def test_scanner_catches_structural_problems():
    reg = ToolRegistry()
    _register(reg, "empty_desc", "", {"x": {"type": "number"}}, lambda a: "ok")
    _register(reg, "bad_required", "缺 required 声明",
              {"x": {"type": "number"}}, lambda a: "ok", required=["y"])
    _register(reg, "bad_range", "最小大于最大",
              {"x": {"type": "number", "minimum": 10, "maximum": 1}},
              lambda a: "ok")
    _register(reg, "bad_type", "非法类型",
              {"x": {"type": ["number", "wat"]}}, lambda a: "ok")

    report = atc.scan_registry(reg)
    assert report["ok"] is False
    issue_tools = {item["tool"] for item in report["fatal"]}
    assert {"empty_desc", "bad_required", "bad_range", "bad_type"} <= issue_tools
    # enum 不是数组也算结构问题
    _register(reg, "bad_enum", "枚举不是数组", {"x": {"type": "string", "enum": "a"}},
              lambda a: "ok")
    assert "bad_enum" in {item["tool"] for item in atc.scan_registry(reg)["fatal"]}


def test_scanner_reports_undeclared_and_unread_args():
    reg = ToolRegistry()

    def drifted(args):
        return str(args.get("bogus"))

    _register(reg, "drifted", "handler 读取未声明参数",
              {"declared": {"type": "string"}}, drifted)
    report = atc.scan_registry(reg)
    issues = " | ".join(item["issue"] for item in report["drift"]
                        if item["tool"] == "drifted")
    assert "bogus" in issues
    assert "declared" in issues


def test_scanner_does_not_flag_wholesale_forwarding_or_gate_args():
    reg = ToolRegistry()

    def forwards(args):
        # 整体转发 + 网关参数：两者都不应报「声明但未读取」
        return str(args)

    _register(reg, "forwarder", "整体转发参数",
              {"a": {"type": "number"}, "arm_manual": {"type": "boolean"}},
              forwards)
    report = atc.scan_registry(reg)
    assert report["drift_count"] == 0


def test_scanner_treats_confirm_token_as_a_gate_arg():
    """confirm_token 由 ToolCallGate 统一校验，handler 不读它不算漂移。

    2026-11-09：真实注册表里 5 个写工具把整个 args 交给
    resolve_write_authorization("tool", args)，token 在门里被消费；
    扫描器只认 args 作首个形参的整体转发，于是误报「声明但未读取」。
    白名单化之后仍要守住：普通参数漏读照样要报。
    """
    reg = ToolRegistry()

    def gated(args):
        return str(args.get("iq"))

    _register(reg, "gated_write", "发射类写工具",
              {"iq": {"type": "string"},
               "confirm_token": {"type": "string"},
               "unused_plain": {"type": "string"}}, gated)
    report = atc.scan_registry(reg)
    issues = " | ".join(item["issue"] for item in report["drift"]
                        if item["tool"] == "gated_write")
    assert "unused_plain" in issues, "普通参数漏读必须仍被报出"
    assert "confirm_token" not in issues, "网关参数不该被当成漂移"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
