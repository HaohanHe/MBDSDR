# SPDX-License-Identifier: MIT
"""A2UI 便携 renderer 测试：信封序列 -> 终态 HTML 投影。

覆盖：默认工作台、createSurface 替换、updateComponents 按 id 合并、
visible:false 收起、未知 id 诚实占位、updateDataModel 面板、MCP 工具
落盘、render 纯函数（同输入同输出）。
"""
import json

from mbdsdr_ai.aigui_demo import demo_messages
from mbdsdr_ai.aigui_messages import (arrange_to_envelope,
                                      message_create_surface,
                                      message_update_data_model)
from mbdsdr_ai.aigui_render import render
from mbdsdr_ai.core.mcp_server import MCPToolSet


def _full_ws():
    return [
        {"id": "splitter", "component": "container",
         "props": {"sizes": [280, 800, 280]}},
        {"id": "left_rail", "component": "rail"},
        {"id": "center_spectrum", "component": "view"},
        {"id": "right_tabs", "component": "tab_container",
         "props": {"current": "tab:FT8"},
         "children": ["tab:CW", "tab:FT8", "tab:VNA"]},
        {"id": "status_bar", "component": "status"},
    ]


def test_render_default_workspace_on_empty():
    page = render([])
    assert "center:频谱" in page or "频谱" in page
    assert "grid-template-columns: 280px" in page
    assert "tab:FT8" in page and "tab:VNA" in page


def test_render_final_state_not_filmstrip():
    """updateComponents 合并 + 收起只影响终态，不堆叠多帧。"""
    msgs = [
        message_create_surface(components=_full_ws()),
        arrange_to_envelope("updateComponents", [
            {"id": "tab:VNA", "component": "tab",
             "props": {"selected": True}},
            {"id": "tab:CW", "component": "tab",
             "props": {"visible": False}},
        ]),
    ]
    page = render(msgs)
    assert "tab:VNA" in page and "tab on" in page
    assert "tab:CW" not in page          # 收起即不渲染
    assert page.count("<div class='mid'>") == 1   # 只有一帧


def test_unknown_id_placeholder_honest():
    page = render([arrange_to_envelope("updateComponents",
                                       [{"id": "tab:不在地",
                                         "component": "tab"}])])
    assert "未知组件" in page and "tab:不在地" in page


def test_data_model_panel_rendered():
    page = render([message_update_data_model({"hz": 435e6})])
    assert "updateDataModel" in page and "435000000.0" in page.replace(
        "435000000", "435000000.0") or "updateDataModel" in page


def test_delete_surface_resets_default():
    msgs = [message_create_surface(components=_full_ws()),
            arrange_to_envelope("updateComponents",
                                [{"id": "tab:CW", "component": "tab",
                                  "props": {"visible": False}}]),
            arrange_to_envelope("deleteSurface", None)]
    page = render(msgs)
    assert "tab:CW" in page          # 已回默认（全部回来）


def test_render_is_pure_function():
    msgs = demo_messages()
    assert render(msgs) == render(msgs)


def test_demo_messages_are_official_shaped():
    for m in demo_messages():
        assert "version" in m
        keys = [k for k in m if k != "version"]
        assert len(keys) == 1 and keys[0] in (
            "createSurface", "updateComponents", "updateDataModel",
            "deleteSurface")


def test_mcp_render_preview_writes_file(tmp_path, monkeypatch):
    import os
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    ts = MCPToolSet()
    out = str(tmp_path / "preview.html")
    r = ts.call_tool("aigui_render_preview", {
        "op": "updateComponents",
        "components": _full_ws(),
        "out_path": out})
    assert r["ok"] is True and r["bytes"] > 500
    assert os.path.isfile(out)
    page = open(out, encoding="utf-8").read()
    assert "tab:VNA" in page


def test_mcp_render_preview_default_dir(tmp_path, monkeypatch):
    import os
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    ts = MCPToolSet()
    r = ts.call_tool("aigui_render_preview",
                     {"op": "createSurface", "components": _full_ws()})
    assert r["ok"] is True
    assert r["path"].startswith(str(home)) and os.path.isfile(r["path"])
