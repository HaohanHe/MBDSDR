# SPDX-License-Identifier: MIT
"""
桌面 AI ↔ 硬件 MCP 编排纯逻辑（Qt 无关，可单测）
================================================
支撑「桌面 AI → 硬件工具」编排链路的三块纯逻辑：

  * 端点解析：显式参数 > 环境变量（MBDSDR_MCP_HOST / MBDSDR_MCP_PORT）>
    tokens.MCP 默认值。默认值只在 tokens 层保留一份，不再散落各处。
  * 固件工具 schema 归一：ai-sdr Mini ``list_tools`` 返回的
    ``{name, desc, params}`` → MCP 标准 ``{name, description, inputSchema}``，
    供 MBDSDRAgent.register_mcp_tools 直接注册。
  * 转发策略：只转发硬件面（设备 list_tools 注册名 ∪ MCP_TOOL_ALIASES 登记名），
    软件工具不过二次转发；别名表是模块级可配置数据，不是内联写死的 lambda 表。

红线：不造假数据——设备没报告的工具名不臆造 schema；不转发的调用如实标注
skipped，不伪造「未知工具」失败。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from tokens import tokens as _tok  # noqa: E402  # tokens 层默认值

#: 环境变量覆盖通道（设置面板/命令行之外的免 UI 覆盖口）
ENV_MCP_HOST = "MBDSDR_MCP_HOST"
ENV_MCP_PORT = "MBDSDR_MCP_PORT"


#: UI 历史工具名 → 远端 MCP 方法映射（可配置：外部可增删改，不必改代码）。
#:
#: * "method"：真正发给远端的 JSON-RPC 方法名；
#: * "params_map"：可选，本地参数键 → 远端参数键的改名表（缺省同名直传）。
#:
#: 这张表是旧 mcp_worker._real_tool 内联 method_map 的可配置化继任者：
#: 只登记「历史上确实打到过硬件面」的名字；纯软件工具不登记、不转发。
MCP_TOOL_ALIASES: Dict[str, Dict[str, Any]] = {
    "tune_fm":         {"method": "tune_fm"},
    "tune_am":         {"method": "tune_am"},
    "set_volume":      {"method": "set_volume"},
    "start_record":    {"method": "start_record"},
    "stop_record":     {"method": "stop_record"},
    "get_version":     {"method": "get_version"},
    "reboot":          {"method": "reboot"},
    "list_tools":      {"method": "list_tools"},
    # AI 层 SDR 工具名（由远端 MCP 服务路由；参数键同名直传）
    "tune_sdr":            {"method": "tune_sdr"},
    "sdr_set_frequency":   {"method": "sdr_set_frequency"},
}


def resolve_mcp_endpoint(host: Optional[str] = None,
                         port: Optional[int] = None) -> Tuple[str, int]:
    """解析 ai-sdr Mini 端点：显式参数 > 环境变量 > tokens 默认值。

    端口参数/环境变量非法（非数字、越界）时如实回退下一级，不抛异常。
    """
    tok = _tok().MCP

    if not host:
        host = os.environ.get(ENV_MCP_HOST, "") or str(tok["default_host"])

    if port is None:
        env_port = os.environ.get(ENV_MCP_PORT, "")
        try:
            port = int(env_port) if env_port else int(tok["default_port"])
        except (TypeError, ValueError):
            port = int(tok["default_port"])
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = int(tok["default_port"])
    if not (0 < port < 65536):
        port = int(tok["default_port"])
    return str(host), port


# ── arm_manual 人工门（AGENTS 红线：写工具必走）────────────────────────
#
# 模型驱动的硬件写操作必须显式 arm_manual=true 才真正下发；只读工具不受限。
# UI 直操（频谱拖动/滑杆）是用户自己的手，等价人工门已满足，不走此判定。

#: arm_manual 是本地门控 token，不透传给设备固件
GATE_PARAM = "arm_manual"

#: 明确只读的工具名（前缀/精确名）；不在表内的硬件工具保守按写操作处理
READ_ONLY_TOOL_PREFIXES = ("get_", "list_", "check_", "web_")
READ_ONLY_TOOL_NAMES = {"list_tools", "web_ota_url"}


def is_write_tool(tool_name: str) -> bool:
    """硬件工具是否为写操作（保守判定：非明确只读即写）。"""
    name = (tool_name or "").strip()
    if not name:
        return False
    if name in READ_ONLY_TOOL_NAMES:
        return False
    return not name.startswith(READ_ONLY_TOOL_PREFIXES)


def annotate_write_gate(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给写工具的 schema 注入 arm_manual 门（模型可见，同 sdr_tools 先例）。

    写工具：描述追加人工门说明、inputSchema 增加 arm_manual 属性并列入
    required（缺参即被 ToolRegistry 预校验拦下，附带提示）。只读工具原样返回。
    """
    out: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or not is_write_tool(tool.get("name", "")):
            out.append(tool)
            continue
        t = dict(tool)
        t["description"] = (
            str(t.get("description", "") or "").rstrip()
            + " 写操作，带 arm_manual 手动门（必须显式 arm_manual=true 才真正下发，防误触）。"
        )
        schema = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
        props = dict(schema.get("properties") or {})
        props[GATE_PARAM] = {
            "type": "boolean",
            "description": "手动确认门：写操作必须显式传 true 才执行",
        }
        schema["properties"] = props
        required = list(schema.get("required") or [])
        if GATE_PARAM not in required:
            required.append(GATE_PARAM)
        schema["required"] = required
        t["inputSchema"] = schema
        out.append(t)
    return out


def firmware_tools_to_mcp_schema(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把固件 list_tools 结果归一为 MCP 标准工具 schema 列表。

    输入条目形如 ``{"name": "tune_fm", "desc": "...", "params": {"freq_mhz": "..."}}``
    （params 值是参数说明文本）；输出 ``{name, description, inputSchema}``。
    已是 MCP 形状（含 inputSchema/parameters）的条目原样透传；无名条目丢弃，
    绝不臆造名字。
    """
    mcp_tools: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "")
        if not name:
            continue
        if "inputSchema" in tool or "parameters" in tool:
            # 已是 MCP 形状：只补 description 缺省，schema 原样保留
            mcp_tools.append({
                "name": name,
                "description": tool.get("description") or tool.get("desc", ""),
                "inputSchema": tool.get(
                    "inputSchema",
                    tool.get("parameters", {"type": "object", "properties": {}})),
            })
            continue

        properties: Dict[str, Any] = {}
        required: List[str] = []
        for pname, pdesc in (tool.get("params") or {}).items():
            text = str(pdesc)
            # 简单类型推断（与 mbdsdr_mcp_client.StdioMCPServer 一致）：
            # 说明文本含数值语义词 → number，否则 string
            ptype = "number" if any(
                kw in text.lower() for kw in ("int", "float", "khz", "mhz", "volume")
            ) else "string"
            properties[pname] = {"type": ptype, "description": text}
            required.append(pname)

        mcp_tools.append({
            "name": name,
            "description": tool.get("desc", "") or f"MCP 工具: {name}",
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return mcp_tools


def normalize_tool_call(tool_name: str, params: Optional[Dict[str, Any]],
                        registered_names: Any = (),
                        aliases: Optional[Dict[str, Dict[str, Any]]] = None
                        ) -> Optional[Dict[str, Any]]:
    """把一次工具调用归一成远端转发计划；软件工具返回 None（不二次转发）。

    转发条件（二选一）：
      1. ``tool_name`` 在设备 list_tools 报告的注册名里（硬件面事实源）；
      2. ``tool_name`` 在可配置别名表 MCP_TOOL_ALIASES 里（UI 历史名）。

    返回 ``{"method": 远端方法名, "params": 改名后的参数}``。
    """
    name = (tool_name or "").strip()
    if not name:
        return None

    def _clean(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        out = dict(params or {})
        out.pop(GATE_PARAM, None)  # 门控 token 只在本地判定，不发给设备
        return out

    if name in (registered_names or ()):
        return {"method": name, "params": _clean(params)}

    table = MCP_TOOL_ALIASES if aliases is None else aliases
    entry = table.get(name)
    if not entry:
        return None

    method = entry.get("method", name)
    params_map = entry.get("params_map") or {}
    out = _clean(params)
    for src, dst in params_map.items():
        if src in out and dst not in out:
            out[dst] = out.pop(src)
    return {"method": method, "params": out}
