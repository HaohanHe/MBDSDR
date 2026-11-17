# SPDX-License-Identifier: MIT
"""超长工具结果：头尾保留 + 全文落盘（DSH spill 机制）。

此前进程内通道只截断不落盘：模型看到「中间 N 字符已裁剪」后无法追问，
只能把同一个工具再跑一遍（频谱/扫频尤其浪费）。MCP 面早已有 spill 信封，
本次把进程内通道补齐，并钉住三条底线：

1. 超长 → 头尾仍在、中间有裁剪标记、全文可在 spill 目录读回；
2. 落盘失败 → 仍给截断结果，但必须写明失败原因，不许静默；
3. 未超长 → 不产生任何 spill 文件（不为正常结果制造垃圾）。
"""
from __future__ import annotations

import json

import pytest

from mbdsdr_ai import tool_registry as tr_mod
from mbdsdr_ai.tool_registry import ToolRegistry
from mbdsdr_ai.tool_results import ToolResult

HEAD_MARK = "HEAD-MARK"
TAIL_MARK = "TAIL-MARK"


def _registry(**kwargs) -> ToolRegistry:
    reg = ToolRegistry(**kwargs)

    def _long(args):
        body = "x" * 500
        return ToolResult(success=True,
                          content=HEAD_MARK + body + TAIL_MARK)

    reg.register(
        name="long_read",
        description="返回超长文本的只读工具（测试用）",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_long,
        write=False,
    )
    return reg


def _call(reg: ToolRegistry) -> ToolResult:
    return reg.call_from_model({
        "id": "call_1",
        "type": "function",
        "function": {"name": "long_read", "arguments": json.dumps({})},
    })


def test_oversize_result_is_truncated_and_spilled(tmp_path, monkeypatch):
    monkeypatch.setattr(tr_mod, "TOOL_OUTPUT_SPILL_DIR", str(tmp_path))
    reg = _registry(tool_output_max_chars=100)

    out = _call(reg)

    assert out.success is True
    assert out.truncated is True
    assert HEAD_MARK in out.content and TAIL_MARK in out.content
    assert "中间" in out.content and "已裁剪" in out.content
    assert "locator=" in out.content

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    # 全文原样落盘：头、被裁掉的中间、尾都在
    assert files[0].read_text(encoding="utf-8") == HEAD_MARK + "x" * 500 + TAIL_MARK
    # locator 与文件名同源，可直接喂给 sdr_spill_read
    locator = out.content.split("locator=")[1].split("；")[0]
    assert files[0].stem == locator


def test_short_result_does_not_spill(tmp_path, monkeypatch):
    monkeypatch.setattr(tr_mod, "TOOL_OUTPUT_SPILL_DIR", str(tmp_path))
    reg = _registry(tool_output_max_chars=10_000)

    out = _call(reg)

    assert out.truncated is False
    assert "已落盘" not in out.content
    assert list(tmp_path.glob("*.txt")) == []


def test_spill_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(tr_mod, "TOOL_OUTPUT_SPILL_DIR",
                        str(blocker / "spill"))
    reg = _registry(tool_output_max_chars=100)

    out = _call(reg)

    # 落盘失败不等于调用失败：结果照给，但必须说清全文没存下来
    assert out.success is True
    assert out.truncated is True
    assert HEAD_MARK in out.content and TAIL_MARK in out.content
    assert "全文落盘失败" in out.content
    assert "locator=" not in out.content
