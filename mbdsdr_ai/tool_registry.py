"""
MBDSDR AI 内核 - 工具注册表
============================
管理 MCP 工具的注册、发现、调用。

核心能力：
- 工具注册（MCP 工具封装为 OpenAI function calling 格式）
- 工具发现（list_tools，让模型先知道有哪些工具）
- 可靠调用（处理参数错误、工具不存在、调用超时）
- 工具输出大小限制（大输出截断/写文件）
- 工具可用性检查（设备未插时告知不可用）
- 工具调用日志（记录每次调用的输入输出）
- 内置工具（上下文查询、模型切换、配置管理等）
"""

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable


@dataclass
class ToolResult:
    """工具调用结果。"""
    success: bool
    content: str
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str = ""
    truncated: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "tool_name": self.tool_name,
            "args": self.args,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "truncated": self.truncated,
            "data": self.data,
        }


@dataclass
class ToolCallLog:
    """工具调用日志条目。"""
    timestamp: float
    tool_name: str
    args: Dict[str, Any]
    success: bool
    latency_ms: float
    error: str = ""
    content_preview: str = ""


class ToolRegistry:
    """
    工具注册表。

    管理所有可用工具，包括：
    - MCP 硬件工具（通过 MCP 客户端调用 ai-sdr Mini）
    - 内置 AI 工具（上下文查询、模型切换、配置管理等）
    - 自进化工具（沙箱执行、代码修改等）
    """

    def __init__(self, tool_output_max_chars: int = 4000):
        self.tools: Dict[str, Dict[str, Any]] = {}  # name -> {definition, handler, available, category}
        self.tool_output_max_chars = tool_output_max_chars
        self.call_log: List[ToolCallLog] = []
        self.max_log_entries = 200
        self._mcp_client = None  # MCP 客户端引用（运行时设置）

    # ── 工具注册 ────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        category: str = "general",
        available: bool = True,
    ):
        """注册一个工具。"""
        definition = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self.tools[name] = {
            "definition": definition,
            "handler": handler,
            "available": available,
            "category": category,
        }

    def register_mcp_tools(self, mcp_tools: List[Dict[str, Any]], mcp_call_handler: Callable):
        """
        批量注册 MCP 工具。

        mcp_tools: MCP 工具列表（从 list_tools 获取）
        mcp_call_handler: 调用 MCP 工具的函数 (tool_name, args) -> result
        """
        for tool in mcp_tools:
            name = tool.get("name", "")
            if not name:
                continue
            description = tool.get("description", f"MCP 工具: {name}")
            parameters = tool.get("inputSchema", tool.get("parameters", {"type": "object", "properties": {}}))

            def make_handler(tool_name):
                def handler(args):
                    return mcp_call_handler(tool_name, args)
                return handler

            self.register(
                name=name,
                description=description,
                parameters=parameters,
                handler=make_handler(name),
                category="mcp",
                available=True,
            )

    def unregister(self, name: str):
        """注销工具。"""
        self.tools.pop(name, None)

    def set_available(self, name: str, available: bool):
        """设置工具可用性（设备未插时设为 False）。"""
        if name in self.tools:
            self.tools[name]["available"] = available

    # ── 工具发现 ────────────────────────────────────────

    def list_tools(self, include_unavailable: bool = False) -> List[Dict[str, Any]]:
        """
        列出所有可用工具（用于 list_tools 工具和模型工具发现）。
        """
        result = []
        for name, tool in self.tools.items():
            if not tool["available"] and not include_unavailable:
                continue
            fn = tool["definition"]["function"]
            result.append({
                "name": name,
                "description": fn["description"],
                "parameters": fn.get("parameters", {
                    "type": "object", "properties": {},
                }),
                "category": tool["category"],
                "available": tool["available"],
            })
        return result

    def get_tool_definitions(self, include_unavailable: bool = False) -> List[Dict[str, Any]]:
        """获取 OpenAI function calling 格式的工具定义列表。"""
        definitions = []
        for name, tool in self.tools.items():
            if not tool["available"] and not include_unavailable:
                continue
            definitions.append(tool["definition"])
        return definitions

    def get_tool_names(self) -> List[str]:
        """获取所有工具名称。"""
        return list(self.tools.keys())

    def has_tool(self, name: str) -> bool:
        """检查工具是否存在且可用。"""
        resolved = name if name in self.tools else self._resolve_tool_name(name)
        return resolved in self.tools and self.tools[resolved]["available"]

    def _resolve_tool_name(self, tool_name: str) -> str:
        """工具名自动解析：兼容短名（spectrum_analyze）和全名（sdr_spectrum_analyze）。

        工作流定义和弱模型可能用短名，这里按优先级尝试各种前缀映射，
        找到第一个在工具注册表中存在的名字。
        """
        if tool_name in self.tools:
            return tool_name

        # 按优先级尝试的候选名
        candidates = []

        # 1. 直接加 sdr_ 前缀
        candidates.append(f"sdr_{tool_name}")

        # 2. spectrum_xxx → sdr_spectrum_xxx
        if tool_name.startswith("spectrum_"):
            candidates.append(f"sdr_{tool_name}")

        # 3. pointing_xxx → sdr_satellite_xxx
        if tool_name.startswith("pointing_"):
            candidates.append(f"sdr_satellite_{tool_name[9:]}")

        # 4. set_xxx → sdr_set_xxx
        if tool_name.startswith("set_"):
            candidates.append(f"sdr_{tool_name}")

        # 5. record_xxx → sdr_record_xxx
        if tool_name.startswith("record_"):
            candidates.append(f"sdr_{tool_name}")

        # 6. xxx_decode → sdr_decode_xxx
        if tool_name.endswith("_decode"):
            base = tool_name[:-7]  # 去掉 _decode
            candidates.append(f"sdr_decode_{base}")

        # 7. identify_xxx → sdr_identify_xxx
        if tool_name.startswith("identify_"):
            candidates.append(f"sdr_{tool_name}")

        # 8. spectrum_center_offset → sdr_measure_signal
        if tool_name == "spectrum_center_offset":
            candidates.append("sdr_measure_signal")

        # 返回第一个存在的候选名
        for cand in candidates:
            if cand in self.tools:
                return cand

        return tool_name  # 都找不到，返回原名（call 会报 tool_not_found）

    # ── 工具调用 ────────────────────────────────────────

    def call(self, tool_name: str, args: Dict[str, Any] = None) -> ToolResult:
        """
        调用工具。

        处理：
        - 工具不存在
        - 工具不可用（设备未插）
        - 参数错误
        - 调用异常
        - 输出过大截断
        """
        start_time = time.time()
        args = args or {}

        # 工具名自动解析：兼容短名（spectrum_analyze）和全名（sdr_spectrum_analyze）
        # 工作流定义里用的是短名，这里自动映射到工具注册表的全名
        if tool_name not in self.tools:
            tool_name = self._resolve_tool_name(tool_name)

        # 检查工具是否存在
        if tool_name not in self.tools:
            available = self.get_tool_names()
            result = ToolResult(
                success=False,
                content=f"工具 '{tool_name}' 不存在。可用工具: {', '.join(available[:10])}{'...' if len(available) > 10 else ''}",
                tool_name=tool_name,
                args=args,
                error="tool_not_found",
            )
            self._log_call(result)
            return result

        tool = self.tools[tool_name]

        # 检查工具是否可用
        if not tool["available"]:
            result = ToolResult(
                success=False,
                content=f"工具 '{tool_name}' 当前不可用（设备未连接或功能未启用）。请先连接设备或检查配置。",
                tool_name=tool_name,
                args=args,
                error="tool_unavailable",
            )
            self._log_call(result)
            return result

        # 参数别名归一化：弱模型可能传 path/freq/gain 等别名，自动映射到标准参数名
        # 仅当标准键不存在时才映射，不覆盖已有值
        # 关键：必须按"目标工具 schema 实际声明的键"来映射，否则会把工具本来就接受的
        # 标准名（如 meteor 工具的 satellite、workflow_execute 的 workflow_name）
        # 错误改名为另一个工具才用的 satellite_name/name，导致模型传对了反而报缺参。
        _PARAM_ALIASES = {
            "path": "file_path", "freq": "frequency_hz", "frequency": "frequency_hz",
            "samplerate": "sample_rate_hz", "sample_rate": "sample_rate_hz",
            "bw": "bandwidth_hz", "bandwidth": "bandwidth_hz",
            "gain": "gain_db", "input_file": "input_path",
            "sat": "satellite_name", "satellite": "satellite_name",
            "lat": "latitude", "lon": "longitude",
            "agc_enabled": "enabled", "agc": "enabled",
            "task_name": "name", "template_name": "name",
            "workflow_name": "name",
            "task_description": "description", "template_description": "description",
            "workflow_description": "description", "subagent_type": "type",
            "task": "goal", "input": "goal", "prompt": "goal",
        }
        tool_def = tool.get("definition", {}).get("function", {})
        _prop_keys = set(tool_def.get("parameters", {}).get("properties", {}).keys())
        if isinstance(args, dict):
            for _alias, _std in _PARAM_ALIASES.items():
                # 仅当：模型传了别名、没传标准名、且该工具确实声明标准名、且未声明别名本身时才映射
                if (_alias in args and _std not in args
                        and _std in _prop_keys and _alias not in _prop_keys):
                    args[_std] = args.pop(_alias)

        # required 参数预校验：缺失时返回明确错误，不让 handler 抛 KeyError
        required_params = tool_def.get("parameters", {}).get("required", [])
        if required_params and isinstance(args, dict):
            missing = [p for p in required_params if p not in args or args[p] is None]
            if missing:
                param_descs = tool_def.get("parameters", {}).get("properties", {})
                hints = []
                for p in missing:
                    desc = param_descs.get(p, {}).get("description", "")
                    ptype = param_descs.get(p, {}).get("type", "any")
                    hints.append(f"{p}({ptype}){': ' + desc if desc else ''}")
                result = ToolResult(
                    success=False,
                    content=f"工具 '{tool_name}' 缺少必填参数: {', '.join(missing)}。参数说明: {'; '.join(hints)}",
                    tool_name=tool_name,
                    args=args,
                    error="missing_required_params",
                )
                self._log_call(result)
                return result

        # 调用 handler
        try:
            handler = tool["handler"]
            raw_result = handler(args)

            # 统一处理返回值
            if isinstance(raw_result, ToolResult):
                result = raw_result
                result.tool_name = tool_name
                result.args = args
            elif isinstance(raw_result, dict):
                result = ToolResult(
                    success=raw_result.get("success", True),
                    content=raw_result.get("content", json.dumps(raw_result, ensure_ascii=False)),
                    tool_name=tool_name,
                    args=args,
                    data=raw_result,
                )
            elif isinstance(raw_result, str):
                result = ToolResult(
                    success=True,
                    content=raw_result,
                    tool_name=tool_name,
                    args=args,
                )
            else:
                result = ToolResult(
                    success=True,
                    content=str(raw_result),
                    tool_name=tool_name,
                    args=args,
                )

        except Exception as e:
            result = ToolResult(
                success=False,
                content=f"工具 '{tool_name}' 调用失败: {type(e).__name__}: {e}。请检查参数是否正确，或参考工具说明。",
                tool_name=tool_name,
                args=args,
                error=f"exception: {type(e).__name__}",
            )

        result.latency_ms = round((time.time() - start_time) * 1000, 1)

        # 输出裁剪（借鉴 DeepSeek harness tool-result-pruner：head/tail 保留，
        # 只砍中间）。频谱/扫频数据头部常有表头、尾部常有结论，纯从头切会丢结论。
        if len(result.content) > self.tool_output_max_chars:
            original_len = len(result.content)
            head = int(self.tool_output_max_chars * 0.6)
            tail = self.tool_output_max_chars - head
            result.content = (
                result.content[:head]
                + f"\n...[中间 {original_len - head - tail} 字符已裁剪]...\n"
                + result.content[-tail:]
            )
            result.truncated = True

        self._log_call(result)
        return result

    def call_from_model(self, tool_call: Dict[str, Any]) -> ToolResult:
        """
        从模型的 tool_calls 格式调用工具。

        tool_call 格式：
        {
            "id": "call_xxx",
            "type": "function",
            "function": {"name": "xxx", "arguments": "{...}"}
        }
        """
        fn = tool_call.get("function", {})
        name = fn.get("name", "")
        args_str = fn.get("arguments", "{}")

        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            # 尝试修复
            try:
                fixed = args_str.replace("'", '"').replace("True", "true").replace("False", "false")
                args = json.loads(fixed)
            except Exception:
                args = {"_raw": args_str}

        return self.call(name, args)

    # ── 调用日志 ────────────────────────────────────────

    def _log_call(self, result: ToolResult):
        """记录工具调用日志。"""
        log = ToolCallLog(
            timestamp=time.time(),
            tool_name=result.tool_name,
            args=result.args,
            success=result.success,
            latency_ms=result.latency_ms,
            error=result.error,
            content_preview=result.content[:200],
        )
        self.call_log.append(log)
        if len(self.call_log) > self.max_log_entries:
            self.call_log = self.call_log[-self.max_log_entries:]

    def get_call_log(self, limit: int = 20, tool_name: str = None) -> List[Dict[str, Any]]:
        """获取工具调用日志。"""
        logs = self.call_log
        if tool_name:
            logs = [l for l in logs if l.tool_name == tool_name]
        return [
            {
                "time": time.strftime("%H:%M:%S", time.localtime(l.timestamp)),
                "tool": l.tool_name,
                "success": l.success,
                "latency_ms": l.latency_ms,
                "error": l.error,
                "preview": l.content_preview,
            }
            for l in reversed(logs[-limit:])
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取工具调用统计。"""
        total = len(self.call_log)
        success = sum(1 for l in self.call_log if l.success)
        failed = total - success
        by_tool = {}
        for l in self.call_log:
            if l.tool_name not in by_tool:
                by_tool[l.tool_name] = {"total": 0, "success": 0, "failed": 0, "total_latency": 0}
            by_tool[l.tool_name]["total"] += 1
            if l.success:
                by_tool[l.tool_name]["success"] += 1
            else:
                by_tool[l.tool_name]["failed"] += 1
            by_tool[l.tool_name]["total_latency"] += l.latency_ms

        for t in by_tool.values():
            t["avg_latency_ms"] = round(t["total_latency"] / max(1, t["total"]), 1)
            del t["total_latency"]

        return {
            "total_calls": total,
            "successful": success,
            "failed": failed,
            "success_rate": round(success / max(1, total), 4),
            "registered_tools": len(self.tools),
            "available_tools": sum(1 for t in self.tools.values() if t["available"]),
            "by_tool": by_tool,
        }

    # ── 内置工具注册 ────────────────────────────────────

    def register_builtin_tools(self, agent_ref=None):
        """
        注册内置 AI 工具（上下文查询、模型管理、配置管理等）。
        agent_ref: Agent 引用（用于访问 context_manager, model_manager 等）。
        """
        # list_tools: 工具发现
        self.register(
            name="list_tools",
            description="列出所有可用工具。AI 连接后应先调用此工具发现可用能力。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(self.list_tools(), ensure_ascii=False, indent=2),
                data={"tools": self.list_tools()},
            ),
            category="meta",
        )

        # context_status: 上下文状态查询
        if agent_ref:
            self.register(
                name="context_status",
                description="查询当前上下文状态：token 用量、剩余空间、消息数、压缩次数、epoch。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda args: ToolResult(
                    success=True,
                    content=agent_ref.context_manager.get_status_text(),
                    data=agent_ref.context_manager.get_stats().to_dict(),
                ),
                category="meta",
            )

            # model_status: 模型状态查询
            self.register(
                name="model_status",
                description="查询当前模型状态：当前模型、API 配置、调用统计、可用模型列表。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda args: ToolResult(
                    success=True,
                    content=agent_ref.model_manager.get_status_text(),
                    data=agent_ref.model_manager.get_status(),
                ),
                category="meta",
            )

            # list_models: 模型列表查询
            self.register(
                name="list_models",
                description="查询所有可用模型列表。可用于切换模型前查看选项。",
                parameters={"type": "object", "properties": {
                    "refresh": {"type": "boolean", "description": "是否强制从 API 刷新"}
                }, "required": []},
                handler=lambda args: ToolResult(
                    success=True,
                    content=json.dumps(
                        agent_ref.model_manager.fetch_models(force_refresh=args.get("refresh", False)),
                        ensure_ascii=False, indent=2, default=lambda o: o.to_dict()
                    ),
                    data={"models": agent_ref.model_manager.list_models()},
                ),
                category="meta",
            )

            # switch_model: 模型切换
            self.register(
                name="switch_model",
                description="切换当前使用的 LLM 模型。",
                parameters={"type": "object", "properties": {
                    "model": {"type": "string", "description": "模型 ID，如 Qwen/Qwen2.5-7B-Instruct"}
                }, "required": ["model"]},
                handler=lambda args: ToolResult(
                    success=True,
                    content=f"模型切换: {agent_ref.model_manager.switch_model(args['model'])[1]}",
                ),
                category="meta",
            )

            # tool_log: 工具调用日志
            self.register(
                name="tool_log",
                description="查看最近的工具调用日志，用于调试和审计。",
                parameters={"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 10"},
                    "tool_name": {"type": "string", "description": "过滤指定工具"}
                }, "required": []},
                handler=lambda args: ToolResult(
                    success=True,
                    content=json.dumps(self.get_call_log(
                        limit=args.get("limit", 10),
                        tool_name=args.get("tool_name"),
                    ), ensure_ascii=False, indent=2),
                ),
                category="meta",
            )

            # ── Codec2 / FreeDV voice codec tools ──────────────────
            self.register(
                name="codec2_encode",
                description="Codec2 1600bps 语音编码：将 8kHz 音频采样压缩为比特流。",
                parameters={"type": "object", "properties": {
                    "audio": {"type": "array", "items": {"type": "number"}, "description": "8kHz 音频采样数组"}
                }, "required": ["audio"]},
                handler=lambda args: self._codec2_encode_handler(args),
                category="voice",
            )
            self.register(
                name="codec2_decode",
                description="Codec2 1600bps 语音解码：将比特流恢复为 8kHz 音频采样。",
                parameters={"type": "object", "properties": {
                    "bits": {"type": "array", "items": {"type": "integer"}, "description": "编码比特数组"}
                }, "required": ["bits"]},
                handler=lambda args: self._codec2_decode_handler(args),
                category="voice",
            )
            self.register(
                name="freedv_modulate",
                description="FreeDV 调制：将比特流调制为音频信号（700D OFDM 或 1600 DBPSK）。",
                parameters={"type": "object", "properties": {
                    "bits": {"type": "array", "items": {"type": "integer"}, "description": "输入比特"},
                    "mode": {"type": "string", "description": "模式: 700D 或 1600", "default": "700D"}
                }, "required": ["bits"]},
                handler=lambda args: self._freedv_modulate_handler(args),
                category="voice",
            )
            self.register(
                name="freedv_demodulate",
                description="FreeDV 解调：将音频信号解调为比特流。",
                parameters={"type": "object", "properties": {
                    "signal": {"type": "array", "items": {"type": "number"}, "description": "音频采样"},
                    "mode": {"type": "string", "description": "模式: 700D 或 1600", "default": "700D"}
                }, "required": ["signal"]},
                handler=lambda args: self._freedv_demodulate_handler(args),
                category="voice",
            )

            # ── HD Radio (NRSC-5) 工具 ──────────────────────────────
            self.register(
                name="hdradio_ofdm_demod",
                description="HD Radio (NRSC-5) OFDM 解调：将 744kHz 基带 IQ 解调为 QPSK 比特流。参考 nrsc5 ofdm/sync。",
                parameters={"type": "object", "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}, "description": "复 IQ 采样（实部/虚部交替）"}
                }, "required": ["iq"]},
                handler=lambda args: self._hdradio_ofdm_demod_handler(args),
                category="broadcast",
            )
            self.register(
                name="hdradio_frame_parse",
                description="HD Radio 帧解析：从 HDLC 字节流提取 PSD 节目名/标题，校验 FCS16。",
                parameters={"type": "object", "properties": {
                    "data": {"type": "array", "items": {"type": "integer"}, "description": "帧字节（0-255）"}
                }, "required": ["data"]},
                handler=lambda args: self._hdradio_frame_parse_handler(args),
                category="broadcast",
            )
            self.register(
                name="hdradio_decode_iq",
                description="HD Radio 完整 IQ 接收：粗+细同步 → OFDM/QPSK 解调 → 比特流（HDC 音频为骨架）。",
                parameters={"type": "object", "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}, "description": "复 IQ 采样"}
                }, "required": ["iq"]},
                handler=lambda args: self._hdradio_decode_iq_handler(args),
                category="broadcast",
            )

        # ── SDRangel 真实 DSP 引擎移植工具 ──
        # 来源: repos/sdrangel/sdrbase/dsp/* 及 plugins/channelrx/*
        self.register_sdrangel_tools()

        # ── GNU Radio 真实 DSP 块移植工具 ──
        # 来源: repos/gnuradio/gr-*（见 mbdsdr_ai/gnuradio_blocks.py）
        self.register_gnuradio_blocks_tools()

        # ── librtlsdr 真实硬件参数查询工具 ──
        # 来源: repos/librtlsdr/src/librtlsdr.c:959-969,1100-1101,1157,1165
        self.register_rtlsdr_params_tools()

        # ── dablin 真实 DAB/DAB+ FIC/FIB/FIG + ETI 层移植工具 ──
        # 来源: repos/dablin/src/{fic_decoder.cpp, eti_player.cpp, eti_source.h, tools.cpp}
        self.register_dab_plus_tools()

    def register_dab_plus_tools(self):
        """注册 dablin 真实 DAB/DAB+ 解析工具（ETI/FIC/FIB/FIG）。

        来源: repos/dablin/src/{fic_decoder.cpp, eti_player.cpp, eti_source.h, tools.cpp}
        """
        self.register(
            name="dab_fic_decode",
            description="解码 DAB FIC 字节流：FIB CRC-16/CCITT 校验 + FIG 解析，返回 "
                        "ensemble(EId/标签)、服务列表、子信道配置(起始CU/比特率)。",
            parameters={"type": "object", "properties": {
                "fic_bytes": {"type": "array", "items": {"type": "integer"},
                              "description": "FIC 字节数组(32 的倍数, 每 FIB 32 字节)"}
            }, "required": ["fic_bytes"]},
            handler=lambda args: self._dab_fic_decode_handler(args),
            category="broadcast",
        )
        self.register(
            name="dab_eti_parse",
            description="解析单个 6144 字节 ETI(NI) 帧：FSYNC(0x073AB6)同步、MNSC/STC/"
                        "FIC 层拆解、两级 CRC-16/CCITT 校验，并解码其中 FIC。",
            parameters={"type": "object", "properties": {
                "frame": {"type": "array", "items": {"type": "integer"},
                          "description": "6144 字节 ETI 帧数组"}
            }, "required": ["frame"]},
            handler=lambda args: self._dab_eti_parse_handler(args),
            category="broadcast",
        )
        self.register(
            name="dab_decode_iq",
            description="从一段(可能含前缀杂散的) ETI 字节完成基带后解码：自动找 FSYNC "
                        "同步 -> 帧对齐 -> ETI 层解析 -> FIC 服务表。",
            parameters={"type": "object", "properties": {
                "data": {"type": "array", "items": {"type": "integer"},
                         "description": "ETI 字节流(可能含前缀)"}
            }, "required": ["data"]},
            handler=lambda args: self._dab_decode_iq_handler(args),
            category="broadcast",
        )

    def _dab_fic_decode_handler(self, args: Dict[str, Any]) -> "ToolResult":
        from .dab_plus_lite import dab_fic_decode
        fb = bytes(args.get("fic_bytes", []))
        res = dab_fic_decode(fb)
        return ToolResult(
            success=True,
            content=f"DAB FIC: ensemble {res['ensemble']['label']!r} "
                    f"(EId {res['ensemble']['eid']:#06x}), "
                    f"{len(res['services'])} services, {len(res['subchannels'])} subchannels, "
                    f"discarded FIBs={res['discarded_fibs']}",
            data=res,
        )

    def _dab_eti_parse_handler(self, args: Dict[str, Any]) -> "ToolResult":
        from .dab_plus_lite import dab_eti_parse
        frame = bytes(args.get("frame", []))
        res = dab_eti_parse(frame)
        return ToolResult(
            success=res.get("ok", False),
            content=f"ETI parse: ok={res.get('ok')}, nst={res.get('nst')}, "
                    f"ficl={res.get('ficl')}, subchannels={res.get('subchannels')}",
            data=res,
        )

    def _dab_decode_iq_handler(self, args: Dict[str, Any]) -> "ToolResult":
        from .dab_plus_lite import dab_decode_iq
        data = bytes(args.get("data", []))
        res = dab_decode_iq(data)
        return ToolResult(
            success=res.get("ok", False),
            content=f"DAB decode: ok={res.get('ok')}, sync_offset={res.get('sync_offset')}",
            data=res,
        )

    def register_rtlsdr_params_tools(self):
        """注册 librtlsdr 真实参数查询工具（纯查表，无需插设备）。

        来源: mbdsdr_ai/rtlsdr_params.py（移植自 repos/librtlsdr）
          - 增益表     librtlsdr.c:959-969  rtlsdr_get_tuner_gains
          - 频率范围   tuner_e4k.c:351-352 / tuner_r82xx.c:1168
          - 采样率区间 librtlsdr.c:1100-1101 + rtl-sdr.h:260-263
        """
        from . import rtlsdr_params as rp

        self.register(
            name="rtlsdr_list_gains",
            description=(
                "列出某 RTL-SDR 调谐器真实支持的离散增益档（dB）。"
                "来源 librtlsdr rtlsdr_get_tuner_gains()。"
                "可选调谐器: E4000/FC0012/FC0013/FC2580/R820T/R828D。"
            ),
            parameters={"type": "object", "properties": {
                "tuner": {"type": "string", "description": "调谐器型号，如 R820T/E4000", "default": "R820T"}
            }, "required": []},
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(
                    rp.get_tuner_summary(args.get("tuner", "R820T")),
                    ensure_ascii=False, indent=2),
                data=rp.get_tuner_summary(args.get("tuner", "R820T")),
            ),
            category="sdr",
        )

        self.register(
            name="rtlsdr_get_freq_range",
            description=(
                "返回某 RTL-SDR 调谐器的真实频率范围 (Hz) 与采样率合法区间。"
                "来源 tuner_e4k.c:351-352 / tuner_r82xx.c:1168 / librtlsdr.c:1100-1101。"
            ),
            parameters={"type": "object", "properties": {
                "tuner": {"type": "string", "description": "调谐器型号", "default": "R820T"}
            }, "required": []},
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps({
                    "tuner": args.get("tuner", "R820T"),
                    "freq_range_hz": rp.get_frequency_range(args.get("tuner", "R820T")),
                    "sample_rate_valid": [rp.SAMPLE_RATE_MIN_HZ, rp.SAMPLE_RATE_MAX_HZ],
                    "sample_rate_dead_band": [rp.SAMPLE_RATE_DEAD_LOW, rp.SAMPLE_RATE_DEAD_HIGH],
                    "recommended_rates_hz": rp.get_supported_sample_rates(),
                }, ensure_ascii=False, indent=2),
                data={"tuner": args.get("tuner", "R820T")},
            ),
            category="sdr",
        )

        self.register(
            name="rtlsdr_set_params",
            description=(
                "把目标增益/采样率吸附到 librtlsdr 真实支持的离散档，"
                "返回可直接下发的参数（增益 dB、合法采样率 Hz、最近档）。"
                "移植自 convenience.c:116-141 nearest_gain。"
            ),
            parameters={"type": "object", "properties": {
                "tuner": {"type": "string", "description": "调谐器型号", "default": "R820T"},
                "gain_db": {"type": "number", "description": "目标增益 dB"},
                "sample_rate_hz": {"type": "number", "description": "目标采样率 Hz"},
            }, "required": []},
            handler=lambda args: self._rtlsdr_set_params_handler(args),
            category="sdr",
        )

    def _rtlsdr_set_params_handler(self, args):
        from . import rtlsdr_params as rp
        tuner = args.get("tuner", "R820T")
        out = {"tuner": tuner}
        if "gain_db" in args and args["gain_db"] is not None:
            g = rp.nearest_gain(tuner, float(args["gain_db"]))
            out["gain_db"] = g
            out["gain_table_db"] = rp.get_gain_table(tuner)
        if "sample_rate_hz" in args and args["sample_rate_hz"] is not None:
            r = float(args["sample_rate_hz"])
            out["sample_rate_requested_hz"] = r
            out["sample_rate_valid"] = rp.is_valid_sample_rate(r)
            out["recommended_rates_hz"] = rp.get_supported_sample_rates()
        return ToolResult(
            success=True,
            content=json.dumps(out, ensure_ascii=False, indent=2),
            data=out,
        )

    def register_sdrangel_tools(self):
        """注册 SDRangel 真实源码移植的 DSP 工具。

        来源: mbdsdr_ai/sdrangel_adapter.py
          - DSPDeviceEngine  dspdevicesourceengine.cpp:288-337
          - FFTFilter        fftfilt.cpp:144-186,436-457
          - DownChannelizer  downchannelizer.cpp:116-144 (HB order=48, downchannelizer.h:31)
        """
        import numpy as np
        from .sdrangel_adapter import (
            FFTFilter, DownChannelizer, DSPDeviceEngine, DEVICE_PRESETS,
        )

        def _dsp_engine_create(args):
            sr = int(args.get("sample_rate", 1024000))
            cf = int(args.get("center_frequency", 100e6))
            eng = DSPDeviceEngine(sample_rate=sr, center_frequency=cf)
            eng.start()
            return ToolResult(
                success=True,
                content=f"DSPDeviceEngine 创建: sr={sr} center={cf} state={eng.state}",
                data={"sample_rate": sr, "center_frequency": cf, "state": eng.state,
                      "sources": DEVICE_PRESETS},
            )

        def _fft_filter(args):
            f1 = float(args.get("f1", 0.0))
            f2 = float(args.get("f2", 0.05))
            flen = int(args.get("flen", 1024))
            f = FFTFilter(f1, f2, flen=flen)
            kind = "lowpass" if f1 == 0 else ("highpass" if f2 == 0 else
                   ("bandpass" if f1 < f2 else "bandreject"))
            return ToolResult(
                success=True,
                content=f"FFTFilter 设计: {kind} f1={f1} f2={f2} flen={flen} block={flen//2}",
                data={"kind": kind, "f1": f1, "f2": f2, "flen": flen,
                      "block": flen // 2, "source": "fftfilt.cpp:144-186"},
            )

        def _downchannelize(args):
            bsr = int(args.get("baseband_sr", 1024000))
            csr = int(args.get("channel_sr", 64000))
            off = float(args.get("channel_offset", 0.0))
            dc = DownChannelizer(bsr, csr, channel_offset=off)
            return ToolResult(
                success=True,
                content=f"DownChannelizer: {bsr}->{dc.channel_sr} "
                        f"({dc.n_stages} 级半带, offset={off}Hz)",
                data={"baseband_sr": bsr, "channel_sr": dc.channel_sr,
                      "n_stages": dc.n_stages, "channel_offset": off,
                      "hb_order": 48, "source": "downchannelizer.cpp:136, h=downchannelizer.h:31"},
            )

        self.register(
            name="dsp_engine_create",
            description="创建 SDRangel 移植的 DSP 采样流引擎（源→DC校正→多通道下变频→解调）。",
            parameters={"type": "object", "properties": {
                "sample_rate": {"type": "integer", "description": "基带采样率 Hz，默认 1024000"},
                "center_frequency": {"type": "integer", "description": "中心频率 Hz，默认 100e6"},
            }, "required": []},
            handler=_dsp_engine_create,
            category="dsp",
        )
        self.register(
            name="fft_filter",
            description="设计 SDRangel fftfilt overlap-add FFT 滤波器（低通/高通/带通/带阻）。f1,f2 为归一化频率(0.5=Nyquist)。",
            parameters={"type": "object", "properties": {
                "f1": {"type": "number", "description": "下边带/高通截止（归一化），低通=0"},
                "f2": {"type": "number", "description": "上边带/低通截止（归一化），高通=0"},
                "flen": {"type": "integer", "description": "FFT 长度(2的幂)，默认1024"},
            }, "required": ["f1", "f2"]},
            handler=_fft_filter,
            category="dsp",
        )
        self.register(
            name="downchannelize",
            description="SDRangel 移植的整数 2^N 下变频通道化：NCO混频+半带链抽取。",
            parameters={"type": "object", "properties": {
                "baseband_sr": {"type": "integer", "description": "基带采样率 Hz"},
                "channel_sr": {"type": "integer", "description": "目标通道采样率 Hz（须为 baseband_sr/2^N）"},
                "channel_offset": {"type": "number", "description": "通道中心偏移 Hz"},
            }, "required": ["baseband_sr", "channel_sr"]},
            handler=_downchannelize,
            category="dsp",
        )

    def register_gnuradio_blocks_tools(self):
        """注册 GNU Radio 真实源码移植的 DSP 块。

        来源: mbdsdr_ai/gnuradio_blocks.py（逐行对照 repos/gnuradio）
          - FIRFilter           gr-filter/lib/fir_filter.cc:34,91-114
          - FFTFilter           gr-filter/lib/fft_filter.cc:76,77,115-149 + fft_filter.h:72
          - AGC2                gr-analog/include/gnuradio/analog/agc2.h:64-85
          - RationalResampler   gr-filter/lib/rational_resampler_impl.cc:43-74,248-256
          - PFBArbResampler     gr-filter/lib/pfb_arb_resampler.cc:83,100,148-206
        """
        import numpy as np
        from .gnuradio_blocks import (
            FIRFilter, FFTFilter, AGC2, RationalResampler, PFBArbResampler,
        )

        def _fir_filter(args):
            taps = list(args.get("taps", [1.0]))
            sig = list(args.get("signal", []))
            x = np.array(sig, dtype=complex if any(isinstance(v, list) for v in sig) else float)
            y = FIRFilter(taps).filter(np.asarray(sig))
            return ToolResult(
                success=True,
                content=f"FIRFilter: {len(taps)} 抽头 → 输出 {len(y)} 点 "
                        f"(fir_filter.cc:34 抽头反转后卷积)",
                data={"ntaps": len(taps), "n_out": int(len(y)),
                      "source": "gr-filter/lib/fir_filter.cc:34,91-114"},
            )

        def _fft_filter(args):
            taps = list(args.get("taps", [1.0]))
            n = int(args.get("n", 1024))
            x = np.random.randn(n)
            ff = FFTFilter(taps)
            y = ff.filter(x)
            return ToolResult(
                success=True,
                content=f"FFTFilter overlap-add: fftsize={ff.fftsize} "
                        f"nsamples={ff.nsamples} tailsize={ff.tailsize} → {len(y)} 点",
                data={"ntaps": len(taps), "fftsize": ff.fftsize,
                      "nsamples": ff.nsamples, "tailsize": ff.tailsize,
                      "source": "gr-filter/lib/fft_filter.cc:76,77,115-149"},
            )

        def _agc_process(args):
            ref = float(args.get("reference", 1.0))
            attack = float(args.get("attack_rate", 1e-1))
            decay = float(args.get("decay_rate", 1e-2))
            n = int(args.get("n", 2000))
            sig = np.concatenate([np.full(n // 2, 0.2 + 0j),
                                  np.full(n // 2, 3.0 + 0j)])
            agc = AGC2(attack_rate=attack, decay_rate=decay, reference=ref)
            out = agc.process(sig)
            return ToolResult(
                success=True,
                content=f"AGC2: ref={ref} attack={attack} decay={decay} "
                        f"尾段输出幅度={np.mean(np.abs(out[-200:])):.3f}",
                data={"reference": ref, "tail_level": float(np.mean(np.abs(out[-200:]))),
                      "gain": float(agc.gain),
                      "source": "gr-analog/.../agc2.h:41-45,64-85"},
            )

        def _rational_resample(args):
            up = int(args.get("interpolation", 2))
            down = int(args.get("decimation", 1))
            n = int(args.get("n", 2048))
            f0 = float(args.get("f0", 0.1))
            x = np.cos(2 * np.pi * f0 * np.arange(n))
            y = RationalResampler(up, down).process(x)
            return ToolResult(
                success=True,
                content=f"RationalResampler {up}/{down}: {n} → {len(y)} 点 "
                        f"(Kaiser β=7.0, fractional_bw=0.4)",
                data={"interp": up, "decim": down, "n_in": n, "n_out": int(len(y)),
                      "beta": 7.0, "fractional_bw": 0.4,
                      "source": "gr-filter/lib/rational_resampler_impl.cc:55,68,248-256"},
            )

        def _pfb_resample(args):
            rate = float(args.get("rate", 0.75))
            n = int(args.get("n", 4000))
            f0 = float(args.get("f0", 0.3))
            nfilts = int(args.get("filter_size", 32))
            x = np.exp(2j * np.pi * f0 * np.arange(n))
            L = 257
            t = np.arange(L) - (L - 1) / 2
            taps = np.sinc(0.5 * t) * np.hanning(L)
            y = PFBArbResampler(rate=rate, taps=taps, filter_size=nfilts).process(x)
            return ToolResult(
                success=True,
                content=f"PFBArbResampler rate={rate}: {n} → {len(y)} 点 "
                        f"(比率 {len(y)/n:.3f})",
                data={"rate": rate, "n_in": n, "n_out": int(len(y)),
                      "ratio": len(y) / n, "filter_size": nfilts,
                      "source": "gr-filter/lib/pfb_arb_resampler.cc:100,148-206"},
            )

        self.register(
            name="fir_filter",
            description="GNU Radio 移植 FIR 滤波：抽头卷积，支持实数/复数。输出==numpy.convolve(x,taps)。",
            parameters={"type": "object", "properties": {
                "taps": {"type": "array", "items": {"type": "number"}, "description": "FIR 抽头系数"},
                "signal": {"type": "array", "items": {"type": "number"}, "description": "输入样本"},
            }, "required": ["taps"]},
            handler=_fir_filter,
            category="dsp",
        )
        self.register(
            name="fft_filter",
            description="GNU Radio 移植 overlap-add 快速卷积 FFT 滤波（长抽头高效）。fftsize=2·2^ceil(log2(ntaps))。",
            parameters={"type": "object", "properties": {
                "taps": {"type": "array", "items": {"type": "number"}, "description": "FIR 抽头"},
                "n": {"type": "integer", "description": "测试信号长度", "default": 1024},
            }, "required": ["taps"]},
            handler=_fft_filter,
            category="dsp",
        )
        self.register(
            name="agc_process",
            description="GNU Radio AGC2（attack/decay）：逐样本把输出幅度收敛到 reference。默认 attack=1e-1 decay=1e-2。",
            parameters={"type": "object", "properties": {
                "reference": {"type": "number", "description": "目标输出幅度", "default": 1.0},
                "attack_rate": {"type": "number", "default": 0.1},
                "decay_rate": {"type": "number", "default": 0.01},
            }, "required": []},
            handler=_agc_process,
            category="dsp",
        )
        self.register(
            name="rational_resample",
            description="GNU Radio 有理重采样：interpolation/decimation 倍插值/抽取，Kaiser 窗抗混叠(β=7.0)。",
            parameters={"type": "object", "properties": {
                "interpolation": {"type": "integer", "default": 2},
                "decimation": {"type": "integer", "default": 1},
                "f0": {"type": "number", "description": "测试单音归一化频率", "default": 0.1},
            }, "required": []},
            handler=_rational_resample,
            category="dsp",
        )
        self.register(
            name="pfb_resample",
            description="GNU Radio 多相任意重采样：任意 rate=out/in，多相滤波器组+微分线性插值，无混叠。",
            parameters={"type": "object", "properties": {
                "rate": {"type": "number", "description": "输出/输入采样率比", "default": 0.75},
                "filter_size": {"type": "integer", "description": "多相分支数", "default": 32},
                "f0": {"type": "number", "default": 0.3},
            }, "required": ["rate"]},
            handler=_pfb_resample,
            category="dsp",
        )

    def get_status_text(self) -> str:
        """获取人类可读的工具注册表状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 工具注册表 ===",
            f"已注册工具: {stats['registered_tools']} 个",
            f"可用工具: {stats['available_tools']} 个",
            f"调用统计: {stats['successful']} 成功 / {stats['failed']} 失败 "
            f"(成功率 {stats['success_rate']:.1%})",
        ]
        # 按类别分组
        categories = {}
        for name, tool in self.tools.items():
            cat = tool["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f"{name}{'' if tool['available'] else ' (不可用)'}")
        for cat, names in sorted(categories.items()):
            lines.append(f"  [{cat}] {', '.join(names)}")
        return "\n".join(lines)

    # ── Codec2 / FreeDV handlers ──────────────────────────────────────

    def _codec2_encode_handler(self, args: Dict[str, Any]) -> "ToolResult":
        """Codec2 encode handler."""
        import numpy as np
        from .codec2_lite import codec2_encode
        audio = np.array(args.get("audio", []), dtype=float)
        bits = codec2_encode(audio)
        return ToolResult(
            success=True,
            content=f"Codec2 encoded {len(audio)} samples → {len(bits)} bits ({len(bits)/0.04:.0f} bps)",
            data={"bits": bits.tolist(), "n_bits": len(bits)},
        )

    def _codec2_decode_handler(self, args: Dict[str, Any]) -> "ToolResult":
        """Codec2 decode handler."""
        import numpy as np
        from .codec2_lite import codec2_decode
        bits = np.array(args.get("bits", []), dtype=np.uint8)
        audio = codec2_decode(bits)
        return ToolResult(
            success=True,
            content=f"Codec2 decoded {len(bits)} bits → {len(audio)} audio samples",
            data={"audio": audio.tolist(), "n_samples": len(audio)},
        )

    def _freedv_modulate_handler(self, args: Dict[str, Any]) -> "ToolResult":
        """FreeDV modulate handler."""
        import numpy as np
        from .freedv_modem import freedv_modulate
        bits = np.array(args.get("bits", []), dtype=np.uint8)
        mode = args.get("mode", "700D")
        signal = freedv_modulate(bits, mode)
        return ToolResult(
            success=True,
            content=f"FreeDV {mode}: modulated {len(bits)} bits → {len(signal)} audio samples",
            data={"signal": signal.tolist(), "n_samples": len(signal)},
        )

    def _freedv_demodulate_handler(self, args: Dict[str, Any]) -> "ToolResult":
        """FreeDV demodulate handler."""
        import numpy as np
        from .freedv_modem import freedv_demodulate
        signal = np.array(args.get("signal", []), dtype=float)
        mode = args.get("mode", "700D")
        bits, snr = freedv_demodulate(signal, mode)
        return ToolResult(
            success=True,
            content=f"FreeDV {mode}: demodulated {len(signal)} samples → {len(bits)} bits (SNR est: {snr:.1f} dB)",
            data={"bits": bits.tolist(), "n_bits": len(bits), "snr": snr},
        )

    # ── HD Radio (NRSC-5) handlers ──────────────────────────────────────

    @staticmethod
    def _iq_to_complex(args: Dict[str, Any]) -> "np.ndarray":
        import numpy as np
        raw = np.array(args.get("iq", []), dtype=float)
        if raw.size == 0:
            return np.empty(0, dtype=complex)
        if raw.size % 2 == 0:
            return (raw[0::2] + 1j * raw[1::2]).astype(complex)
        return raw.astype(complex)

    def _hdradio_ofdm_demod_handler(self, args: Dict[str, Any]) -> "ToolResult":
        import numpy as np
        from . import nrsc5_lite as N
        iq = self._iq_to_complex(args)
        if iq.size == 0:
            return ToolResult(success=False, content="空 IQ 输入")
        res = N.hdradio_ofdm_demod(iq)
        return ToolResult(
            success=True,
            content=f"HD Radio OFDM: {len(iq)} 采样 → {len(res['bits'])} bits "
                    f"(FFT={res['fft_size']}, CP={res['cp']}, data_carriers={res['data_carriers']})",
            data={"bits": res["bits"].tolist(), "n_bits": len(res["bits"]),
                  "sym_offset": int(res["sym_offset"])},
        )

    def _hdradio_frame_parse_handler(self, args: Dict[str, Any]) -> "ToolResult":
        from . import nrsc5_lite as N
        data = bytes(args.get("data", []))
        res = N.hdradio_frame_parse(data)
        return ToolResult(
            success=True,
            content=f"HD Radio 帧: 提取 {res['psd_count']} 条 PSD 文本: {res['titles']}",
            data=res,
        )

    def _hdradio_decode_iq_handler(self, args: Dict[str, Any]) -> "ToolResult":
        from . import nrsc5_lite as N
        iq = self._iq_to_complex(args)
        if iq.size == 0:
            return ToolResult(success=False, content="空 IQ 输入")
        res = N.hdradio_decode_iq(iq)
        return ToolResult(
            success=True,
            content=f"HD Radio 接收: {res['n_bits']} bits (FFT={res['fft_size']})。"
                    f" HDC 音频为骨架，需外部 HE-AAC v2 库。",
            data=res,
        )
