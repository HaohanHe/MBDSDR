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

        # ── SDRangel 真实 DSP 引擎移植工具 ──
        # 来源: repos/sdrangel/sdrbase/dsp/* 及 plugins/channelrx/*
        self.register_sdrangel_tools()

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
