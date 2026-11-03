# -*- coding: utf-8 -*-
"""COORDINATION.md 洞察区占位守卫（协调契约测试）.

约束（洞察区 Q&A 硬约束）：
- 「当前在途」「队列」两节标题整行不得改动
- 两节内不得出现空的「####」标题（占位漏删）
- 这是协调机制自身的可靠性测试——用户 2026-11-03 起强制多会话协作模式
  （WorkBuddy × TRAE 翠），唯一协调面是 docs/COORDINATION.md，其结构
  稳定性是分工不乱的前提。纯 stdlib，收集期零依赖。

对应洞察区 Q&A:66（能不能为 COORDINATION.md 结构本身写一条守卫）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COORD = ROOT / "docs" / "COORDINATION.md"

_SECTION_MARKERS = ("## 二、当前在途", "## 三、接下来要办啥")


def _load() -> str:
    assert COORD.is_file(), f"协调台账不存在：{COORD}"
    return COORD.read_text(encoding="utf-8")


def test_coordination_headers_survive():
    """两个工作区标题必须整行在（洞察区 Q&A 硬约束）。

    匹配按**前缀**：标题核心（「## 二、当前在途」/「## 三、接下来要办啥」）
    不得改名，但行尾时间戳后缀（如「（2026-11-03 22:40 更新）」）随例行
    更新变动是合法的。
    """
    text = _load()
    lines = text.splitlines()
    for marker in _SECTION_MARKERS:
        assert any(l.strip().startswith(marker) for l in lines), (
            f"协调台账标题丢失或被改名：{marker!r}"
        )


def test_coordination_no_placeholder_headings():
    """在途/队列两节内不得残留空的 #### 占位标题。"""
    text = _load()
    lines = text.splitlines()
    # 只在两个工作区节内检查（到下一个 ## 为止）
    active = False
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            active = any(s.startswith(m) for m in _SECTION_MARKERS)
            continue
        if active and re.fullmatch(r"#{3,}\s*", s):
            raise AssertionError(
                f"协调台账残留占位标题（洞察区 Q&A 约束）：{line!r}"
            )


def test_coordination_sections_have_content():
    """两节不得整体为空（占位漏删的另一种形态）。"""
    text = _load()
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            current = next((m for m in _SECTION_MARKERS if s.startswith(m)),
                           None)
            if current:
                sections[current] = []
            continue
        if current:
            sections[current].append(s)
    for marker in _SECTION_MARKERS:
        body = [l for l in sections.get(marker, []) if l]
        assert body, f"协调台账节为空（占位漏删）：{marker!r}"
