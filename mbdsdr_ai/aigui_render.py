# SPDX-License-Identifier: MIT
"""A2UI 便携参考 renderer：官方信封 -> 单文件 HTML 投影（纯函数，无状态库）。

定位（与 C++ renderer 的分工，重要）：
- **布局模型的唯一事实源是 C++ renderer**（cpp/src/ui/workspace_controller.cpp：
  splitter 尺寸、tab 可见性、placeholder 规则都在那里定）；
- 本模块是**无状态投影 renderer**：吃官方信封（createSurface /
  updateComponents / updateDataModel / deleteSurface），吐一份自包含 HTML
  （内联 CSS、白名单组件、未知 id 诚实占位）。它不持有布局状态、不与 C++ 争
  事实源——存在的意义是让「AI 编排 UI」在**没有 Qt 的机器上也能看、能测**
  （本机 cc1 损坏期间的可用见证），以及给 P2 Flutter renderer 之前一个
  协议层参照实现。

渲染契约（与 C++ renderer 对齐的可见语义）：
- 白名单 23 组件（来自 component_registry.cpp，跨语言钉扎见
  test_aigui_messages.py）：5 结构容器 + 14 tab + 4 center 视图；
- 未知 id -> 占位块（"未知组件"诚实标注，绝不编造内容）；
- props：sizes=[L,M,R] 施加到三栏 CSS grid，min-width 下限
  （kMinPanel=120 / center 320）；current 决定右侧 tab 选中项；
- updateDataModel 的 value 渲染为只读 JSON 面板（无线索伪造读数）。
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, Iterable, List, Optional

from .aigui_messages import (A2UI_VERSION, COMPONENT_CATALOG,
                             DEFAULT_SPLITTER_SIZES,
                             DEFAULT_SPLITTER_STRETCH)

_CSS = """
:root { color-scheme: light dark; }
body { font: 13px/1.5 system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 0; background: #0f1115; color: #e6e8eb; }
.a2ui { display: flex; flex-direction: column; min-height: 100vh; }
.a2ui .mid { display: grid; gap: 2px; background: #2a2e37; flex: 1; }
.a2ui .mid > div { background: #171a21; padding: 10px 12px; overflow: auto; }
.a2ui .tabs { display: flex; flex-wrap: wrap; gap: 4px 10px; align-content: start; }
.a2ui .tabs .tab { padding: 2px 8px; border-radius: 4px; color: #9aa3af; }
.a2ui .tabs .tab.on { background: #2f6fdd; color: #fff; }
.a2ui .status { background: #12141a; color: #9aa3af; padding: 4px 12px;
                border-top: 1px solid #2a2e37; }
.a2ui .ph { border: 1px dashed #4b5262; border-radius: 6px; color: #8b93a1;
            padding: 12px; margin: 6px 0; font-size: 12px; }
.a2ui .chart { border: 1px solid #2a2e37; border-radius: 6px; height: 220px;
               display: flex; align-items: center; justify-content: center;
               color: #5d6572; margin-top: 8px; }
.a2ui pre { background: #0b0d11; border: 1px solid #2a2e37; border-radius: 6px;
            padding: 8px; overflow: auto; max-height: 240px; }
.a2ui h3 { margin: 0 0 6px; font-size: 13px; color: #c8cdd6; }
.a2ui .rail a { display: block; color: #9aa3af; padding: 3px 0; text-decoration: none; }
"""


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def _placeholder(cid: str) -> str:
    """未知 id 占位（与 C++ renderer 的诚实空态语义对齐）。"""
    return (f'<div class="ph">未知组件 <code>{_esc(cid)}</code>——'
            f'不在 23 项白名单内（查 list_ui_components / '
            f'aigui_component_catalog），此处按占位渲染。</div>')


def _sizes_css(sizes: Any) -> str:
    """把 props.sizes=[L,M,R] 转 CSS grid 列宽（含 kMinPanel 下限钳制）。"""
    if not isinstance(sizes, (list, tuple)) or len(sizes) != 3:
        return "grid-template-columns: %dpx 1fr %dpx;" % (
            DEFAULT_SPLITTER_SIZES[0], DEFAULT_SPLITTER_SIZES[2])
    out: List[str] = []
    for i, raw in enumerate(sizes):
        minimum = 120 if i < 2 else 320
        try:
            px = int(max(minimum, int(raw)))
        except (TypeError, ValueError):
            px = DEFAULT_SPLITTER_SIZES[i]
        out.append(f"{px}px")
    return "grid-template-columns: %s 1fr %s;" % (out[0], out[2])


def _node_html(node: Dict[str, Any]) -> str:
    cid = node.get("id", "")
    if cid not in COMPONENT_CATALOG:
        return _placeholder(cid)
    cat, caps, _ = COMPONENT_CATALOG[cid]
    props = node.get("props") if isinstance(node.get("props"), dict) else {}

    if cat == "container":
        sizes = _sizes_css(props.get("sizes"))
        return f'<div class="grid" data-id="{_esc(cid)}" style="{sizes}"></div>'
    if cat == "rail":
        return (f'<div data-id="{_esc(cid)}"><h3>左轨</h3>'
                f'<a>SDR 状态</a><a>书签</a><a>扫描</a></div>')
    if cat == "view" and cid == "center_spectrum":
        return (f'<div data-id="{_esc(cid)}"><h3>频谱</h3>'
                f'<div class="chart">中心视图：center:频谱（占位）</div></div>')
    if cat == "tab_container":
        return (f'<div data-id="{_esc(cid)}"><h3>右侧页签</h3>'
                f'<div class="tabs"></div></div>')
    if cat == "status":
        return f'<div class="status" data-id="{_esc(cid)}">状态栏</div>'
    if cat == "tab":
        label = cid.split(":", 1)[1]
        on = props.get("selected") is True
        return (f'<div class="tab{" on" if on else ""}" '
                f'data-id="{_esc(cid)}">{_esc(label)}</div>')
    if cat == "view":  # center:世界/气象/时空视图
        label = cid.split(":", 1)[1]
        return (f'<div data-id="{_esc(cid)}"><h3>{_esc(label)}</h3>'
                f'<div class="chart">中心视图：{_esc(label)}（占位）</div></div>')
    return _placeholder(cid)  # pragma: no cover - 目录外兜底


def render_component_list(components: Any, current_tab: str = "",
                          state: Optional[Dict[str, Dict[str, Any]]] = None
                          ) -> Dict[str, str]:
    """把扁平邻接表投成三段 HTML（left / mid / right）。

    A2UI 扁平邻接语义：节点表 + children 引用。右侧 tab 通常仅以 children
    引用出现（right_tabs.children=[tab:CW, ...]），因此除顶层节点外，还要把
    children 引用补渲为对应组件（与 C++ renderer 的白名单渲染一致）。
    ``state`` 为合并后的节点表：children 引用同样受其 props 节制
    （visible=false 收起、selected 标记选中态）——与 updateComponents 的
    合并语义一致。
    """
    left: List[str] = []
    mid: List[str] = []
    right: List[str] = []
    nodes = [n for n in (components or []) if isinstance(n, dict)]
    seen: set = set()
    for n in nodes:
        cid = str(n.get("id", ""))
        seen.add(cid)
        cat = COMPONENT_CATALOG.get(cid, ("container",))[0] \
            if cid in COMPONENT_CATALOG else None
        if cat is None or cat == "container":
            mid.append(_node_html(n))
        elif cat == "rail":
            left.append(_node_html(n))
        elif cat == "tab_container":
            right.append(_node_html(n))
        elif cat == "tab":
            right.append(_node_html(n))
        elif cat == "status":
            pass
        else:
            mid.append(_node_html(n))
    # children 引用补渲（扁平邻接表的另一半；props 以 state 合并结果为准）
    for n in nodes:
        children = n.get("children")
        if not isinstance(children, list):
            continue
        for ref in children:
            if not isinstance(ref, str) or ref in seen:
                continue
            seen.add(ref)
            entry = (state or {}).get(ref) or {}
            props = entry.get("props") if isinstance(entry.get("props"),
                                                     dict) else {}
            if props.get("visible") is False:
                continue  # updateComponents 收起：连 children 引用也不渲染
            if ref in COMPONENT_CATALOG:
                node = {"id": ref, "component": COMPONENT_CATALOG[ref][0]}
                if props:
                    node["props"] = dict(props)
                cat = COMPONENT_CATALOG[ref][0]
                if cat == "tab":
                    right.append(_node_html(node))
                else:
                    mid.append(_node_html(node))
            else:
                right.append(_placeholder(ref))
    return {"left": "".join(left) or '<div class="ph">（无左轨节点）</div>',
            "mid": "".join(mid) or '<div class="ph">（无中心节点）</div>',
            "right": "".join(right) or '<div class="ph">（无右侧节点）</div>'}


def _default_workspace() -> List[Dict[str, Any]]:
    """默认三栏工作台（与 C++ 默认 280/800/280、stretch 1/3/1 对齐的投影）。"""
    return [
        {"id": "splitter", "component": "container",
         "props": {"sizes": list(DEFAULT_SPLITTER_SIZES),
                   "stretch": list(DEFAULT_SPLITTER_STRETCH)}},
        {"id": "left_rail", "component": "rail"},
        {"id": "center_spectrum", "component": "view"},
        {"id": "right_tabs", "component": "tab_container",
         "props": {"current": "tab:FT8"},
         "children": ["tab:CW", "tab:ADS-B", "tab:FT8", "tab:VNA"]},
    ]


def render(aigui_messages: Iterable[Dict[str, Any]],
           title: str = "A2UI preview") -> str:
    """把信封序列投影成终态工作台的单文件 HTML（函数内局部状态，无隐藏状态）。

    语义对齐 renderer 契约：createSurface 整面替换、updateComponents 按 id
    合并、props.visible=false 收起节点、deleteSurface 回默认工作台、
    updateDataModel 追加只读 JSON 面板。渲染只发生一次（终态），不是逐帧堆叠。
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    data_panels: List[str] = []
    messages = [m for m in (aigui_messages or []) if isinstance(m, dict)]
    if not messages:
        nodes = {n["id"]: n for n in _default_workspace()}
    for msg in messages:
        if "createSurface" in msg:
            body = msg["createSurface"] or {}
            nodes = {str(n.get("id")): n
                     for n in (body.get("components") or [])
                     if isinstance(n, dict)}
        elif "updateComponents" in msg:
            body = msg["updateComponents"] or {}
            for n in (body.get("components") or []):
                if isinstance(n, dict) and n.get("id"):
                    key = str(n["id"])
                    merged = dict(nodes.get(key, {}))
                    merged.update(n)
                    nodes[key] = merged
        elif "updateDataModel" in msg:
            body = msg["updateDataModel"] or {}
            data_panels.append(
                "<h3>updateDataModel</h3><pre>%s</pre>"
                % _esc(json.dumps(body.get("value"), ensure_ascii=False,
                                  indent=2)))
        elif "deleteSurface" in msg:
            nodes = {n["id"]: n for n in _default_workspace()}

    visible = [n for n in nodes.values()
               if not (isinstance(n.get("props"), dict)
                       and n["props"].get("visible") is False)]
    parts = render_component_list(visible, state=nodes)
    body = _wrap(parts)
    return ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
            f"<body><div class='aigui'>" + body.replace("class='mid'",
                                                        "class='mid'", 1)
            + "".join(data_panels) + "</div></body></html>")


def _wrap(parts: Dict[str, str]) -> str:
    return ("<div class='mid'>%s%s%s</div>"
            % (f"<div>{parts['left']}</div>",
               f"<div>{parts['mid']}</div>",
               f"<div>{parts['right']}</div>"))
