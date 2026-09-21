"""MBDSDR 技能（Skills）注册表。

借鉴 DeepSeek harness 的 skill 子系统：
- 技能是纯 Markdown：``skills/<name>/SKILL.md`` 或 ``skills/<name>.md``；
- YAML frontmatter 至少含 ``name`` 和 ``description``；
- 模型启动时只看到 name+description 目录（不加载正文，省 token）；
- 需要时调 ``skill_load(name)`` 才读完整正文——按需加载、不堆上下文；
- 自进化复利：往 ``skills/`` 丢一个新目录就是新技能，下次自动被发现。

这正是"技能由对话触发而非固定按钮"的载体：找干扰源、卫星跟踪、
SSTV 解码指引都是技能，模型按 description 自己决定何时加载。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Skill:
    name: str
    description: str
    path: str
    when_to_use: str = ""

    def to_summary(self) -> Dict[str, str]:
        return {"name": self.name, "description": self.description,
                "when_to_use": self.when_to_use}


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
    """扫描技能目录，提供目录与按需加载。"""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self._cache: Dict[str, Skill] = {}

    def _scan(self) -> None:
        self._cache = {}
        if not os.path.isdir(self.skills_dir):
            return
        # 目录包：<name>/SKILL.md
        for name in sorted(os.listdir(self.skills_dir)):
            d = os.path.join(self.skills_dir, name)
            if os.path.isdir(d):
                sf = os.path.join(d, "SKILL.md")
                if os.path.isfile(sf):
                    self._add_from_file(name, sf)
            elif name.endswith(".md"):
                self._add_from_file(name[:-3], os.path.join(self.skills_dir, name))

    def _add_from_file(self, name: str, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return
        meta, body = _parse_frontmatter(text)
        desc = meta.get("description", body.split("\n")[0].lstrip("# ").strip())
        self._cache[name] = Skill(
            name=meta.get("name", name),
            description=desc,
            path=path,
            when_to_use=meta.get("when_to_use", ""),
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
        return body.strip()
