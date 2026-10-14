# SPDX-License-Identifier: MIT
"""工具权限规则集：把二值 arm_manual 门升级为 allow/ask/deny 三级策略。

来源（2026-10-14 上游审计，kimi-code 与 kilocode 独立收敛的第一落点）：
- kimi-code（MoonshotAI/kimi-code）：permissionPolicyService 有序政策链 +
  危险命令问询 + 会话级批准；
- kilocode（Kilo-Org/kilocode）：permission 规则表 {permission, pattern,
  action} + once/always/reject + deny 从工具视野隐藏。
MBDSDR 保留自己的红线：结果信封诚实、绝不伪造执行结果——规则集只决定
「要不要问 / 能不能执行」，不改变信封语义。

规则解析顺序（先命中先返回）：
  1. 精确工具名规则（allow/ask/deny）
  2. fnmatch 通配模式（如 "set_*"）
  3. 默认策略：写工具 ask、读工具 allow
deny 拥有最高优先级：即使调用方带 arm_manual=True 也不执行（对齐
kilocode hard ruleset veto）。
会话批准缓存：调用带 arm_manual=True 且 approve_session=True 时，按
(工具名, 规范化参数) 记入本会话免问名单；后续同键调用自动放行并在结果
标注 approval="session"（可观测，不隐形）。
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

ACTIONS: Tuple[str, ...] = ("allow", "ask", "deny")

# 配置文件（可选）：~/.mbdsdr/tool_policy.json
# {"rules": [{"pattern": "shutdown", "action": "deny"},
#            {"pattern": "set_*", "action": "allow"}]}
POLICY_ENV = "MBDSDR_TOOL_POLICY"
DEFAULT_POLICY_PATH = "~/.mbdsdr/tool_policy.json"


def normalize_key(name: str, args: Dict[str, Any]) -> Tuple:
    """(工具名, 规范化参数) -> 可哈希的会话缓存键（参数顺序无关）。"""
    return (name, tuple(sorted((k, repr(v)) for k, v in args.items())))


def _validate_rules(rules: Any, source: str) -> List[Dict[str, str]]:
    """校验规则列表；坏条目跳过并在 stderr 明说（不静默吞）。"""
    out: List[Dict[str, str]] = []
    if not isinstance(rules, list):
        print(f"tool_policy: {source} 的 rules 必须是数组，已忽略", file=sys.stderr)
        return out
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or "pattern" not in r or "action" not in r:
            print(f"tool_policy: {source} 第 {i} 条规则缺 pattern/action，已跳过",
                  file=sys.stderr)
            continue
        action = str(r["action"]).lower()
        if action not in ACTIONS:
            print(f"tool_policy: {source} 第 {i} 条 action {r['action']!r} "
                  f"不在 {ACTIONS}，已跳过", file=sys.stderr)
            continue
        out.append({"pattern": str(r["pattern"]), "action": action})
    return out


class ToolPolicy:
    """工具权限规则集（纯逻辑，无 IO 依赖；IO 走 load()）。"""

    def __init__(self,
                 rules: Optional[Iterable[Dict[str, str]]] = None,
                 default_write: str = "ask",
                 default_read: str = "allow") -> None:
        if default_write not in ACTIONS or default_read not in ACTIONS:
            raise ValueError(f"默认策略必须在 {ACTIONS} 内")
        self.rules: List[Dict[str, str]] = [
            {"pattern": str(r["pattern"]),
             "action": str(r["action"]).lower()}
            for r in (rules or [])]
        self.default_write = default_write
        self.default_read = default_read

    # -- 构造 ---------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: str = "config") -> "ToolPolicy":
        """从配置 dict 构造（坏条目诚实跳过，不静默吞）。"""
        if not isinstance(data, dict):
            return cls()
        rules = _validate_rules(data.get("rules"), source)
        dw = str(data.get("default_write", "ask")).lower()
        dr = str(data.get("default_read", "allow")).lower()
        if dw not in ACTIONS or dr not in ACTIONS:
            print(f"tool_policy: {source} 的默认策略非法，回退 ask/allow",
                  file=sys.stderr)
            dw, dr = "ask", "allow"
        return cls(rules, dw, dr)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ToolPolicy":
        """从配置文件加载；缺失/坏 JSON -> 默认策略（写 ask 读 allow）。

         Honors MBDSDR_TOOL_POLICY env override. 绝不因为配置问题崩溃或
         伪造策略——坏配置回默认并在 stderr 说明。
        """
        p = os.environ.get(POLICY_ENV) or path or DEFAULT_POLICY_PATH
        p = os.path.expanduser(p)
        if not os.path.isfile(p):
            return cls()
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"tool_policy: 读取 {p} 失败（{e}），回退默认策略",
                  file=sys.stderr)
            return cls()
        return cls.from_dict(data, source=p)

    # -- 判定 ---------------------------------------------------------------
    def action_for(self, name: str, is_write: bool) -> str:
        """解析工具动作：deny > 精确 > 通配 > 默认。"""
        action = self.default_write if is_write else self.default_read
        for r in self.rules:  # 通配在前无所谓：精确必然也命中同 action
            if r["pattern"] == name:
                return r["action"]
        for r in self.rules:
            if r["pattern"] != name and fnmatch.fnmatchcase(name, r["pattern"]):
                return r["action"]
        return action

    def is_denied(self, name: str) -> bool:
        return self.action_for(name, True) == "deny"
