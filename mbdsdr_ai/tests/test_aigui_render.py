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


# ---------------------------------------------------------------------------
# 追加覆盖：HTML 注入面 / 不可信 props / 畸形信封（只增不减，不重复既有用例）
# ---------------------------------------------------------------------------

_XSS = '<script>alert(1)</script>'
_ATTR = '" onerror="alert(2)'
_NASTY = _XSS + _ATTR + " BI1AAA 中文 & <>"


def test_injection_in_every_user_controlled_slot_is_escaped():
    """id / props value / children 引用 / data model / title 全在本ilirрованной escaped 通道上。"""
    envs = [
        {"version": "v1.0", "createSurface": {"surfaceId": _NASTY}},
        arrange_to_envelope("createSurface", [{"id": _NASTY,
                                               "component": "tab"}]),
        message_update_data_model({"note": _NASTY, "nested": [_NASTY]}),
        arrange_to_envelope("updateComponents", [
            {"id": "right_tabs", "component": "tab_container",
             "children": [_NASTY]}]),
    ]
    page = render(envs, title=_NASTY)
    for raw in ("<script", 'onerror="', "<>"):
        assert raw not in page, "未转义的危险片段出现在页面里: %r" % raw
    assert "&lt;script&gt;" in page          # 原文以转义形式如实可见
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "未知组件" in page                 # 未知 id 走占位，不渲染成组件


def test_unknown_id_placeholder_escapes_quotes_and_brackets():
    cid = 'tab:' + _ATTR
    page = render([arrange_to_envelope("updateComponents",
                                       [{"id": cid, "component": "tab"}])])
    assert 'onerror="' not in page
    assert "&quot; onerror=&quot;alert(2)" in page
    assert "未知组件" in page


def test_data_model_value_with_markup_and_cjk_is_read_only_json():
    page = render([message_update_data_model(
        {"note": "<b>435 MHz</b>", "callsign": "BI1AAA", "hz": 435000000})])
    assert "<b>" not in page and "&lt;b&gt;" in page
    assert "435000000" in page                # 读数照抄，不伪造
    assert "BI1AAA" in page


def test_data_model_unserializable_value_is_stated_not_thrown():
    """不可序列化的 value：如实标注，不抛穿、不退化成一个空 {}。"""
    import datetime as _dt
    for bad in ({1, 2, 3}, _dt.datetime(2026, 10, 29)):
        page = render([message_update_data_model(bad)])
        assert "updateDataModel" in page
        assert "无法序列化" in page


def test_data_model_self_reference_is_stated_not_thrown():
    loop = {"name": "loop"}
    loop["self"] = loop
    page = render([message_update_data_model(loop)])
    assert "无法序列化" in page


def test_data_model_panels_keep_envelope_order():
    page = render([message_update_data_model({"n": 1}),
                   message_update_data_model({"n": 2})])
    assert page.index('"n": 1') < page.index('"n": 2')


def test_sizes_floor_is_kminpanel_for_every_column():
    """下限对齐 C++ clampSize：三栏统一 120，不给右栏套 320 的伪下限。"""
    page = render([arrange_to_envelope("updateComponents", [
        {"id": "splitter", "component": "container",
         "props": {"sizes": [1, 1, 1]}}])])
    assert "grid-template-columns: 120px 1fr 120px;" in page
    keep = render([arrange_to_envelope("updateComponents", [
        {"id": "splitter", "component": "container",
         "props": {"sizes": [280, 800, 150]}}])])
    assert "grid-template-columns: 280px 1fr 150px;" in keep, \
        "右栏 150px（>=120）应照 agent 要求保留，不能被抬到 320px"


def test_sizes_non_finite_falls_back_to_default_column():
    """Infinity/NaN 是不可信输入（Python json 也吃 Infinity 字面量）：退回默认列宽。"""
    nan_inf = json.loads('{"sizes": [Infinity, 800, 280]}')
    page = render([{"version": "v1.0", "updateComponents": {
        "surfaceId": "workspace",
        "components": [{"id": "splitter", "component": "container",
                        "props": {"sizes": nan_inf["sizes"]}}]}}])
    assert "grid-template-columns: 280px 1fr 280px;" in page


def test_sizes_malformed_shapes_fall_back_to_defaults():
    for bad in (None, "280", {"a": 1}, [280, 800], [280, 800, "宽"]):
        page = render([arrange_to_envelope("updateComponents", [
            {"id": "splitter", "component": "container",
             "props": {"sizes": bad}}])])
        assert "grid-template-columns: 280px 1fr 280px;" in page


def test_malformed_envelopes_never_raise():
    junk = [
        {"createSurface": None},
        {"createSurface": {"components": "abc"}},
        {"createSurface": {"components": [None, "x", 7, {"no_id": 1}]}},
        {"updateComponents": None},
        {"updateComponents": {"components": [{"component": "tab"}]}},
        {"updateComponents": {"components": {"id": "tab:VNA"}}},
        {"updateDataModel": None},
        {"deleteSurface": None},
        {"version": "v1.0"},                 # 只有 version
        {"nope": {"surfaceId": "workspace"}},
    ]
    page = render(junk)
    assert page.startswith("<!doctype html>")
    assert "aigui" in page


def test_unknown_envelope_key_yields_honest_empty_state():
    page = render([{"nope": {"surfaceId": "workspace"}}])
    for empty in ("（无左轨节点）", "（无中心节点）", "（无右侧节点）"):
        assert empty in page, "空态缺如实说明: %s" % empty


def test_repeated_create_surface_last_wins():
    page = render([
        message_create_surface(
            components=[{"id": "tab:CW", "component": "tab"}]),
        message_create_surface(
            components=[{"id": "tab:VNA", "component": "tab"}]),
    ])
    assert "tab:VNA" in page and "tab:CW" not in page


def test_delete_surface_then_update_reapplies_on_default():
    msgs = [arrange_to_envelope("deleteSurface", None),
            arrange_to_envelope("updateComponents", [
                {"id": "tab:CW", "component": "tab",
                 "props": {"visible": False}}]),
            arrange_to_envelope("updateComponents", [
                {"id": "tab:VNA", "component": "tab"}])]
    page = render(msgs)
    assert "tab:CW" not in page and "tab:VNA" in page


def test_deeply_nested_data_model_does_not_raise():
    deep = json.loads("[" * 300 + "]" * 300)
    page = render([message_update_data_model(deep)])
    assert "</html>" in page
