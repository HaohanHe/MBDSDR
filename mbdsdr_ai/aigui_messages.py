# SPDX-License-Identifier: MIT
"""MBDSDR AI 内核 - A2UI agent 侧消息构造与校验（协议正确的一侧）

背景（docs/learn/phase63/agent-ui-protocol-basis.md、a2ui-v1-delta.md）：
A2UI 是 agent→renderer 的声明式 UI 内容协议（a2ui.org v1.0 Candidate）。
MBDSDR 的 renderer 是 C++ Qt 工作台（cpp/src/ui/component_registry.cpp 编译期
白名单 + workspace_controller.cpp 布局执行）——那是布局模型的**唯一事实源**，
本模块刻意**不复制布局模型、不持有 renderer 状态**。

本模块只做 agent 侧的本职：把 agent 的意图构造成**可下发给 renderer 的
A2UI v1.0 信封**，并在发送前按白名单目录做引用校验（未知 id 在构造期就
显式列出，与 renderer 端"未知 id→placeholder"的诚实语义对齐，让 agent
在本地就能发现自己引用了不存在的组件，而不是等 renderer 兜底）。

红线：
- 纯函数，无 Qt 依赖，无网络，无副作用；
- 校验失败返回结构化错误（unknown_component_ids），绝不静默丢弃节点；
- 不解释 props 的视觉语义——那是 renderer 的职责。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# A2UI v1.0 envelope version marker（官方 createSurface 消息示例即带 "version"；
# 见 a2ui.org specification/v1.0-a2ui/ Envelope message structure）。
A2UI_VERSION = "v1.0"

# 默认 surfaceId：MBDSDR 三栏工作台唯一 surface。
DEFAULT_SURFACE_ID = "workspace"

# ---------------------------------------------------------------------------
# 组件白名单目录（从 C++ component_registry.cpp:17-69 逐条派生，23 项）
# 事实源在 C++；本表由 mbdsdr_ai/tests/test_aigui_messages.py 的
# test_catalog_matches_cpp_registry 钉住（解析 C++ 源码逐项比对），
# C++ 侧增删组件而此处未同步时测试必须红。
# 结构：id -> (category, capabilities, min_size_px)
# ---------------------------------------------------------------------------
COMPONENT_CATALOG: Dict[str, Tuple[str, Tuple[str, ...], int]] = {
    # ---- 结构容器（当前三栏布局）----
    "splitter": ("container", ("resize",), 120),
    "left_rail": ("rail", ("resize",), 120),
    "center_spectrum": ("view", ("resize",), 320),
    "right_tabs": ("tab_container", ("resize", "select", "show", "hide"), 160),
    "status_bar": ("status", (), 0),
    # ---- 右侧 QTabWidget 页面（label 与 main_window addTab 一致）----
    "tab:CW": ("tab", ("select", "show", "hide"), 0),
    "tab:ADS-B": ("tab", ("select", "show", "hide"), 0),
    "tab:FT8": ("tab", ("select", "show", "hide"), 0),
    "tab:寻呼": ("tab", ("select", "show", "hide"), 0),
    "tab:数据": ("tab", ("select", "show", "hide"), 0),
    "tab:m17": ("tab", ("select", "show", "hide"), 0),
    "tab:VOR": ("tab", ("select", "show", "hide"), 0),
    "tab:星座": ("tab", ("select", "show", "hide"), 0),
    "tab:天空": ("tab", ("select", "show", "hide"), 0),
    "tab:扫描/书签": ("tab", ("select", "show", "hide"), 0),
    "tab:录制库": ("tab", ("select", "show", "hide"), 0),
    "tab:AI 助手": ("tab", ("select", "show", "hide"), 0),
    "tab:电台": ("tab", ("select", "show", "hide"), 0),
    "tab:VNA": ("tab", ("select", "show", "hide"), 0),
    # ---- 中心区次级视图（真实 widget，但非右侧 tab）----
    "center:频谱": ("view", (), 0),
    "center:世界": ("view", (), 0),
    "center:气象": ("view", (), 0),
    "center:时空视图": ("view", (), 0),
}

CATALOG_SIZE = len(COMPONENT_CATALOG)  # 23

# MBDSDR 布局默认值（cpp/src/ui/workspace_layout.h:74-76 kDefaultSize*、
# :82 splitter_stretch {1,3,1}）。
DEFAULT_SPLITTER_SIZES = (280, 800, 280)
DEFAULT_SPLITTER_STRETCH = (1, 3, 1)


def is_registered(component_id: str) -> bool:
    """组件 id 是否在编译期白名单目录内。"""
    return component_id in COMPONENT_CATALOG


def catalog_entries() -> List[Dict[str, Any]]:
    """目录条目列表（agent 编排前查询用，形态对齐 C++ componentCatalog()）。"""
    return [
        {
            "id": cid,
            "category": cat,
            "capabilities": list(caps),
            "min_size": min_size,
        }
        for cid, (cat, caps, min_size) in COMPONENT_CATALOG.items()
    ]


def collect_component_ids(components: Any) -> Tuple[List[str], List[str]]:
    """从扁平邻接表里取出全部 id 与 children 引用（去重、保序）。

    返回 (all_ids, child_refs)：child_refs 是 children 数组里引用的 id
    （官方 ChildList array 形态），用于校验引用可解析。
    非列表/非对象输入诚实返回空——不抛异常，调用方按校验错误处理。
    """
    seen: List[str] = []
    kids: List[str] = []

    def _mark(lst: List[str], v: Any) -> None:
        if isinstance(v, str) and v and v not in lst:
            lst.append(v)

    if not isinstance(components, list):
        return seen, kids
    for node in components:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        _mark(seen, nid)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        # right_tabs.props.current 是 renderer 认的第三个引用槽（除 id 与
        # children 之外）：cpp/src/ui/workspace_layout.cpp 在该 id 未注册时
        # 与 children 引用一样 markPlaceholder，故此处同样要显形。
        if nid == "right_tabs" and isinstance(props.get("current"), str):
            _mark(kids, props.get("current"))
        children = node.get("children")
        if isinstance(children, list):
            for c in children:
                _mark(kids, c)
    return seen, kids


def unknown_component_ids(components: Any) -> List[str]:
    """列出未注册的组件 id 与不可解析的 children 引用（去重、保序）。

    这是 agent 侧的第一道诚实关：renderer 会把未知 id 渲染成 placeholder，
    但 agent 在构造期就应当知道自己引用了什么。
    """
    ids, kids = collect_component_ids(components)
    bad = [i for i in ids if not is_registered(i)]
    bad += [k for k in kids if not is_registered(k) and k not in bad]
    return bad


def _node(component_id: str, component_type: str, **props: Any) -> Dict[str, Any]:
    """构造一个 A2UI 组件节点（扁平邻接表单节点）。"""
    node: Dict[str, Any] = {"id": component_id, "component": component_type}
    node.update(props)
    return node


def message_create_surface(
    surface_id: str = DEFAULT_SURFACE_ID,
    components: Optional[List[Dict[str, Any]]] = None,
    data_model: Optional[Dict[str, Any]] = None,
    send_data_model: bool = False,
) -> Dict[str, Any]:
    """构造官方 createSurface 信封（A2UI v1.0：可携带初始组件树与数据模型）。"""
    body: Dict[str, Any] = {"surfaceId": surface_id}
    if send_data_model:
        body["sendDataModel"] = True
    if components is not None:
        body["components"] = components
    if data_model is not None:
        body["dataModel"] = data_model
    return {"version": A2UI_VERSION, "createSurface": body}


def message_update_components(
    surface_id: str = DEFAULT_SURFACE_ID,
    components: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造官方 updateComponents 信封（扁平邻接表增量更新）。"""
    return {
        "version": A2UI_VERSION,
        "updateComponents": {
            "surfaceId": surface_id,
            "components": components if components is not None else [],
        },
    }


def message_update_data_model(
    value: Any,
    surface_id: str = DEFAULT_SURFACE_ID,
    path: str = "/",
) -> Dict[str, Any]:
    """构造官方 updateDataModel 信封（JSON Pointer path；value=None 即删键）。"""
    body: Dict[str, Any] = {"surfaceId": surface_id, "value": value}
    if path and path != "/":
        body["path"] = path
    return {"version": A2UI_VERSION, "updateDataModel": body}


def message_delete_surface(surface_id: str = DEFAULT_SURFACE_ID) -> Dict[str, Any]:
    """构造官方 deleteSurface 信封。"""
    return {"version": A2UI_VERSION, "deleteSurface": {"surfaceId": surface_id}}


# ---------------------------------------------------------------------------
# MBDSDR P0 工具参数 -> A2UI 官方信封的适配（agent 侧翻译层）
# ---------------------------------------------------------------------------

def arrange_to_envelope(op: str, components: Any) -> Dict[str, Any]:
    """把 MBDSDR arrange_workspace 的 (op, components) 参数翻译成 A2UI 信封。

    op 语义（对齐 C++ tool_schema.cpp 第 10 域与 workspace_controller.cpp）：
      createSurface     -> 官方 createSurface（整面重建，可带 components）
      updateComponents  -> 官方 updateComponents（默认，含参数缺失时）
      deleteSurface     -> 官方 deleteSurface（回默认布局）
    未知 op 与 C++ 的 absent->updateComponents 语义对齐，按
    updateComponents 处理——调用方应先用 validate_components 自行把关，
    本函数不静默吞错误、也不为坏 op 伪造 create/delete 语义。
    """
    op = (op or "updateComponents").strip()
    if op == "createSurface":
        return message_create_surface(components=components if isinstance(components, list) else None)
    if op == "deleteSurface":
        return message_delete_surface()
    return message_update_components(components=components)


def validate_components(components: Any) -> Dict[str, Any]:
    """对一组待下发组件做 agent 侧校验，返回结构化结果。

    {ok, unknown_ids, node_count, child_refs}。unknown_ids 非空时 renderer
    会把它们渲染成 placeholder——agent 应在发送前自行决定是否修正。
    components 不是数组时 ok=False 且显式带 error（不假装空列表合法）。
    """
    if not isinstance(components, list):
        return {
            "ok": False,
            "unknown_ids": [],
            "node_count": 0,
            "child_refs": 0,
            "error": "components 必须是数组（flat adjacency list）",
        }
    ids, kids = collect_component_ids(components)
    bad = unknown_component_ids(components)
    return {
        "ok": not bad,
        "unknown_ids": bad,
        "node_count": len(ids),
        "child_refs": len(kids),
    }
