# SPDX-License-Identifier: MIT
"""W64 反证：MCP 解码器注册表必须与 controller 的能力声明同源。

改前：MCP 工具面没有任何入口能回答「哪些解码器能流式、哪些只能文件级」，
`start_decoder` 的描述是手写字符串（"仅 adsb/rds"），与
`controller.STREAMABLE_DECODERS` 是两份副本；controller 增删解码器时
MCP 文案会静默过期，调用方只能靠报错猜能力。

改后：新增只读工具 `list_decoders`，其返回值与 `start_decoder` 的描述
都直接取自 controller 的常量——把常量改成假值后两者必须同步变化，
若 MCP 侧再复制一份字面量，本用例即红。
"""
from __future__ import annotations

import pytest

from mbdsdr_ai.core import (
    FILE_ONLY_DECODERS,
    STREAMABLE_DECODERS,
    SUPPORTED_DECODERS,
)
from mbdsdr_ai.core import controller as controller_mod
from mbdsdr_ai.core.mcp_server import MCPToolSet, _build_tool_table


class _AllowAllPolicy:
    def action_for(self, name, is_write, domain=""):
        return "allow"


@pytest.fixture
def ts(tmp_path):
    """独立 profile 的控制器：测试不得读写真实用户配置。"""
    from mbdsdr_ai.core import SDRController

    ctrl = SDRController(config_dir=str(tmp_path))
    yield MCPToolSet(ctrl, policy=_AllowAllPolicy())
    try:
        ctrl.shutdown()
    except Exception:
        pass


def _start_decoder_desc(table):
    return next(t for t in table if t["name"] == "start_decoder")["description"]


def test_list_decoders_tool_exists_and_is_read_only():
    """改前该工具不存在，call_tool 返回 unknown_tool。"""
    table = _build_tool_table()
    names = {t["name"] for t in table}
    assert "list_decoders" in names, f"注册表缺能力枚举入口: {sorted(names)[:10]}…"
    tool = next(t for t in table if t["name"] == "list_decoders")
    assert tool["write"] is False
    assert tool["inputSchema"]["type"] == "object"


def test_list_decoders_matches_controller_partition(ts):
    out = ts.call_tool("list_decoders", {})
    assert out.get("error") != "unknown_tool", out
    assert out["streamable"] == list(STREAMABLE_DECODERS)
    assert out["file_only"] == list(FILE_ONLY_DECODERS)
    assert set(out["streamable"]) | set(out["file_only"]) == set(SUPPORTED_DECODERS)
    assert set(out["streamable"]).isdisjoint(out["file_only"])
    assert "--decode-file" in out["file_entry"]


def test_registry_text_has_single_source(monkeypatch, tmp_path):
    """把 controller 声明改成假值，MCP 出口必须同步——证明不是字面量副本。"""
    from mbdsdr_ai.core import SDRController

    monkeypatch.setattr(controller_mod, "STREAMABLE_DECODERS", ("adsb",))
    monkeypatch.setattr(
        controller_mod, "FILE_ONLY_DECODERS", ("rds", "pocsag"))
    monkeypatch.setattr(
        controller_mod, "SUPPORTED_DECODERS", ("adsb", "rds", "pocsag"))

    ctrl = SDRController(config_dir=str(tmp_path))
    try:
        ts = MCPToolSet(ctrl, policy=_AllowAllPolicy())
        out = ts.call_tool("list_decoders", {})
    finally:
        ctrl.shutdown()
    assert out["streamable"] == ["adsb"]
    assert out["file_only"] == ["rds", "pocsag"]

    desc = _start_decoder_desc(_build_tool_table())
    assert "仅 adsb" in desc
    assert "rds, pocsag" in desc
    assert "pocsag" in desc


@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_start_decoder_description_names_every_file_only_decoder(dt):
    """每个文件级解码器都必须出现在描述里，调用方不必靠试错。"""
    table = _build_tool_table()
    assert dt in _start_decoder_desc(table)
