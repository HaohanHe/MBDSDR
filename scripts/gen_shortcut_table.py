# SPDX-License-Identifier: MIT
"""从快捷键注册表生成 `docs/shortcuts.md`（W104：键位表 + 冲突检测）。

唯一事实源是 `desktop/shortcuts.py` 的 `SHORTCUTS`；本脚本只做渲染，不猜键位、
不补条目。生成的表是给人读的成品，测试会校验「表行数 == 注册表条目数」。

用法::

    python scripts/gen_shortcut_table.py            # 写入 docs/shortcuts.md
    python scripts/gen_shortcut_table.py --check    # 只校验是否过期（退出码 1 = 过期）
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from desktop.shortcuts import LOCAL_SCOPES, SHORTCUTS, Shortcut  # noqa: E402

#: 生成物路径固定，测试按同一常量定位
OUT_PATH = os.path.join(_ROOT, "docs", "shortcuts.md")

#: 域标题（id 前缀 → 中文小节名）；未登记的域原样显示

_DOMAIN_TITLES = {
    "file": "文件",
    "view": "视图",
    "planetarium": "星象仪",
    "tools": "工具",
    "help": "帮助",
    "panel": "面板",
    "layout": "布局",
    "vfo": "VFO",
    "spectrum": "频谱",
}

_SCOPE_TITLES = {
    "window": "全局",
    "planetarium": "星象仪形态",
    "spectrum": "频谱获焦",
}


def _cell(text: str) -> str:
    """Markdown 单元格转义：竖线会把表格切坏。"""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _scope_title(scope: str) -> str:
    title = _SCOPE_TITLES.get(scope, scope)
    if scope in LOCAL_SCOPES:
        return f"{title}（局部）"
    return title


def render(entries=SHORTCUTS) -> str:
    """把注册表渲染成完整 Markdown 文本（确定性输出：无时间戳）。"""
    bound = [s for s in entries if (s.key or "").strip()]
    unbound = [s for s in entries if not (s.key or "").strip()]

    lines = [
        "# 快捷键一览（生成物）",
        "",
        "> 本文件由 [`scripts/gen_shortcut_table.py`](../scripts/gen_shortcut_table.py) "
        "从 [`desktop/shortcuts.py`](../desktop/shortcuts.py) 的 `SHORTCUTS` 注册表生成，"
        "**不要手改**。改键位请改注册表后重跑脚本；"
        "`tests/test_shortcut_table.py` 会校验表里条目数与注册表一致，防止表腐烂。",
        ">",
        f"> 共 **{len(entries)}** 条登记：配键 **{len(bound)}** 条、故意不配键 "
        f"**{len(unbound)}** 条（后者在「备注」里写明原因）。",
        ">",
        "> **生效范围**：全局 = 主窗恒生效；星象仪形态 / 频谱获焦 = 局部，"
        "只有该形态或频谱控件自身获焦时才生效（**焦点在哪，局部优先**，"
        "因此允许与全局条目同键）。",
        "> **改键**：「设置 → 快捷键」写的是覆写层，本表只列出厂键位。",
        "",
    ]

    domains = []
    for s in entries:
        domain = s.id.split(".", 1)[0]
        if domain not in domains:
            domains.append(domain)

    for domain in domains:
        group = [s for s in entries if s.id.split(".", 1)[0] == domain]
        lines.append(f"## {_DOMAIN_TITLES.get(domain, domain)}")
        lines.append("")
        lines.append("| 登记 id | 键位 | 动作 | 生效范围 | 备注 |")
        lines.append("|---|---|---|---|---|")
        for s in group:
            key = f"`{s.key}`" if (s.key or "").strip() else "—（不配键）"
            lines.append(
                f"| `{s.id}` | {key} | {_cell(s.label)} | "
                f"{_scope_title(s.scope)} | {_cell(s.note)} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write(path: str = OUT_PATH, entries=SHORTCUTS) -> str:
    text = render(entries)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 docs/shortcuts.md")
    ap.add_argument("--check", action="store_true",
                    help="只校验生成物是否与注册表一致；不一致退出码 1")
    ap.add_argument("--out", default=OUT_PATH, help="输出路径（默认 docs/shortcuts.md）")
    args = ap.parse_args(argv)

    if args.check:
        expected = render()
        try:
            with open(args.out, encoding="utf-8") as fh:
                actual = fh.read()
        except FileNotFoundError:
            print(f"[shortcuts] 缺少生成物：{args.out}（跑 scripts/gen_shortcut_table.py）")
            return 1
        if actual != expected:
            print(f"[shortcuts] 生成物已过期：{args.out}（重跑 scripts/gen_shortcut_table.py）")
            return 1
        print(f"[shortcuts] 生成物与注册表一致（{len(SHORTCUTS)} 条）")
        return 0

    write(args.out)
    print(f"[shortcuts] 已写入 {args.out}（{len(SHORTCUTS)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
