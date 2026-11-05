# SPDX-License-Identifier: MIT
"""MBDSDR Web 产品桥接适配层（`window.mbdsdrBridge` → AI 内核工具注册表）。

============================================================================
为什么单独一层
============================================================================
产品前端（`web/product/`）的 ``MB.CONTRACT`` 有 8 个方法，默认走离线诚实空态。
阶段 A 只把产品工程合进仓库，没有接运行时。本模块是**阶段 B 的 Python 侧**：
把 ``mbdsdr_ai/tool_registry.py`` 的发现 / 调用能力，映射成宿主可注入
``window.mbdsdrBridge`` 的形状，并对齐前端契约。

**不碰桌面 UI**：本模块不 import Qt、不 import ``desktop/main_window.py``、
不 import任何硬件后端。宿主（Qt/WebEngine、或本地 HTTP 包装层）负责把
:meth:`WebBridge.as_bridge_dict` 的方法对象注入到 JS 全局；怎么注入、怎么把
同步返回值包成 Promise，是宿主的事，不在本笔范围（记为 not_verified）。

============================================================================
写工具确认门（S13 红线，最重要的一条）
============================================================================
仓库里有**两道不同的门**，都不是「模型自报 arm_manual」：

* C++ ``ControlHub``：宿主写开关 ``writeEnabled_``（``control_hub.cpp`` 的
  ``row->write && !writeEnabled_.load()``），未开返回 ``{ok:false,gated:true}``。
* Python 内核 / MCP：外部确认通道签发的**一次性** ``confirm_token``
  （别名 ``tx_token``），由 ``mbdsdr_ai/core/mcp_server.py`` 的写工具门消费。
  ``mcp_server.py`` 明确写「模型自报 arm_manual **不再**参与判定」，只记录
  「出现过」，然后照样拒绝（``manual_gate_required``）。

``ToolRegistry.call()`` 本身**没有这道门**——门在 ``mcp_server.call_tool`` 层。
所以本桥接层的铁律是：

1. 写工具**绝不**直接走 ``registry.call()``；
2. 缺 ``confirm_token`` / ``tx_token`` → 不下发，返回 ``manual_gate_required``；
3. 即便带了 token，也只有注入了 ``confirm_channel``（应接到带门的
   ``mcp_server.call_tool``）才转发；没接 → 一律 gated，**不自行放行**；
4. 模型自报的 ``arm_manual`` 被剥离并记录 ``selfReportRejected``；
5. ``gated`` 是「未获确认」，**不是执行失败、更不是设备故障**。

本层**不签发 token、不打开宿主写开关、不评估策略**。它只是把门的位置摆正，
让 Web 前端无法成为绕过真人确认的通道。

============================================================================
诚实口径
============================================================================
* ``is_live()`` 只表示「桥接对象已连到工具注册表」，**不代表设备在线**；
  设备状态由工具回执的 ``ok`` / ``error`` 决定。
* 卫星 / TLE / 过境 / 频谱默认**不提供**（``[]`` / ``None``），除非宿主注入
  了各自的 provider。站址 ``site_trusted=False`` 时过境一律空列表，对齐
  ``mbdsdr_ai/station.py`` 与 AGENTS.md §1 身份红线。
* 不合成 IQ、不编造 TLE、不把工具名当能力数量标语。
* 本模块不提供任何「定轨」入口；定轨不是功能，只能由用户在对话里说出口。

============================================================================
not_verified（勿当作已上线）
============================================================================
* 宿主注入（QWebChannel / JS 全局）尚未实现；本模块只定义可注入的形状。
* ``confirm_channel`` 尚未接到 ``mcp_server.call_tool``；未接时写工具恒 gated。
* 未做真实硬件逐工具实测；回执口径以运行时 ``tools/list`` 为准。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

#: 与 ``web/product/src/00-core.js`` 的 ``MB.CONTRACT`` 逐字对齐（含顺序）。
#: ``tests/test_web_bridge.py`` 会读 JS 源文件做跨语言钉扎，改一边必须改两边。
CONTRACT_METHODS: Sequence[str] = (
    "sourceName", "isLive", "listSatellites", "getTle",
    "getPasses", "getCapabilities", "readSpectrum", "invokeTool",
)

#: 内核写工具门认的字段名，来自 ``mbdsdr_ai/core/mcp_server.py``。
TOKEN_FIELDS: Sequence[str] = ("confirm_token", "tx_token")
#: 模型可自报、但内核明确不作为放行依据的字段名。
SELF_REPORT_FIELDS: Sequence[str] = ("arm_manual",)

_METHOD_MAP: Dict[str, str] = {
    "sourceName": "source_name",
    "isLive": "is_live",
    "listSatellites": "list_satellites",
    "getTle": "get_tle",
    "getPasses": "get_passes",
    "getCapabilities": "get_capabilities",
    "readSpectrum": "read_spectrum",
    "invokeTool": "invoke_tool",
}


def envelope(kind: str, tool: str = "", **extra: Any) -> Dict[str, Any]:
    """统一诚实回执：``ok:false`` 打底，任何异常路径都不伪装成功。"""
    out: Dict[str, Any] = {"ok": False, "tool": tool or "", "data": None,
                           "error": kind}
    out.update(extra)
    return out


class WebBridge:
    """产品契约 → ``ToolRegistry`` 的适配层。

    参数全部由宿主注入，本类不主动构造硬件 / 策略 / 网络对象：

    ``registry``
        实现 ``get_tool_names()`` / ``list_tools()`` / ``is_write_tool(name)``
        / ``call(name, args) -> ToolResult`` 的对象（鸭子类型，便于离线测试）。
        传 ``None`` 表示离线，:meth:`is_live` 为 False，工具调用返回
        ``not_connected``。
    ``confirm_channel``
        可选 ``(name, args) -> dict``，**应接到带写工具门的**
        ``mcp_server.call_tool``。写工具只有经它转发；未注入则恒 gated。
    ``satellites`` / ``tle_provider`` / ``pass_provider`` / ``spectrum_reader``
        可选数据 provider。不注入就诚实给空值，绝不编造。
    ``site_trusted``
        站址是否可信；False 时 :meth:`get_passes` 一律返回空列表。
    """

    def __init__(
        self,
        registry: Any = None,
        *,
        confirm_channel: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        satellites: Optional[Sequence[Dict[str, Any]]] = None,
        tle_provider: Optional[Callable[[str], Any]] = None,
        pass_provider: Optional[Callable[[str], Any]] = None,
        spectrum_reader: Optional[Callable[[], Any]] = None,
        site_trusted: bool = False,
    ) -> None:
        self.registry = registry
        self.confirm_channel = confirm_channel
        self.satellites = list(satellites) if satellites is not None else None
        self.tle_provider = tle_provider
        self.pass_provider = pass_provider
        self.spectrum_reader = spectrum_reader
        self.site_trusted = bool(site_trusted)
        self.last_gate: Optional[Dict[str, Any]] = None

    # ── 契约方法（snake_case，宿主按 _METHOD_MAP 注入 camelCase） ──────────

    def source_name(self) -> str:
        return "tool_registry.python" if self.is_live() else "bridge.offline"

    def is_live(self) -> bool:
        """只表示桥接已连到注册表；**不代表设备在线**。"""
        if self.registry is None:
            return False
        try:
            names = self.registry.get_tool_names()
        except (AttributeError, TypeError):
            return False
        return bool(names)

    def list_satellites(self) -> List[Dict[str, Any]]:
        return list(self.satellites) if self.satellites is not None else []

    def get_tle(self, norad: str = "") -> Any:
        if self.tle_provider is None:
            return None
        return self.tle_provider(norad)

    def get_passes(self, norad: str = "") -> List[Any]:
        if not self.site_trusted or self.pass_provider is None:
            return []
        result = self.pass_provider(norad)
        return list(result) if isinstance(result, (list, tuple)) else []

    def get_capabilities(self) -> List[Dict[str, Any]]:
        """真实工具面（``tools/list`` 等价），不是产品层的能力位分组。

        产品侧 ``MB.CAPS`` 是可视化分组，由 ``repo-facts.json`` 生成；本方法
        返回的是内核实际注册的工具，供宿主 / 审计核对，两者不要混为一谈。
        """
        if not self.is_live():
            return []
        tools = self.registry.list_tools(include_unavailable=True)
        return [
            {
                "id": t.get("name", ""),
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "category": t.get("category", ""),
                "available": bool(t.get("available", False)),
                "write": bool(self.registry.is_write_tool(t.get("name", ""))),
            }
            for t in tools
        ]

    def read_spectrum(self) -> Any:
        if self.spectrum_reader is None:
            return None
        return self.spectrum_reader()

    # ── 门与调用 ────────────────────────────────────────────────────────

    @staticmethod
    def strip_self_report(args: Optional[Dict[str, Any]]) -> tuple[Dict[str, Any], List[str]]:
        """剥离模型自报标记；返回 ``(干净 args, 被剥离的字段名)``。"""
        clean = dict(args or {})
        seen = [k for k in SELF_REPORT_FIELDS if k in clean]
        for k in seen:
            clean.pop(k, None)
        return clean, seen

    @staticmethod
    def has_confirm_token(args: Dict[str, Any]) -> bool:
        return any(bool(args.get(k)) for k in TOKEN_FIELDS)

    def _gated(self, name: str, code: str, clean: Dict[str, Any],
               seen: List[str], why: str) -> Dict[str, Any]:
        out = envelope(
            code, name,
            gated=True,
            notFailure=True,          # 未获确认 ≠ 执行失败
            gate="mcp",
            selfReportRejected=list(seen),
            suggestion=why,
            confirmChannelWired=self.confirm_channel is not None,
        )
        self.last_gate = out
        return out

    def invoke_tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用内核工具，返回产品侧信封（宿主负责把 dict 包成 Promise）。

        写工具的三条拒路都在**调用 registry 之前**，因此 ``registry.call``
        永远不会被本层用作绕过真人确认的通道。
        """
        clean, seen = self.strip_self_report(args)
        if not self.is_live():
            return envelope("not_connected", name,
                            offline=True, selfReportRejected=list(seen),
                            suggestion="connect the tool registry first")

        try:
            is_write = bool(self.registry.is_write_tool(name))
        except (AttributeError, TypeError):
            is_write = False

        if is_write:
            if not self.has_confirm_token(clean):
                return self._gated(
                    name, "manual_gate_required", clean, seen,
                    "由外部确认通道签发一次性 confirm_token；模型自报 arm_manual 不算确认")
            if self.confirm_channel is None:
                return self._gated(
                    name, "manual_gate_unavailable", clean, seen,
                    "确认通道未接入 mcp_server.call_tool，本层不自行放行写工具")
            # token 与 args 原样交回带门的内核层；本层不消费、不验证、不签发。
            result = self.confirm_channel(name, clean)
            if isinstance(result, dict):
                return self._normalise(name, result, seen)
            return envelope("bad_envelope", name,
                            detail="confirm_channel returned a non-dict response")

        try:
            result = self.registry.call(name, clean)
        except (AttributeError, TypeError) as exc:
            # 具体异常，转成诚实信封；绝不静默吞掉。
            return envelope("exception", name,
                            detail=f"{type(exc).__name__}: {exc}")
        return self._normalise(name, self._result_dict(result), seen)

    @staticmethod
    def _result_dict(result: Any) -> Dict[str, Any]:
        if isinstance(result, dict):
            return result
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return {"content": str(result)}

    @staticmethod
    def _normalise(name: str, raw: Dict[str, Any], seen: List[str]) -> Dict[str, Any]:
        """把 ToolResult / MCP 回执归一成产品信封，保留 gated 与失败的区分。"""
        ok = bool(raw.get("ok", raw.get("success", False)))
        gated = bool(raw.get("gated", False)) or raw.get("code") in (
            "manual_gate_required", "manual_gate_unavailable")
        error = raw.get("error") or raw.get("code") or ""
        out = envelope(error or ("ok" if ok else "tool_failed"), name,
                       data=raw.get("data"),
                       content=raw.get("content", ""),
                       latencyMs=raw.get("latency_ms", 0.0),
                       truncated=bool(raw.get("truncated", False)),
                       nextStep=raw.get("next_step", ""))
        out["ok"] = ok and not gated
        if gated:
            out["gated"] = True
            out["notFailure"] = True
            # 结构化门错误码优先于自由文本：UI 按 code 判「未获确认」，
            # 不能让 "token rejected" 这类文案把它冲掉。
            out["error"] = raw.get("code") or error or "manual_gate_required"
            if raw.get("error") and raw.get("error") != out["error"]:
                out["detail"] = raw["error"]
        if seen:
            out["selfReportRejected"] = list(seen)
        return out

    # ── 宿主注入形状与契约描述 ──────────────────────────────────────────

    def as_bridge_dict(self) -> Dict[str, Any]:
        """可直接作为 ``window.mbdsdrBridge`` 形状的方法字典。

        注意：产品 ``10-data.js`` 的 ``BridgeSource`` 用 ``b.invoke``（不是
        ``invokeTool``），并用 ``Promise.resolve(...)`` 兼容同步返回值；因此
        ``invoke`` 直接绑到 :meth:`invoke_tool` 即可，不要求宿主再包装。
        """
        return {
            "invoke": self.invoke_tool,
            "satellites": self.list_satellites(),
            "getTle": self.get_tle,
            "getPasses": self.get_passes,
            "readSpectrum": self.read_spectrum,
            "sourceName": self.source_name,
            "isLive": self.is_live,
            "getCapabilities": self.get_capabilities,
            "manifest": self.manifest,
        }

    def manifest(self) -> Dict[str, Any]:
        """JSON 契约描述，供宿主注入实现 / 跨语言对拍 / 文档生成使用。"""
        return {
            "version": "1.0",
            "globalName": "mbdsdrBridge",
            "source": "mbdsdr_ai/web_bridge.py",
            "contractMethods": list(CONTRACT_METHODS),
            "methodMap": dict(_METHOD_MAP),
            "bridgeShape": {
                "invoke": "(name, args) -> envelope dict (host may wrap in Promise)",
                "satellites": "array, optional; default []",
                "getTle": "(norad) -> TLE or null, optional",
                "getPasses": "(norad) -> array, optional; [] when site untrusted",
                "readSpectrum": "() -> array | null, optional; never synthesised",
                "getCapabilities": "() -> real tools/list entries",
                "sourceName": "() -> string",
                "isLive": "() -> bool; bridge attached, NOT device online",
            },
            "gate": {
                "controlHub": {
                    "owner": "C++ host write switch writeEnabled_",
                    "result": "{ok:false, gated:true}",
                    "frontEndAction": "forward args only; never open the switch",
                },
                "mcp": {
                    "owner": "external confirmation channel",
                    "tokenFields": list(TOKEN_FIELDS),
                    "selfReportFields": list(SELF_REPORT_FIELDS),
                    "errorCode": "manual_gate_required",
                    "selfReportRejected": True,
                    "frontEndMayIssueToken": False,
                },
                "gatedIsNotFailure": True,
            },
            "honesty": {
                "noFakeData": True,
                "isLiveIsNotDeviceOnline": True,
                "siteUntrustedBlocksPasses": not self.site_trusted,
                "orbitDeterminationNotAFeature": True,
            },
            "notVerified": [
                "host injection (QWebChannel / JS global) is not implemented",
                "confirm_channel is not wired to mcp_server.call_tool; writes stay gated",
                "no real-hardware per-tool verification",
            ],
        }

    def manifest_json(self) -> str:
        return json.dumps(self.manifest(), ensure_ascii=False, indent=2)


__all__ = ["CONTRACT_METHODS", "TOKEN_FIELDS", "SELF_REPORT_FIELDS",
           "WebBridge", "envelope"]
