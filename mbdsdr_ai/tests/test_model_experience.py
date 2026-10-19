# SPDX-License-Identifier: MIT
"""
模型体验层：ToolResult.next_step（DSH nextStep 模式）+ 框架错误路径恢复指引

设计来源：deepseek-harness 工具执行管线——失败/部分成功时结果携带
nextStep（"该做什么或告诉用户什么"），模型不必从错误码猜恢复路径。
本文件钉死：

  - ToolResult.next_step 默认空、to_dict 往返不丢；
  - 四条框架错误路径（tool_not_found / tool_unavailable /
    missing_required_params / exception）均自动填充 next_step；
  - handler 返回 dict 时 next_step 键透传；
  - agent 侧 _tool_result_to_model_content 把 next_step 注入模型可见文本，
    无 next_step 时不加噪音。

运行：python3 -m pytest mbdsdr_ai/tests/test_model_experience.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402
from mbdsdr_ai.agent import _tool_result_to_model_content  # noqa: E402


# ---------------------------------------------------------------- ToolResult 字段

def test_next_step_defaults_empty():
    r = ToolResult(success=True, content="ok")
    assert r.next_step == ""


def test_to_dict_roundtrip_next_step():
    r = ToolResult(success=False, content="boom", next_step="先连接设备")
    d = r.to_dict()
    assert d["next_step"] == "先连接设备"
    # 既有字段不受影响
    assert d["success"] is False and d["content"] == "boom"


# ---------------------------------------------------------------- 框架错误路径

def _reg_with(tool_name="t", **kwargs):
    reg = ToolRegistry()
    reg.register(name=tool_name, description="d", parameters=kwargs.pop(
        "parameters", {"type": "object", "properties": {}}),
        handler=kwargs.pop("handler", lambda a: ToolResult(success=True, content="ok")),
        **kwargs)
    return reg


def test_tool_not_found_carries_next_step():
    reg = ToolRegistry()
    r = reg.call("no_such_tool", {})
    assert r.success is False
    assert r.error == "tool_not_found"
    assert r.next_step and "list_tools" in r.next_step


def test_tool_unavailable_carries_next_step():
    reg = ToolRegistry()
    reg.register(name="t", description="d",
                 parameters={"type": "object", "properties": {}},
                 handler=lambda a: ToolResult(success=True, content="ok"),
                 available=False)
    r = reg.call("t", {})
    assert r.error == "tool_unavailable"
    assert r.next_step and "连接" in r.next_step


def test_missing_required_params_carries_next_step():
    reg = ToolRegistry()
    reg.register(name="t", description="d",
                 parameters={"type": "object",
                             "properties": {"frequency_hz": {"type": "number"}},
                             "required": ["frequency_hz"]},
                 handler=lambda a: ToolResult(success=True, content="ok"))
    r = reg.call("t", {})
    assert r.error == "missing_required_params"
    assert r.next_step and "补齐" in r.next_step


def test_exception_carries_next_step():
    def boom(a):
        raise ValueError("bad arg")

    reg = ToolRegistry()
    reg.register(name="t", description="d",
                 parameters={"type": "object", "properties": {}}, handler=boom)
    r = reg.call("t", {})
    assert r.success is False
    assert r.error.startswith("exception:")
    assert r.next_step and "重试" in r.next_step


def test_dict_result_next_step_passthrough():
    reg = ToolRegistry()
    reg.register(name="t", description="d",
                 parameters={"type": "object", "properties": {}},
                 handler=lambda a: {"success": True, "content": "done",
                                    "next_step": "把结果写入日志"})
    r = reg.call("t", {})
    assert r.success is True
    assert r.next_step == "把结果写入日志"
    assert r.data["next_step"] == "把结果写入日志"


# ---------------------------------------------------------------- 模型可见注入

def test_model_content_appends_next_step():
    r = ToolResult(success=False, content="设备未连接", next_step="先调用 sdr_connect")
    out = _tool_result_to_model_content(r)
    assert out.startswith("设备未连接")
    assert "[下一步建议] 先调用 sdr_connect" in out


def test_model_content_clean_without_next_step():
    r = ToolResult(success=True, content="plain result")
    out = _tool_result_to_model_content(r)
    assert out == "plain result"
    assert "下一步建议" not in out


def test_model_content_blank_next_step_ignored():
    r = ToolResult(success=True, content="x", next_step="   ")
    assert _tool_result_to_model_content(r) == "x"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
