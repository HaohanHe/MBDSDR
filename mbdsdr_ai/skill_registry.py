# SPDX-License-Identifier: MIT
"""MBDSDR 技能（Skills）注册表。

借鉴 DeepSeek harness 的 skill 子系统：
- 技能是纯 Markdown：``skills/<name>/SKILL.md`` 或 ``skills/<name>.md``；
- YAML frontmatter 至少含 ``name`` 和 ``description``；
- 模型启动时只看到 name+description 目录（不加载正文，省 token）；
- 需要时调 ``skill_load(name)`` 才读完整正文——按需加载、不堆上下文；
- 自进化复利：往 ``skills/`` 丢一个新目录就是新技能，下次自动被发现。

这正是"技能由对话触发而非固定按钮"的载体：找干扰源、卫星跟踪、
SSTV 解码指引都是技能，模型按 description 自己决定何时加载。

2026-10-14（kimi-code 审计第五落点）三项升级：
- **目录 mtime 缓存**：不再每次 list()/load() 全量 os.listdir 重扫，
  仅当技能根目录 st_mtime 变化（或首次见到该根）才重扫该根；
- **user 根**：新增第二个技能根 ``~/.mbdsdr/skills``（跨项目个人技能，
  对齐 kimi-code 的 user/project 双根），同名技能项目根优先；
- **激活事件**：load() 成功返回时向注入的 HookManager 发一条
  ``agent.tool_call`` 事件（data 含 skill/path/source）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .hooks import Event, EventType

# user 技能根默认位置（可经构造参数 user_dir 覆盖或置 "" 禁用），
# 与 plugin_system 的 ~/.mbdsdr/plugins 同一约定。
DEFAULT_USER_SKILLS_DIR = "~/.mbdsdr/skills"


@dataclass
class Skill:
    name: str
    description: str
    path: str
    when_to_use: str = ""
    source: str = "project"  # 所属技能根：project（项目根）/ user（user 根）

    def to_summary(self) -> Dict[str, str]:
        return {"name": self.name, "description": self.description,
                "when_to_use": self.when_to_use, "source": self.source}


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, m.group(2)


class SkillRegistry:
    """扫描技能目录（项目根 + user 根），提供目录与按需加载。

    参数
    ----
    skills_dir : str
        项目技能根（既有调用方的唯一位置参数，语义不变）。
    user_dir : Optional[str]
        user 技能根；``None``（默认）取 ``~/.mbdsdr/skills``；
        显式传 ``""`` 禁用 user 根（测试/单根部署用）。
    hook_manager : Optional[HookManager]
        注入后 load() 成功时发 ``agent.tool_call`` 激活事件；不注入则静默跳过。
    """

    def __init__(self, skills_dir: str, user_dir: Optional[str] = None,
                 hook_manager=None):
        # expanduser + normpath：Windows 下 "~" 展开后其余段仍带正斜杠，
        # 归一化保证 summary/事件里的路径分隔符一致；空串原样保留（= 禁用），
        # 否则 normpath("") 会变成 "."（当前目录）造成误扫描
        def _norm(p: str) -> str:
            return os.path.normpath(os.path.expanduser(p)) if p else p

        self.skills_dir = _norm(skills_dir)
        # user_dir=None → 默认 user 根；"" → 禁用
        if user_dir is None:
            self.user_dir = _norm(DEFAULT_USER_SKILLS_DIR)
        else:
            self.user_dir = _norm(user_dir)
        self.hook_manager = hook_manager
        # 每个根的 mtime → 该根上次扫描结果；merged _cache 为由各根合并出的视图
        self._root_cache: Dict[str, Tuple[Optional[float], Dict[str, Skill]]] = {}
        self._cache: Dict[str, Skill] = {}
        # 根顺序即优先级：项目根在前，user 根同名技能不覆盖（项目根优先）
        self._roots: List[str] = []
        for root in (self.skills_dir, self.user_dir):
            if root and root not in self._roots:
                self._roots.append(root)

    # ------------------------------------------------------------------
    # 扫描（按根 mtime 缓存）
    # ------------------------------------------------------------------
    def _scan(self) -> None:
        # mtime 缓存：仅当某根 st_mtime 变化（或首次见到该根）才重扫该根，
        # 否则复用缓存条目——省掉每次 list()/load() 的全量 listdir。
        # 已知边界（诚实标注，不过度设计 inotify/watchdog）：
        #   * 若目录内容以不影响父目录 mtime 的方式被改动（少见：部分编辑器/
        #     原子替换直接在子条目上操作而不触父目录时间戳），不会触发重扫，
        #     缓存会过期到下次任何影响父目录 mtime 的改动为止；
        #   * 文件系统 mtime 粒度较粗时，同一粒度窗口内的改动可能不改变
        #     st_mtime，同样漏触发。
        # 需要严格热更新的场景后续可换 inotify/watchdog，本轮不做。
        self._cache = {}
        for root in self._roots:
            mtime = self._root_mtime(root)
            cached = self._root_cache.get(root)
            if cached is not None and cached[0] == mtime:
                entries = cached[1]
            else:
                entries = self._scan_root(root)
                self._root_cache[root] = (mtime, entries)
            # 项目根在前先登记；user 根同名技能不得覆盖（setdefault 即优先级）
            for name, sk in entries.items():
                self._cache.setdefault(name, sk)

    @staticmethod
    def _root_mtime(root: str) -> Optional[float]:
        try:
            return os.stat(root).st_mtime
        except OSError:
            return None

    def _scan_root(self, root: str) -> Dict[str, Skill]:
        entries: Dict[str, Skill] = {}
        if not os.path.isdir(root):
            return entries
        source = "project" if root == self.skills_dir else "user"
        # 目录包：<name>/SKILL.md
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(d):
                sf = os.path.join(d, "SKILL.md")
                if os.path.isfile(sf):
                    sk = self._add_from_file(name, sf, source)
                    if sk is not None:
                        entries[name] = sk
            elif name.endswith(".md"):
                sk = self._add_from_file(name[:-3],
                                         os.path.join(root, name), source)
                if sk is not None:
                    entries[name[:-3]] = sk
        return entries

    # ------------------------------------------------------------------
    # 技能读取
    # ------------------------------------------------------------------
    def _add_from_file(self, name: str, path: str,
                       source: str) -> Optional[Skill]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return None
        meta, body = _parse_frontmatter(text)
        desc = meta.get("description", body.split("\n")[0].lstrip("# ").strip())
        return Skill(
            name=meta.get("name", name),
            description=desc,
            path=path,
            when_to_use=meta.get("when_to_use", ""),
            source=source,
        )

    def list(self) -> List[Dict[str, str]]:
        """返回全部技能的 name+description 目录（不含正文）。"""
        self._scan()
        return [s.to_summary() for s in self._cache.values()]

    def load(self, name: str) -> Optional[str]:
        """按需读取某个技能的完整正文。"""
        self._scan()
        sk = self._cache.get(name)
        if not sk:
            return None
        try:
            with open(sk.path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:  # noqa: BLE001
            return f"读取技能 {name} 失败: {e}"
        _, body = _parse_frontmatter(text)
        self._emit_activated(sk)
        return body.strip()

    # ------------------------------------------------------------------
    # 激活事件（HookManager 可选注入，静默兜底）
    # ------------------------------------------------------------------
    def _emit_activated(self, sk: Skill) -> None:
        """load() 成功时发 ``agent.tool_call`` 激活事件。

        未注入 hook_manager / 无订阅 / 触发异常一律静默跳过，绝不影响主流程
        （可选注入 + except 静默的兜底风格同 plugin_system）。
        """
        if self.hook_manager is None:
            return
        try:
            self.hook_manager.trigger(Event(
                event_type=EventType.AGENT_TOOL_CALL.value,
                data={"skill": sk.name, "path": sk.path, "source": sk.source},
                source="skill_registry",
            ))
        except Exception:  # noqa: BLE001
            pass
