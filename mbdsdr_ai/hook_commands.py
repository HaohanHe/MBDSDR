# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 外部命令 hooks（external hooks）
=================================================

配置驱动的「本地命令在生命周期关键点否决工具调用」：对齐 kimi-code
externalHooks 的可否决语义（stdin 传事件 JSON、exit 2 或 stdout JSON
deny 即拦截），MBDSDR 取安全子集。

配置文件：``~/.mbdsdr/hooks.json``
----------------------------------
默认位置 ``~/.mbdsdr/hooks.json``，可用 ``MBDSDR_HOOKS_CONFIG`` 环境变量
覆盖；构造 :class:`HookCommandRunner` 时显式传 ``path`` 优先于环境变量
（与 ``tool_policy`` 的 ``MBDSDR_TOOL_POLICY`` 同约定）：

.. code-block:: json

    {"hooks": [{"event": "agent.pre_tool_use",
                "command": ["python", "check.py"],
                "timeout_s": 30}]}

语义
----
* **事件匹配**：每条规则按 ``event`` 字段匹配；当前仅支持
  ``"agent.pre_tool_use"``，未知 event 名诚实跳过并在 stderr 明说；
* **执行**：``subprocess``（``shell=False``），命令数组原样 execve，
  事件 JSON 经 stdin 以 UTF-8 传入，stdout 回读；
* **否决判定**：退出码 == 2（kimi-code 约定），或 stdout JSON 含
  ``{"decision": "deny", "reason"/"message": ...}``；
* **超时**（默认 30s、可按 ``timeout_s`` 配置）：杀进程、stderr 留痕、
  **按放行处理**——观测失败不阻断主流程，与进程内 ``Hook.trigger`` 的
  异常兜底一致（fail-open）；
* **命令不存在 / 启动失败 / 其他非零退出码**：stderr 留痕、放行；
* **懒加载 + mtime 缓存**：首次 ``decide`` 前读配置，配置 mtime 变化才
  重载（复用 skill_registry 的缓存思路，不做 inotify）。

威胁模型（必读）
----------------
* **配置文件属于用户本人的信任面**：``hooks.json`` 里的命令以调用方
  （MBDSDR 进程自身）的同等权限执行——能跑进程内 hook 的一切都能做。
  不要把来路不明的 hooks 配置装进来，那等同于把 shell 交出去。
* 本模块刻意**不引入 ``shell=True``**（命令数组不经过任何命令解释器）、
  **不做任何参数插值**（事件数据只经 stdin JSON 传递，绝不拼进命令行），
  以回避命令行注入面。
* fail-open 是权衡而非疏漏：外部命令 hook 是策略/观测面，其自身故障
  （超时、崩溃、依赖缺失）不应把接收机工具调用全数冻结。需要硬保证的
  场景应落在进程内 hook 或 tool_policy deny 规则（同为本地信任面）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

#: 覆盖配置路径的环境变量（对齐 tool_policy.POLICY_ENV 约定）。
HOOKS_ENV = "MBDSDR_HOOKS_CONFIG"
#: 配置文件默认位置（``~`` 会 expanduser）。
DEFAULT_HOOKS_PATH = "~/.mbdsdr/hooks.json"
#: 默认超时（秒）：与 kimi-code externalHooks 的 30s 对齐。
DEFAULT_TIMEOUT_S = 30.0
#: 当前支持的事件名（外部命令 hooks 子集）：工具调用前可否决。
SUPPORTED_EVENTS: Tuple[str, ...] = ("agent.pre_tool_use",)
#: 否决退出码（kimi-code 约定）：进程以该退出码结束即否决本次调用。
VETO_EXIT_CODE = 2


def _event_type_of(event: Any) -> str:
    """取事件的裸 event_type 串（解出 str-Enum 的值）。"""
    et = event.get("event_type") if isinstance(event, dict) \
        else getattr(event, "event_type", "")
    return str(getattr(et, "value", et))


def _event_payload(event: Any) -> Dict[str, Any]:
    """事件 -> 传入子进程 stdin 的 JSON 载荷（对齐 hooks.Event.to_dict）。"""
    if isinstance(event, dict):
        return event
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {"event_type": _event_type_of(event),
            "data": getattr(event, "data", {})}


def _deny_from_stdout(stdout: bytes) -> Optional[Dict[str, Any]]:
    """stdout JSON 含 ``{"decision": "deny", "reason"/"message"}`` 即否决。

    非 JSON / 非 dict / decision 非 deny 一律 None（exit 2 才是强信号，
    杂散 stdout 不应当拦）。
    """
    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and \
            str(data.get("decision", "")).lower() == "deny":
        return {"reason": data.get("reason") or data.get("message")
                or "hook_denied"}
    return None


def _validate_hooks(rules: Any, source: str) -> List[Dict[str, Any]]:
    """校验 hooks 数组；坏条目 / 未知 event 诚实跳过并在 stderr 明说。"""
    out: List[Dict[str, Any]] = []
    if rules is None:
        return out  # 无 hooks 键：空配置合法（= 无外部 hook）
    if not isinstance(rules, list):
        print("hook_commands: %s 的 hooks 必须是数组，本轮无外部 hook"
              % source, file=sys.stderr)
        return out
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            print("hook_commands: %s 第 %d 条 hook 非对象，已跳过"
                  % (source, i), file=sys.stderr)
            continue
        event = str(r.get("event", ""))
        if event not in SUPPORTED_EVENTS:
            print("hook_commands: %s 第 %d 条 event %r 暂不支持"
                  "（当前支持 %s），已跳过"
                  % (source, i, event, "/".join(SUPPORTED_EVENTS)),
                  file=sys.stderr)
            continue
        cmd = r.get("command")
        if not isinstance(cmd, list) or not cmd or \
                not all(isinstance(c, str) for c in cmd):
            print("hook_commands: %s 第 %d 条 command 必须是非空字符串"
                  "数组（shell=False 原样执行），已跳过" % (source, i),
                  file=sys.stderr)
            continue
        timeout = r.get("timeout_s", DEFAULT_TIMEOUT_S)
        try:
            timeout_f = float(timeout)
            if timeout_f <= 0:
                raise ValueError("timeout_s 必须为正")
        except (TypeError, ValueError):
            print("hook_commands: %s 第 %d 条 timeout_s %r 非法，回退默认"
                  " %ss" % (source, i, r.get("timeout_s"),
                            DEFAULT_TIMEOUT_S), file=sys.stderr)
            timeout_f = DEFAULT_TIMEOUT_S
        out.append({"event": event, "command": list(cmd),
                    "timeout_s": timeout_f})
    return out


class HookCommandRunner:
    """外部命令 hooks 执行器。

    对外 API：``decide(event) -> Optional[{"reason": ...}]``——
    ``None`` = 放行；``{"reason": ...}`` = 否决（附理由）。
    ``event`` 可为 ``mbdsdr_ai.hooks.Event`` 或带 ``event_type``/``data``
    键的 dict（便于测试与内部直接构造）。

    参数
    ----
    path : Optional[str]
        配置文件路径；``None`` 时依次尝试 ``MBDSDR_HOOKS_CONFIG``
        环境变量、默认路径 ``~/.mbdsdr/hooks.json``。显式传 ``path``
        优先于环境变量。路径不存在 = 空转（无外部 hook，不报错）。

    配置懒加载：首次 ``decide`` 时读盘，之后仅当配置 mtime 变化才重载
    （复用 skill_registry 的 mtime 缓存思路；已知边界：父目录级原子
    替换之外的粗暴场景不在覆盖范围，不过度设计 inotify）。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._explicit_path = path
        # 缓存：(mtime 指纹 or None, 规则列表)；None = 配置缺失
        self._cache: Optional[Tuple[Optional[int], List[Dict[str, Any]]]] = None

    # ------------------------------------------------------------------
    # 配置加载（懒加载 + mtime 缓存）
    # ------------------------------------------------------------------
    def _config_path(self) -> str:
        p = (self._explicit_path or os.environ.get(HOOKS_ENV)
             or DEFAULT_HOOKS_PATH)
        return os.path.expanduser(p)

    @staticmethod
    def _config_mtime(path: str) -> Optional[int]:
        """配置 mtime 指纹（ns 精度）；文件缺失返回 None。"""
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return None

    def _load_rules(self) -> List[Dict[str, Any]]:
        path = self._config_path()
        stamp = self._config_mtime(path)
        if self._cache is not None and self._cache[0] == stamp:
            return self._cache[1]  # 命中缓存：不重读
        if stamp is None:
            rules: List[Dict[str, Any]] = []  # 缺失 = 空转，不制造噪声
        else:
            rules = self._read_rules(path)
        self._cache = (stamp, rules)
        return rules

    def _read_rules(self, path: str) -> List[Dict[str, Any]]:
        try:
            # utf-8-sig：透明剥离子常见 Windows 编辑器写出的 UTF-8 BOM
            # （纯 UTF-8 无 BOM 文件读法不变），BOM 不该让整份配置失效
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print("hook_commands: 读取 %s 失败（%s），本轮无外部 hook"
                  % (path, e), file=sys.stderr)
            return []
        hooks = data.get("hooks") if isinstance(data, dict) else None
        return _validate_hooks(hooks, path)

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def decide(self, event: Any) -> Optional[Dict[str, Any]]:
        """对某事件跑所有匹配的外部命令 hook；返回首个否决或 None（放行）。

        自身永远不抛：配置读取 / 命令执行的一切失败都降级为放行 +
        stderr 留痕（观测面绝不当主流程单点）。
        """
        try:
            rules = self._load_rules()
        except Exception as e:  # pragma: no cover - 防御
            print("hook_commands: 配置加载异常（放行）: %s" % e,
                  file=sys.stderr)
            return None
        event_type = _event_type_of(event)
        for rule in rules:
            if rule["event"] != event_type:
                continue
            verdict = _run_command(rule["command"], event, rule["timeout_s"])
            if verdict is not None:
                return verdict
        return None


def _run_command(cmd: List[str], event: Any,
                 timeout_s: float) -> Optional[Dict[str, Any]]:
    """执行单条命令 hook；None = 放行，{"reason": ...} = 否决。

    shell=False：``cmd`` 为可执行文件 + 参数列表，不经过 shell，也不做
    任何参数插值（事件数据只走 stdin）。失败一律放行 + stderr 留痕：
      * 启动失败（命令不存在等）：放行；
      * 超时（subprocess.run 已杀进程）：放行；
      * 其他非零退出码：放行；
      * 退出码 == VETO_EXIT_CODE（2）：否决；
      * 退出码 0 且 stdout JSON deny：否决。
    """
    payload = json.dumps(_event_payload(event),
                         ensure_ascii=False, default=str).encode("utf-8")
    try:
        proc = subprocess.run(  # noqa: S603 - 本地信任配置、shell=False
            cmd, input=payload, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("hook_commands: %s 超时（>%ss），已杀进程并按放行处理"
              % (" ".join(cmd), timeout_s), file=sys.stderr)
        return None
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print("hook_commands: 启动 %s 失败（%s），按放行处理"
              % (cmd, e), file=sys.stderr)
        return None
    except Exception as e:  # pragma: no cover - 防御
        print("hook_commands: 执行 %s 异常（%s），按放行处理"
              % (cmd, e), file=sys.stderr)
        return None

    if proc.returncode == VETO_EXIT_CODE:
        parsed = _deny_from_stdout(proc.stdout)
        return {"reason": (parsed or {}).get("reason")
                or "hook_command_exit_%d" % VETO_EXIT_CODE}
    if proc.returncode != 0:
        print("hook_commands: %s 退出码 %d（非 0/2），按放行处理"
              % (" ".join(cmd), proc.returncode), file=sys.stderr)
        return None
    return _deny_from_stdout(proc.stdout)
