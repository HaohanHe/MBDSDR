# SPDX-License-Identifier: MIT
"""
MBDSDR Core - MCP / JSON-RPC 2.0 接口
=======================================

把 :class:`~mbdsdr_ai.core.controller.SDRController` 的能力暴露成 MCP 工具，
供任意 MCP 客户端（Cursor / Claude Desktop / 通用 agent）通过 stdio 调用。

协议：JSON-RPC 2.0 over stdin/stdout。
  * ``initialize``   握手，返回服务端信息
  * ``tools/list``   列出全部工具（name / description / inputSchema）
  * ``tools/call``   调用工具，参数 ``{"name": ..., "arguments": {...}}``

无后端 / 未连接时，工具返回 ``{"error": "not_connected", "suggestion": ...}``，
绝不崩溃、绝不造假数据。

本文件纯标准库（json / sys / threading），绝不 import PySide6/Qt。
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from .controller import SDRController
from .errors import SDRUserError


# JSON Schema 片段复用
_NUM = {"type": "number"}
_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


# ======================================================================
# 工具注册表：每个工具 = (name, description, inputSchema, handler)
# handler 签名 (ctrl, args: dict) -> 可 JSON 序列化的结果
# ======================================================================
def _noop(ctrl: SDRController, args: Dict[str, Any]) -> Any:  # pragma: no cover
    return {"ok": True}


def _build_tool_table() -> List[Dict[str, Any]]:
    """构造静态工具表（与 SDRController 方法一一对应）。"""
    table: List[Dict[str, Any]] = []

    def add(name: str, desc: str, schema: Dict[str, Any],
            handler: Callable[[SDRController, Dict[str, Any]], Any]) -> None:
        table.append({
            "name": name,
            "description": desc,
            "inputSchema": schema,
            "handler": handler,
        })

    # ---- 设备 ----
    add("list_devices", "枚举可见 SDR 设备（无硬件返回空，含 debug 调试源）。",
        _schema({}), lambda c, a: [d.to_dict() if hasattr(d, "to_dict") else {
            "name": getattr(d, "name", str(d)),
            "driver": getattr(d, "driver", ""),
            "serial": getattr(d, "serial", ""),
        } for d in c.list_devices()])

    add("connect", "连接设备。device_id 为序列号/'driver:serial'，或 'debug' 调试信号源。",
        _schema({"device_id": _STR}, ["device_id"]),
        lambda c, a: {"connected": c.connect(a["device_id"])})

    add("disconnect", "断开当前设备。", _schema({}),
        lambda c, a: (c.disconnect(), {"connected": False})[1])

    add("is_connected", "是否已连接后端。", _schema({}),
        lambda c, a: {"connected": c.is_connected()})

    # ---- 接收参数 ----
    add("set_frequency", "调谐中心频率（Hz）。",
        _schema({"hz": _NUM}, ["hz"]),
        lambda c, a: (c.set_frequency(float(a["hz"])), {"ok": True})[1])

    add("get_frequency", "读取当前中心频率（Hz）。", _schema({}),
        lambda c, a: {"frequency_hz": c.get_frequency()})

    add("set_demod", "设置解调模式（AM/FM/WFM/NFM/USB/LSB/CW）。",
        _schema({"mode": _STR}, ["mode"]),
        lambda c, a: (c.set_demod(a["mode"]), {"ok": True})[1])

    add("get_demod", "读取当前解调模式。", _schema({}),
        lambda c, a: {"demod": c.get_demod()})

    add("set_gain", "设置总增益（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_gain(float(a["db"])), {"ok": True})[1])

    add("set_sample_rate", "设置基带采样率（Hz）。",
        _schema({"sr": _NUM}, ["sr"]),
        lambda c, a: (c.set_sample_rate(float(a["sr"])), {"ok": True})[1])

    add("set_bandwidth", "设置信道带宽（Hz）。",
        _schema({"hz": _NUM}, ["hz"]),
        lambda c, a: (c.set_bandwidth(float(a["hz"])), {"ok": True})[1])

    add("set_squelch", "设置静噪门限（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_squelch(float(a["db"])), {"ok": True})[1])

    # ---- 音频 ----
    add("list_audio_outputs", "枚举音频输出设备。", _schema({}),
        lambda c, a: [d.to_dict() for d in c.list_audio_outputs()])

    add("set_audio_output", "选择音频输出设备索引。",
        _schema({"index": _INT}, ["index"]),
        lambda c, a: {"ok": c.set_audio_output(int(a["index"]))})

    add("start_audio", "启动音频播放。", _schema({}),
        lambda c, a: {"ok": c.start_audio()})

    add("stop_audio", "停止音频播放。", _schema({}),
        lambda c, a: (c.stop_audio(), {"ok": True})[1])

    add("set_volume", "设置播放音量（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_volume(float(a["db"])), {"ok": True})[1])

    add("set_mute", "静音/取消静音。",
        _schema({"muted": _BOOL}, ["muted"]),
        lambda c, a: (c.set_mute(bool(a["muted"])), {"ok": True})[1])

    # ---- DSP ----
    add("read_spectrum", "读一帧功率谱（dB 数组，长度 nfft）。",
        _schema({"nfft": _INT}, ["nfft"]),
        lambda c, a: _ndarray_to_json(c.read_spectrum(int(a["nfft"]))))

    add("read_iq", "读 n 个复基带样本（complex64，转 list）。",
        _schema({"n": _INT}, ["n"]),
        lambda c, a: _ndarray_to_json(c.read_iq(int(a["n"]))))

    add("read_audio", "读 n 个解调后音频样本。",
        _schema({"n": _INT}, ["n"]),
        lambda c, a: _ndarray_to_json(c.read_audio(int(a["n"]))))

    add("start_analyze", "对当前信号做自动调制识别。",
        _schema({"modulation": _STR}),
        lambda c, a: c.start_analyze(a.get("modulation")))

    # ---- 扫描 ----
    add("start_scan", "启动步进扫频，返回扫描句柄。",
        _schema({"start_hz": _NUM, "stop_hz": _NUM, "step_hz": _NUM},
                ["start_hz", "stop_hz", "step_hz"]),
        lambda c, a: {"handle": c.start_scan(float(a["start_hz"]),
                                             float(a["stop_hz"]),
                                             float(a["step_hz"]))})

    add("stop_scan", "停止扫描。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: (c.stop_scan(a["handle"]), {"ok": True})[1])

    add("get_scan_results", "获取扫描活动段结果。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: c.get_scan_results(a["handle"]))

    # ---- 解码 ----
    add("start_decoder", "启动解码器（adsb/aprs/noaa_apt/meteor），返回句柄。",
        _schema({"decoder_type": _STR, "params": {"type": "object"}},
                ["decoder_type"]),
        lambda c, a: {"handle": c.start_decoder(a["decoder_type"], a.get("params"))})

    add("stop_decoder", "停止解码器。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: (c.stop_decoder(a["handle"]), {"ok": True})[1])

    add("get_decoder_messages", "取走解码器累积报文。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: c.get_decoder_messages(a["handle"]))

    # ---- 卫星 ----
    add("list_satellites", "列出内置可接收卫星。", _schema({}),
        lambda c, a: [s.to_dict() for s in c.list_satellites()])

    add("get_satellite_pass", "预报某 NORAD 编号卫星最近一次过境。",
        _schema({"norad": _INT}, ["norad"]),
        lambda c, a: c.get_satellite_pass(int(a["norad"])))

    add("tune_satellite", "按多普勒调谐到某卫星。",
        _schema({"norad": _INT}, ["norad"]),
        lambda c, a: c.tune_satellite(int(a["norad"])))

    add("set_ground_station", "设置地面站位置。",
        _schema({"lat": _NUM, "lon": _NUM, "alt_m": _NUM},
                ["lat", "lon"]),
        lambda c, a: (c.set_ground_station(float(a["lat"]), float(a["lon"]),
                                           float(a.get("alt_m", 0.0))),
                      {"ok": True})[1])

    # ---- 书签 ----
    add("add_bookmark", "添加书签。",
        _schema({"freq_hz": _NUM, "name": _STR, "mode": _STR, "group": _STR},
                ["freq_hz", "name"]),
        lambda c, a: c.add_bookmark(float(a["freq_hz"]), a["name"],
                                    a.get("mode", "FM"), a.get("group", "")))

    add("list_bookmarks", "列出书签（可按 group 过滤）。",
        _schema({"group": _STR}),
        lambda c, a: c.list_bookmarks(a.get("group")))

    add("delete_bookmark", "按 id（频率 Hz）删除书签。",
        _schema({"id": _INT}, ["id"]),
        lambda c, a: {"ok": c.delete_bookmark(int(a["id"]))})

    add("find_nearest_bookmark", "找离给定频率最近的书签。",
        _schema({"freq_hz": _NUM}, ["freq_hz"]),
        lambda c, a: c.find_nearest_bookmark(float(a["freq_hz"])))

    # ---- 录制 ----
    add("start_recording", "开始 IQ 录制（SigMF）。",
        _schema({"path": _STR}, ["path"]),
        lambda c, a: c.start_recording(a["path"]))

    add("stop_recording", "停止录制。", _schema({}),
        lambda c, a: c.stop_recording())

    add("get_recording_status", "录制状态。", _schema({}),
        lambda c, a: c.get_recording_status())

    # ---- 服务 ----
    add("start_remote_control", "启动 GQRX 风格远程控制服务。",
        _schema({"port": _INT}, ["port"]),
        lambda c, a: c.start_remote_control(int(a["port"])))

    add("stop_remote_control", "停止远程控制服务。", _schema({}),
        lambda c, a: (c.stop_remote_control(), {"ok": True})[1])

    add("start_web_server", "启动 Web 服务器。",
        _schema({"port": _INT}, ["port"]),
        lambda c, a: c.start_web_server(int(a["port"])))

    add("stop_web_server", "停止 Web 服务器。", _schema({}),
        lambda c, a: (c.stop_web_server(), {"ok": True})[1])

    add("get_service_status", "远程/Web 服务状态。", _schema({}),
        lambda c, a: c.get_service_status())

    # ---- ANR ----
    add("set_anr_enabled", "开关自适应降噪。",
        _schema({"enabled": _BOOL}, ["enabled"]),
        lambda c, a: (c.set_anr_enabled(bool(a["enabled"])), {"ok": True})[1])

    add("set_anr_strength", "设置降噪强度 0-10。",
        _schema({"level": _INT}, ["level"]),
        lambda c, a: (c.set_anr_strength(int(a["level"])), {"ok": True})[1])

    add("learn_noise_floor", "用当前音频学习噪声底。", _schema({}),
        lambda c, a: c.learn_noise_floor())

    # ---- 多 VFO ----
    add("add_vfo", "新建 VFO，返回 id。",
        _schema({"freq_hz": _NUM, "mode": _STR, "bw_hz": _NUM},
                ["freq_hz"]),
        lambda c, a: {"vfo_id": c.add_vfo(float(a["freq_hz"]),
                                          a.get("mode", "FM"),
                                          float(a.get("bw_hz", 12500.0)))})

    add("remove_vfo", "删除 VFO。",
        _schema({"vfo_id": _STR}, ["vfo_id"]),
        lambda c, a: (c.remove_vfo(a["vfo_id"]), {"ok": True})[1])

    add("set_primary_vfo", "设为主听 VFO。",
        _schema({"vfo_id": _STR}, ["vfo_id"]),
        lambda c, a: {"ok": c.set_primary_vfo(a["vfo_id"])})

    add("list_vfos", "列出全部 VFO。", _schema({}),
        lambda c, a: [v.to_dict() for v in c.list_vfos()])

    # ---- 系统 ----
    add("get_status", "完整系统状态。", _schema({}),
        lambda c, a: c.get_status().to_dict())

    add("shutdown", "优雅关闭全部资源。", _schema({}),
        lambda c, a: (c.shutdown(), {"ok": True})[1])

    return table


# ======================================================================
# 二级域目录（P1 #64）——冻结 8 域，与 C++ 侧 mcp_server.cpp 同名同序
#
# 这是服务器侧分组视图：工具 schema 的唯一事实源仍是上面的
# ``_build_tool_table()``；这里只做“每工具恰好归属一个域”的桶分组。
# Python 面（SDRController）当前没有 VNA 工具，故三个 VNA 域在本表面向用户
# 为空卡片（诚实空态）；8 域名集合本身保持与 C++ 完全一致，形成跨语言钉扎。
# ======================================================================
FROZEN_DOMAIN_SUMMARIES: Dict[str, str] = {
    "SDR 调谐/VFO": "接收机调谐、解调模式、带宽/静噪/降噪与多 VFO 及频谱读取参数控制。",
    "亚音数字解码": "CTCSS/DCS 亚音与各类数字报文解码器的启停与报文读取。",
    "VNA 测量": "VNA 扫频设置与 S 参数原始测量读取。",
    "VNA 分析": "对 VNA 测量数据做谐振/阻抗匹配等分析。",
    "VNA 校准": "VNA 校准状态与校准流程控制。",
    "导出演制": "IQ 录制、录制状态与音频/频谱数据导出。",
    "书签扫描卫星": "书签管理、步进扫描与卫星过境预报/调谐。",
    "系统设备": "设备连接、音频/服务开关与整体状态。",
}

FROZEN_DOMAIN_NAMES: List[str] = list(FROZEN_DOMAIN_SUMMARIES.keys())

# 本表面工具 -> 域。未列出的工具会被 _grouped_tool_cards 视为“未分配”，
# 测试会据此钉死“每工具恰好一个域”。
_TOOL_DOMAIN: Dict[str, str] = {
    # SDR 调谐/VFO
    "set_frequency": "SDR 调谐/VFO",
    "get_frequency": "SDR 调谐/VFO",
    "set_demod": "SDR 调谐/VFO",
    "get_demod": "SDR 调谐/VFO",
    "set_gain": "SDR 调谐/VFO",
    "set_sample_rate": "SDR 调谐/VFO",
    "set_bandwidth": "SDR 调谐/VFO",
    "set_squelch": "SDR 调谐/VFO",
    "start_analyze": "SDR 调谐/VFO",
    "set_anr_enabled": "SDR 调谐/VFO",
    "set_anr_strength": "SDR 调谐/VFO",
    "learn_noise_floor": "SDR 调谐/VFO",
    "add_vfo": "SDR 调谐/VFO",
    "remove_vfo": "SDR 调谐/VFO",
    "set_primary_vfo": "SDR 调谐/VFO",
    "list_vfos": "SDR 调谐/VFO",
    "read_spectrum": "SDR 调谐/VFO",
    # 亚音数字解码
    "start_decoder": "亚音数字解码",
    "stop_decoder": "亚音数字解码",
    "get_decoder_messages": "亚音数字解码",
    # VNA 测量 / VNA 分析 / VNA 校准：本面无 VNA 工具（空卡片，诚实空态）
    # 导出演制
    "start_recording": "导出演制",
    "stop_recording": "导出演制",
    "get_recording_status": "导出演制",
    "read_iq": "导出演制",
    "read_audio": "导出演制",
    # 书签扫描卫星
    "start_scan": "书签扫描卫星",
    "stop_scan": "书签扫描卫星",
    "get_scan_results": "书签扫描卫星",
    "list_satellites": "书签扫描卫星",
    "get_satellite_pass": "书签扫描卫星",
    "tune_satellite": "书签扫描卫星",
    "set_ground_station": "书签扫描卫星",
    "add_bookmark": "书签扫描卫星",
    "list_bookmarks": "书签扫描卫星",
    "delete_bookmark": "书签扫描卫星",
    "find_nearest_bookmark": "书签扫描卫星",
    # 系统设备
    "list_devices": "系统设备",
    "connect": "系统设备",
    "disconnect": "系统设备",
    "is_connected": "系统设备",
    "list_audio_outputs": "系统设备",
    "set_audio_output": "系统设备",
    "start_audio": "系统设备",
    "stop_audio": "系统设备",
    "set_volume": "系统设备",
    "set_mute": "系统设备",
    "start_remote_control": "系统设备",
    "stop_remote_control": "系统设备",
    "start_web_server": "系统设备",
    "stop_web_server": "系统设备",
    "get_service_status": "系统设备",
    "get_status": "系统设备",
    "shutdown": "系统设备",
}


def _resolve_domain(query: str) -> Optional[str]:
    """把 params.domain 容错解析成冻结域名；匹配不到返回 None。"""
    if query in FROZEN_DOMAIN_NAMES:
        return query
    q = query.strip().lower()
    for name in FROZEN_DOMAIN_NAMES:
        if name.lower() == q:
            return name
    hits = [n for n in FROZEN_DOMAIN_NAMES
            if q and (n.lower().find(q) >= 0 or q.find(n.lower()) >= 0)]
    if len(hits) == 1:
        return hits[0]
    return None


def _ndarray_to_json(arr: Any) -> Any:
    """numpy 数组转 JSON；None 表示无数据。"""
    if arr is None:
        return {"data": None,
                "error": "not_connected",
                "suggestion": "请先 connect() 一个设备或调试信号源。"}
    try:
        return {"data": [float(x) for x in arr.tolist()],
                "length": int(arr.size)}
    except Exception as e:  # pragma: no cover
        return {"error": "not_available", "technical_detail": str(e)}


# ======================================================================
# MCPToolSet
# ======================================================================
class MCPToolSet:
    """把 SDRController 方法暴露成 MCP 工具集。

    Parameters
    ----------
    controller :
        共享的 :class:`SDRController`。不传则自建一个（无后端）。
    """

    def __init__(self, controller: Optional[SDRController] = None) -> None:
        self.controller = controller if controller is not None else SDRController()
        self._table = _build_tool_table()
        self._by_name: Dict[str, Dict[str, Any]] = {
            t["name"]: t for t in self._table
        }

    # ------------------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        """返回扁平工具列表（name/description/inputSchema，不含 handler）。

        保留为兼容内部调用；对外 ``tools/list`` 走 :meth:`tools_list_result`
        的二级域分组视图。
        """
        return [
            {"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]}
            for t in self._table
        ]

    # ------------------------------------------------------------------
    # 二级域分组视图（P1 #64，与 C++ mcp_server.cpp 同形状）
    # ------------------------------------------------------------------
    def _bucketed(self, full: bool) -> Dict[str, List[Dict[str, Any]]]:
        """按 _TOOL_DOMAIN 把本表工具分桶。full=True 放完整 schema，否则只放名。"""
        buckets: Dict[str, List[Dict[str, Any]]] = {
            n: [] for n in FROZEN_DOMAIN_NAMES}
        for t in self._table:
            dom = _TOOL_DOMAIN.get(t["name"])
            if dom is None:
                continue  # 未分配工具：测试会钉死不发生
            if full:
                buckets[dom].append(
                    {"name": t["name"], "description": t["description"],
                     "inputSchema": t["inputSchema"]})
            else:
                buckets[dom].append({"name": t["name"]})
        return buckets

    def tools_list_result(self, params: Optional[Dict[str, Any]] = None):
        """tools/list 的 result 体；带错误串时返回 (None, errmsg)。"""
        params = params or {}
        detailed = bool(params.get("detailed", False))
        dom = params.get("domain")

        if isinstance(dom, str) and dom:
            name = _resolve_domain(dom)
            if name is None:
                return None, ("未知 domain: %s（合法域: %s）"
                              % (dom, "、".join(FROZEN_DOMAIN_NAMES)))
            entries = [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"]}
                for t in self._table if _TOOL_DOMAIN.get(t["name"]) == name]
            return {"tools": entries}, None

        buckets = self._bucketed(full=detailed)
        if detailed:
            cards = [
                {"domain": n, "summary": FROZEN_DOMAIN_SUMMARIES[n],
                 "tools": buckets[n]}
                for n in FROZEN_DOMAIN_NAMES]
            return {"domains": cards}, None

        # 默认：域卡片（只列工具名）。
        cards = [
            {"domain": n, "summary": FROZEN_DOMAIN_SUMMARIES[n],
             "tools": [t["name"] for t in buckets[n]]}
            for n in FROZEN_DOMAIN_NAMES]
        return {"tools": cards}, None

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """按名调用工具。无后端时安全降级，绝不抛异常。"""
        tool = self._by_name.get(name)
        if tool is None:
            return {"error": "unknown_tool", "tool": name,
                    "suggestion": "用 tools/list 查看可用工具。"}
        args = dict(arguments or {})
        try:
            return tool["handler"](self.controller, args)
        except SDRUserError as e:
            return e.to_dict()
        except Exception as e:  # pragma: no cover - 防御
            return {"error": "internal_error", "technical_detail": str(e)}

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 分发（纯函数，便于测试）
    # ------------------------------------------------------------------
    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理一条 JSON-RPC 请求。通知（无 id）返回 None。"""
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        def ok(result: Any) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code: int, message: str) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": message}}

        if method == "initialize":
            return ok({"protocolVersion": "2024-11-05",
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "mbdsdr-core", "version": "0.1.0"}})
        if method == "notifications/initialized":
            return None  # 通知无需回复
        if method == "tools/list":
            result, errmsg = self.tools_list_result(params)
            if errmsg is not None:
                return err(-32602, errmsg)
            return ok(result)
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = self.call_tool(name, arguments)
            # MCP 约定：结果包在 content 文本里
            text = json.dumps(result, ensure_ascii=False, default=str)
            return ok({"content": [{"type": "text", "text": text}]})
        return err(-32601, f"未知方法: {method}")


# ======================================================================
# stdio 服务器主循环
# ======================================================================
def run_mcp_server(controller: Optional[SDRController] = None) -> None:
    """启动 stdio MCP 服务器（JSON-RPC 2.0 over stdin/stdout）。

    阻塞运行，逐行读 stdin、逐行写 stdout。错误打到 stderr（不污染协议流）。
    """
    toolset = MCPToolSet(controller)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[mcp] 无法解析 JSON: {e}\n")
            continue
        try:
            resp = toolset.handle_request(req)
        except Exception as e:  # pragma: no cover
            sys.stderr.write(f"[mcp] 处理异常: {e}\n")
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    run_mcp_server()
