# SPDX-License-Identifier: MIT
"""工具调用守卫的共享实现（MCP 面与进程内面唯一一份）。

两条面都必须回答同样三个问题，并且用同样的事件字段：
- 工具即将执行：``AGENT_PRE_TOOL_USE``（可否决）
- 工具已有结果：``AGENT_TOOL_RESULT``（只读观测）
- 这次是谁触发的：``data["trigger"]`` 区分 ``model`` / ``user``

本模块只依赖 ``hooks`` / ``hook_commands`` 两个叶子，不导入核心控制器，
避免把 MCP 面的重量级依赖带进工具注册表。

留痕必须落盘（2026-11-14）
--------------------------

事件总线是进程内的，没有观测者就什么也没留下。进程内 ``ToolRegistry`` 构造
时自动按 ``MBDSDR_AUDIT_LOG`` 挂一个 :class:`AuditLog` 通配符观测者，落点与
MCP 面完全相同（``~/.mbdsdr/audit.jsonl``），不另建第二处账本。这样即便调用
方是 UI 直点（如天气面板的 ``sat_image_enhance``），调用一次也能在同一个账本
里查到 ``tool`` / ``trigger`` / ``ok``。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Dict, Optional

from .hooks import Event, EventType

# ======================================================================
# 持久审计账本（AI 操作员可追溯性）
# ======================================================================
#: 审计账本开关：设为 JSONL 文件路径即开启；空串/未设则关闭（库内构造与
#: 测试默认无副作用，与 MBDSDR_HOOKS_CONFIG 同 pattern）。
AUDIT_LOG_ENV = "MBDSDR_AUDIT_LOG"

#: 操作员手动开启时的默认账本位置（~/.mbdsdr/ 已是 hooks.json 的家）。
AUDIT_LOG_DEFAULT = os.path.join(os.path.expanduser("~"), ".mbdsdr", "audit.jsonl")

#: 模型/代理发起的工具调用：过模型侧守卫，可被否决。
TRIGGER_MODEL = "model"
#: 用户直接点按/命令行发起的调用：只留痕，不被模型侧守卫拦。
TRIGGER_USER = "user"


def normalize_trigger(trigger: str) -> str:
    """只认 ``user``；其余一律按 ``model`` 处理（安全侧默认从严）。"""
    return (TRIGGER_USER if str(trigger or "").strip().lower() == TRIGGER_USER
            else TRIGGER_MODEL)


def emit_pre_tool_use(hooks: Any, hook_commands: Any, name: str,
                      args: Dict[str, Any], *, is_write: bool,
                      trigger: str = TRIGGER_MODEL,
                      source: str = "") -> Optional[Dict[str, Any]]:
    """触发 PRE 事件；返回否决字典或 None。

    trigger=user：只发事件留痕，不取否决、不跑外部命令闸（用户点按不是
    模型调用，不能让模型侧守卫把人自己的操作拦掉）。
    其余触发：先跑进程内 HookManager，再跑配置驱动的外部命令 hooks，
    任一否决即拦。任何异常都按放行处理（观测面绝不当主流程单点）。
    """
    normalized = normalize_trigger(trigger)
    try:
        ev = Event(
            event_type=EventType.AGENT_PRE_TOOL_USE,
            data={"tool": name, "arguments": dict(args or {}),
                  "write": bool(is_write), "trigger": normalized},
            source=source,
        )
        if normalized == TRIGGER_USER:
            hooks.trigger(ev)
            return None
        inner = hooks.veto_for(ev)
        if inner is not None:
            return inner
        # 外部命令 hooks（kimi-code externalHooks 子集）：exit 2 或
        # stdout JSON deny 即否决；超时/启动失败/其他退出码一律放行。
        return hook_commands.decide(ev)
    except Exception as e:  # pragma: no cover - 防御
        print("[tool_guard] pre_tool_use 钩子故障（放行）: %s" % e,
              file=sys.stderr)
        return None


def emit_tool_result(hooks: Any, name: str, result: Any, *,
                     trigger: str = TRIGGER_MODEL, source: str = "") -> None:
    """触发 RESULT 观测事件；钩子异常不冒泡。"""
    try:
        data = dict(result_summary(name, result))
        data["trigger"] = normalize_trigger(trigger)
        hooks.trigger(Event(event_type=EventType.AGENT_TOOL_RESULT,
                            data=data, source=source))
    except Exception as e:  # pragma: no cover - 观测面不得影响主流程
        print("[tool_guard] tool_result 钩子故障（忽略）: %s" % e,
              file=sys.stderr)


def result_summary(name: str, result: Any) -> Dict[str, Any]:
    """结果信封的观测摘要（不含完整负载）。

    键结构与 MCP 面一致：tool/kind/ok/outcome/spilled（``trigger`` 由
    :func:`emit_tool_result` 追加）。dict 信封与 ``ToolResult`` 走同一套键，
    这样两条面的事件字段可以直接对照。
    """
    if isinstance(result, dict):
        outcome = result.get("outcome")
        if outcome is None and result.get("ok") is True:
            outcome = "executed"
        return {"tool": name, "kind": "dict",
                "ok": result.get("ok"),
                "outcome": outcome,
                "spilled": bool(result.get("truncated"))}
    if hasattr(result, "success"):
        success = bool(result.success)
        error = str(getattr(result, "error", "") or "")
        data = getattr(result, "data", None)
        outcome = ("vetoed" if error == "hook_vetoed"
                   else ("executed" if success else "error"))
        return {"tool": name, "kind": type(result).__name__, "ok": success,
                "outcome": outcome,
                "spilled": bool(getattr(result, "truncated", False)
                                or (isinstance(data, dict)
                                    and data.get("truncated")))}
    return {"tool": name, "kind": type(result).__name__,
            "ok": None, "outcome": None, "spilled": False}


class AuditLog:
    """工具调用事件的持久 JSONL 审计账本。

    进程内 ``HookManager._history`` 重启即丢；"AI 操作员"叙事要求
    "谁/何时/对哪个工具/结果如何"可回溯。本类以通配符钩子挂在任意
    ``HookManager`` 上（MCP 面的 ``MCPToolSet.hooks`` 与进程内
    ``ToolRegistry.hooks`` 同一实现），把 AGENT_PRE_TOOL_USE /
    AGENT_TOOL_RESULT 事件按行追加到 JSONL（两事件都经
    ``HookManager.trigger``，通配符钩子必然收到）。

    口径与事件总线一致：只落事件信封（tool / arguments / ok / outcome
    摘要），长结果不重复落盘（spill 路径已有全文）。任何 IO 失败只记
    stderr 并放行——审计面绝不当工具链的单点故障源。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path if path is not None else AUDIT_LOG_DEFAULT
        self._lock = threading.Lock()
        self._dir_ready = False

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None,
                 default_on: bool = False) -> Optional["AuditLog"]:
        """按 MBDSDR_AUDIT_LOG 构造账本。

        ``default_on=False``（库内构造默认）：未设/空串 -> None（关闭），
        测试与 CI 无副作用。``default_on=True``（产品入口
        :func:`run_mcp_server` / 桌面端 ``main()``）：未设 -> 默认路径
        ``~/.mbdsdr/audit.jsonl``（与 ``tool_results`` spill 落盘同惯例）；
        显式 ``off``/``0`` 可关。
        """
        env = os.environ if env is None else env
        raw = (env.get(AUDIT_LOG_ENV) or "").strip()
        if raw:
            if raw.lower() in ("off", "0", "none", "false"):
                return None
            return cls(raw)
        return cls() if default_on else None

    def record(self, event: Event) -> None:
        """钩子回调：追加一行 JSONL。绝不抛异常（Hook.trigger 已兜底）。"""
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False)
            with self._lock:
                if not self._dir_ready:
                    parent = os.path.dirname(os.path.abspath(self.path))
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    self._dir_ready = True
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:  # pragma: no cover - 审计失败不阻断工具流
            print(f"[tool_guard] 审计账本写入失败（忽略）: {e}", file=sys.stderr)


def attach_audit_log(hooks: Any, audit: Optional[AuditLog] = None, *,
                     env: Optional[Dict[str, str]] = None,
                     default_on: bool = False) -> Optional[AuditLog]:
    """把审计账本挂成通配符观测者；未开启返回 None（不留空账本）。

    显式注入优先；未注入则按 ``MBDSDR_AUDIT_LOG`` 构造。两个面（MCP 的
    ``MCPToolSet`` 与进程内的 ``ToolRegistry``）都走这一个挂载函数，所以
    落点只有一处，不存在「某个入口忘了接观测者」的分叉。
    """
    audit = (audit if audit is not None
             else AuditLog.from_env(env=env, default_on=default_on))
    if audit is not None:
        hooks.register("*", audit.record, description="audit_log")
    return audit
