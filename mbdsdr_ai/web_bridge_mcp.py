# SPDX-License-Identifier: MIT
"""把真实 MCP 工具面（``MCPToolSet``）接成 Web 产品的桥接后端。

============================================================================
为什么需要这一层
============================================================================
``mbdsdr_ai/web_bridge.py`` 只认识一个鸭子类型的 ``registry``（``get_tool_names``
/ ``list_tools`` / ``is_write_tool`` / ``call``），而仓库里真正带安全门、带 82 个
工具、带 ``ToolCallGate`` 的实现是 ``mbdsdr_ai/core/mcp_server.py::MCPToolSet``，
它的接口叫 ``list_tools`` / ``call_tool``，写工具标记在 ``_by_name[name]["write"]``。
本模块做两件事，别的什么都不做：

1. **适配**：``McpToolSetRegistry`` 把 ``MCPToolSet`` 包成 ``WebBridge`` 期望的形状，
   ``call()`` 一律走 ``toolset.call_tool()`` ——**绝不绕过** ``ToolCallGate``。
2. **真实确认通道**：``make_confirm_channel()`` 把「用户在同源页面上点了确认」
   翻译成内核要的东西：``tx_guard.issue_confirmation()`` 签发的**一次性 token**，
   绑定本次动作摘要，TTL 内一次消费。token 全程留在宿主进程内，
   前端 JS 只能"请求确认"，拿不到也伪造不了 token 字符串。

============================================================================
安全不变量（有测试钉死，改动前先读）
============================================================================
* 前端传来的 ``confirm_token`` / ``tx_token`` **一律丢弃**。谁都能往 JSON 里写
  一个字符串，宿主不认外来 token，只认自己刚签发的那一个。
* 没有 pending 确认 → 恒 ``manual_gate_required``，不签发、不执行。
* 一次确认只对一个 ``action_digest(工具名, 参数)`` 有效；换参数重放必然失配
  （``tx_guard`` 的 ``gate_action_mismatch``），执行完即作废（单次使用）。
* 确认表有 TTL（默认 60 s），过期自动清；``revoke_all()`` 可整体吊销。
* ``MBDSDR_BRIDGE_CONFIRM=deny`` 环境变量可**整体关闭**确认端点（只读模式）。

============================================================================
诚实口径 / not_verified
============================================================================
* 默认确认来源是「同源本地页面上的确认按钮」，**不是**物理隔离的确认通道
  （不是安全密钥、不是生物识别、不是独立设备）。同一台机器上的其他进程，
  只要能通过回环 + bridge token 校验，同样能触发确认——这是本地单用户桌面
  应用的既有边界，不吹成"绝对安全"。
* 不做频率限制与确认审计轮换；``tx_guard`` 侧的审计账本（opt-in
  ``MBDSDR_AUDIT_LOG``）才是留痕的地方。
* 真实硬件逐工具实测仍未做：无设备时工具会诚实返回设备错误，不是本层编造。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mbdsdr_ai.web_bridge import TOKEN_FIELDS, envelope

#: 确认有效期（秒）：够人看清提示再点，又不至于挂着一条长期授权
DEFAULT_CONFIRM_TTL_S = 60.0

#: 可整体关闭确认端点的环境变量（值为 deny/off/0 时生效）
CONFIRM_ENV = "MBDSDR_BRIDGE_CONFIRM"


def confirm_enabled() -> bool:
    """确认端点是否启用（默认启用；显式 deny/off/0 关闭）。"""
    return str(os.environ.get(CONFIRM_ENV, "")).strip().lower() not in (
        "deny", "off", "0", "false", "no")


class PendingConfirms:
    """宿主侧「用户已确认」登记表：动作摘要 → 过期时刻。

    它只记录"人说过可以"，**不是**授权本体；授权本体仍是 ``tx_guard`` 的
    一次性 token（由 :func:`make_confirm_channel` 在消费成功后签发）。
    """

    def __init__(self, ttl_s: float = DEFAULT_CONFIRM_TTL_S,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._ttl = float(ttl_s)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._items: Dict[str, Tuple[float, str]] = {}   # digest -> (expiry, tool)

    # ── 摘要 ────────────────────────────────────────────────────────────
    @staticmethod
    def digest(name: str, args: Optional[Dict[str, Any]]) -> str:
        """与内核门完全一致的动作摘要（复用 tx_guard，不复制算法）。"""
        from mbdsdr_ai.tx_guard import action_digest
        clean = {k: v for k, v in dict(args or {}).items()
                 if k not in TOKEN_FIELDS and k != "approve_session"}
        return action_digest(name, clean)

    def _purge(self, now: float) -> None:
        dead = [k for k, (exp, _) in self._items.items() if exp <= now]
        for k in dead:
            self._items.pop(k, None)

    # ── 生命周期 ────────────────────────────────────────────────────────
    def request(self, name: str, args: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
        """登记一次用户确认请求；返回 ``(是否接受, 摘要)``。"""
        if not confirm_enabled():
            return False, ""
        digest = self.digest(name, args)
        now = self._clock()
        with self._lock:
            self._purge(now)
            self._items[digest] = (now + self._ttl, str(name))
            return True, digest

    def consume(self, name: str, args: Optional[Dict[str, Any]]) -> bool:
        """消费一条确认（一次性）。工具名或参数变了就不算命中。"""
        digest = self.digest(name, args)
        now = self._clock()
        with self._lock:
            self._purge(now)
            return self._items.pop(digest, None) is not None

    def revoke_all(self) -> None:
        with self._lock:
            self._items.clear()

    def pending(self) -> List[Dict[str, Any]]:
        now = self._clock()
        with self._lock:
            self._purge(now)
            return [{"tool": tool, "expiresInS": round(exp - now, 1)}
                    for exp, tool in self._items.values()]


class McpToolSetRegistry:
    """``MCPToolSet`` → ``WebBridge`` 的 registry 协议适配器。

    ``call()`` 只走 ``toolset.call_tool()``（内含 ``ToolCallGate``：PreToolUse
    钩子 → 写工具 policy/一次性 token → 入参有限性校验）。本类**不**直接碰
    handler、**不**提供任何绕过门的方法。
    """

    def __init__(self, toolset: Any) -> None:
        self.toolset = toolset

    # ── 工具面 ──────────────────────────────────────────────────────────
    def get_tool_names(self) -> List[str]:
        try:
            return [t["name"] for t in self.toolset.list_tools()]
        except (AttributeError, TypeError, KeyError):
            return []

    def list_tools(self, include_unavailable: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for t in self.toolset.list_tools():
            name = str(t.get("name") or "")
            if not name:
                continue
            out.append({
                "name": name,
                "description": str(t.get("description") or ""),
                "category": self.domain_of(name),
                # 工具已注册即可调用；"available" 反映的是**设备**状态，
                # 无设备时调用会诚实返回设备错误，不在这里假装可用。
                "available": True,
                "write": self.is_write_tool(name),
            })
        return out

    def domain_of(self, name: str) -> str:
        try:
            from mbdsdr_ai.core.mcp_server import _TOOL_DOMAIN
            return str(_TOOL_DOMAIN.get(name, ""))
        except (ImportError, AttributeError):
            return ""

    def is_write_tool(self, name: str) -> bool:
        """写工具判定：以工具表 ``write`` 标记为准，名模式作为兜底。"""
        try:
            entry = self.toolset._by_name.get(name) or {}
            if "write" in entry:
                return bool(entry.get("write"))
        except AttributeError:
            pass
        try:
            from mbdsdr_ai.tool_registry import _auto_write
            return bool(_auto_write(name, None))
        except (ImportError, AttributeError):
            return False

    def schema_of(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            entry = self.toolset._by_name.get(name) or {}
        except AttributeError:
            return None
        schema = entry.get("inputSchema")
        return schema if isinstance(schema, dict) else None

    # ── 调用 ────────────────────────────────────────────────────────────
    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """经 ``MCPToolSet.call_tool`` 调用（门在里面，本层不加不减）。"""
        return self.toolset.call_tool(name, dict(args or {}))


def make_confirm_channel(
    registry: McpToolSetRegistry,
    pending: PendingConfirms,
    *,
    ttl_s: float = 30.0,
    source: str = "web_bridge",
) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """构造真实的 ``confirm_channel``：用户确认 → 签发一次性 token → 过内核门。

    Parameters
    ----------
    registry :
        :class:`McpToolSetRegistry`（其 ``call`` 带 ``ToolCallGate``）。
    pending :
        宿主侧确认登记表；**没有登记就一律拒绝**，不在本函数里找默认放行。
    ttl_s :
        签发的一次性 token 有效期（不是确认表的 TTL；后者更长，见
        :data:`DEFAULT_CONFIRM_TTL_S`）。
    """

    def channel(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        # 前端带来的任何 token 都不可信：一律剥掉，只认宿主自己签发的。
        clean = {k: v for k, v in dict(args or {}).items() if k not in TOKEN_FIELDS}
        if not validate_target(registry, name):
            return envelope("unknown_tool", name,
                            suggestion="工具不在内核注册表里，拒绝签发确认")
        if not pending.consume(name, clean):
            return envelope(
                "manual_gate_required", name,
                gated=True, notFailure=True, gate="mcp",
                suggestion=("需要用户在同一来源页面上先确认本次动作；"
                            "确认只对当前参数有效且只能使用一次"))
        from mbdsdr_ai.tx_guard import issue_confirmation
        try:
            token = issue_confirmation(name, clean, ttl_s=ttl_s,
                                       note="web bridge: user confirmation",
                                       source=source)
        except Exception as exc:      # 签发失败 = 不放行，绝不降级成裸调用
            return envelope("manual_gate_unavailable", name,
                            gated=True, notFailure=True, gate="mcp",
                            detail=f"{type(exc).__name__}: {exc}")
        result = registry.call(name, {**clean, "confirm_token": token})
        return result if isinstance(result, dict) else {"ok": True, "data": result}

    return channel


def validate_target(registry: McpToolSetRegistry, name: str) -> bool:
    """工具必须真实存在于内核注册表；防止白名单之外的反射调用。"""
    try:
        return name in registry.get_tool_names()
    except (AttributeError, TypeError):
        return False


def build_default_backend(
    *, mcp: bool = True,
    site_trusted: bool = False,
    satellites: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[Any, Optional[McpToolSetRegistry], PendingConfirms]:
    """构造默认后端：MCPToolSet（若可用）+ 适配器 + 确认表。

    返回 ``(WebBridge, registry 或 None, pending)``。``mcp=False`` 或构造失败时
    返回离线桥（``is_live()`` 为 False 的诚实空态），不抛异常。
    """
    from mbdsdr_ai.web_bridge import WebBridge

    pending = PendingConfirms()
    registry: Optional[McpToolSetRegistry] = None
    toolset = None
    if mcp:
        try:
            from mbdsdr_ai.core.mcp_server import MCPToolSet
            toolset = MCPToolSet()
            registry = McpToolSetRegistry(toolset)
            if not registry.get_tool_names():
                registry = None
        except Exception:      # 依赖缺失 / 构造失败 → 诚实离线，不假装在线
            registry = None
    if registry is None:
        return WebBridge(satellites=satellites, site_trusted=site_trusted), None, pending

    bridge = WebBridge(
        registry=registry,
        confirm_channel=make_confirm_channel(registry, pending),
        satellites=satellites,
        site_trusted=site_trusted,
        spectrum_reader=_spectrum_reader(registry),
    )
    return bridge, registry, pending


def _spectrum_reader(registry: McpToolSetRegistry) -> Callable[[], Any]:
    """只读谱线 provider：真实工具存在才接，否则诚实返回 None。"""

    def read() -> Any:
        if not validate_target(registry, "read_spectrum"):
            return None
        return registry.call("read_spectrum", {})

    return read
