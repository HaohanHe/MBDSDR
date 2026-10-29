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
- props：sizes=[L,M,R] 施加到三栏 CSS grid，非有限值退回默认列宽；min-width
  下限统一 kMinPanel=120（与 C++ workspace_layout.cpp clampSize 逐字对齐；
  中间列按 1fr 铺满，不用 X 高度 px）；right_tabs.props.current 决定右侧 tab
  选中项（未注册的 id 走"未知组件"占位，与 C++ 同处 markPlaceholder 对齐）；
- visible：与 C++ 同口径，顶层 ``{"visible": false}`` 与 ``props.visible``
  都认（顶层优先），缺省则视为未声明、按可见渲染。
- updateDataModel 的 value 渲染为只读 JSON 面板（无线索伪造读数；不可序列化
  的值如实标注"无法序列化"，不抛穿、不伪造一个空 JSON）。
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
    """把 props.sizes=[L,M,R] 转 CSS grid 列宽（含 kMinPanel 下限钳制）。

    下限对齐 C++ 事实源 ``workspace_layout.cpp`` 的 ``clampSize``：三栏统一
    ``kMinPanel=120``（不按列区别对待——C++ 没有 per-column floor）。
    props 是不可信输入：非有限值（Infinity/NaN，JSON 里 Python 会解析成 float）
    一律退回该列默认值，不抛异常。
    """
    if not isinstance(sizes, (list, tuple)) or len(sizes) != 3:
        return "grid-template-columns: %dpx 1fr %dpx;" % (
            DEFAULT_SPLITTER_SIZES[0], DEFAULT_SPLITTER_SIZES[2])
    out: List[str] = []
    for i, raw in enumerate(sizes):
        try:
            px = max(120, int(raw))  # kMinPanel = 120（与 C++ clampSize 同值）
        except (TypeError, ValueError, OverflowError):
            px = DEFAULT_SPLITTER_SIZES[i]
        out.append(f"{px}px")
    return "grid-template-columns: %s 1fr %s;" % (out[0], out[2])


def _visible_flag(node: Dict[str, Any], accept_flat: bool = True) -> Any:
    """取节点的 visible 标记（顺序与范围都对齐 C++ 事实源）。

    cpp/src/ui/workspace_layout.cpp 同时接受扁平 A2UI 形状
    ``{"id": "tab:数据", "visible": false}`` 和规范形状 ``props.visible``，
    但扁平形状只在 ``id.startsWith("tab:")`` 分支里被读取（其它容器在
    该子集里不带 per-node 布局载荷）——Python 投影必须与它同口径，否则
    同一条信封两侧渲染不一致。两者都没有 -> None（未声明，按可见处理）。
    """
    flat = node.get("visible")
    if accept_flat and isinstance(flat, bool):
        return flat
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    return props.get("visible")


def _node_html(node: Dict[str, Any], current_tab: str = "") -> str:
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
        # 选中态两路来源，均不可信输入 -> 只用于加 class，不进 HTML 文本：
        #   1) props.selected === true（显式单卡选中）；
        #   2) 与 right_tabs.props.current 同 id（C++ current_tab 语义）。
        on = props.get("selected") is True or (bool(current_tab)
                                               and cid == current_tab)
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
    ``current_tab`` 是 right_tabs.props.current 解析出的选中 id（C++
    workspace_layout.cpp 的 ``current_tab``），命中的 tab 渲染为选中态；
    空串表示 agent 未指定，不做任何选中。
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
            right.append(_node_html(n, current_tab=current_tab))
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
            if _visible_flag(entry) is False:
                continue  # updateComponents 收起：连 children 引用也不渲染
            if ref in COMPONENT_CATALOG:
                node = {"id": ref, "component": COMPONENT_CATALOG[ref][0]}
                if props:
                    node["props"] = dict(props)
                cat = COMPONENT_CATALOG[ref][0]
                if cat == "tab":
                    right.append(_node_html(node, current_tab=current_tab))
                else:
                    mid.append(_node_html(node))
            else:
                right.append(_placeholder(ref))
    return {"left": "".join(left) or '<div class="ph">（无左轨节点）</div>',
            "mid": "".join(mid) or '<div class="ph">（无中心节点）</div>',
            "right": "".join(right) or '<div class="ph">（无右侧节点）</div>'}


def _json_text(value: Any) -> str:
    """数据模型 value → 只读 JSON 文本；不可序列化时如实标注（不抛穿、不造假）。

    value 是 agent 侧不可信输入：自引用 dict 会触发 ValueError、datetime/set
    等会触发 TypeError——两种都按"这份数据模型没法投影成 JSON"如实写出，
    绝不退化成一个空 {} 冒充"成功渲染"。
    """
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as e:
        return "（数据模型无法序列化为 JSON：%s: %s）" % (type(e).__name__, e)


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
                % _esc(_json_text(body.get("value"))))
        elif "deleteSurface" in msg:
            nodes = {n["id"]: n for n in _default_workspace()}

    # right_tabs.props.current -> 选中 tab（C++ workspace_layout.cpp 的
    # current_tab；未注册的 id 在同一处被 markPlaceholder，这里同样诚实占位）。
    current_tab = ""
    rt = nodes.get("right_tabs")
    if isinstance(rt, dict):
        rt_props = rt.get("props") if isinstance(rt.get("props"), dict) else {}
        cur = rt_props.get("current")
        if isinstance(cur, str) and cur:
            current_tab = cur

    visible = []
    for n in nodes.values():
        cid = str(n.get("id", ""))
        if _visible_flag(n, accept_flat=cid.startswith("tab:")) is False:
            continue
        visible.append(n)
    parts = render_component_list(visible, current_tab=current_tab, state=nodes)
    if current_tab and current_tab not in COMPONENT_CATALOG:
        parts["right"] += _placeholder(current_tab)  # 未知 current：占位不编造
    body = _wrap(parts)
    return ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
            f"<body><div class='aigui'>" + body
            + "".join(data_panels) + "</div></body></html>")


def _wrap(parts: Dict[str, str]) -> str:
    return ("<div class='mid'>%s%s%s</div>"
            % (f"<div>{parts['left']}</div>",
               f"<div>{parts['mid']}</div>",
               f"<div>{parts['right']}</div>"))
