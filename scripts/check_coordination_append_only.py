#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""§6.1 登记表「只允许增长」检查。

规则（`docs/COORDINATION.md` §6.1.8）：表格行不许删、不许改写；释放时在行末
追加标记 ``〔释放 <时间> <方式>〕``。所以比较前先把释放标记剥掉——**追加标记
必须绿，删行与改写已有行必须红**。

为什么要有这条：2026-11-13 `09712c6f` 删掉了 §6.1 的两行（20:10 登记的
`docs/parameter-unification.md` / `docs/PRD.md`）。表是登记面，删行等于
把「谁在什么时候登记过」一起删掉——事后只能靠 git 考古，且按当前文件行号
根本找不到。允许删除的表不叫登记表，叫便签。

用法::

    python scripts/check_coordination_append_only.py            # HEAD vs 暂存区
    python scripts/check_coordination_append_only.py --worktree # HEAD vs 工作区
    python scripts/check_coordination_append_only.py --rev <sha> [--doc <path>]

退出码：0 只增/未变；1 有删除或改写；2 用法/读取失败。
本脚本只做机械比对，不判断归属、不代为补行。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import List

ROOT = Path(os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1])
DEFAULT_DOC = os.environ.get("MBDSDR_COORD_DOC") or "docs/COORDINATION.md"
SECTION_HEAD = "### 6.1 当前占用"
#: 行末释放标记：`〔释放 2026-11-13 21:05 released〕`（时间 + 方式）
RELEASE_MARK_RE = re.compile(r"〔释放[^〕]*〕")

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2


def table_rows(text: str) -> List[str]:
    """取 §6.1 表格行（含表头与分隔行，保持原文顺序）。

    只靠「以 | 开头」判断边界：表格之后第一个非空非表格行即结束。表格之前的
    说明文字不会被当成表格（2026-11-13 `safe_commit` 也因类似假设踩过坑）。
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith(SECTION_HEAD))
    except StopIteration:
        raise ValueError(f"文档里找不到小节标题：{SECTION_HEAD}")
    rows: List[str] = []
    started = False
    for ln in lines[start + 1:]:
        stripped = ln.strip()
        if stripped.startswith("|"):
            started = True
            rows.append(stripped)
            continue
        if started and stripped:
            break
    return rows


def normalize(row: str) -> str:
    """剥离行末释放标记后再比较：追加标记不算改动。"""
    return RELEASE_MARK_RE.sub("", row).strip()


def removed_rows(base_text: str, target_text: str) -> List[str]:
    """基线里有、现值里没有的行（按多重集计数，重复行也各算一次）。"""
    base = Counter(normalize(r) for r in table_rows(base_text))
    target = Counter(normalize(r) for r in table_rows(target_text))
    return list((base - target).elements())


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout.decode("utf-8", "replace")


def _read_doc(rev: str, index: bool, doc: str) -> str:
    if index:
        return _git("show", f":{doc}")
    return _git("show", f"{rev}:{doc}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§6.1 表格只允许增长")
    parser.add_argument("--doc", default=DEFAULT_DOC, help="协调文档（相对仓库根）")
    parser.add_argument("--rev", default="HEAD", help="基线版本（默认 HEAD）")
    parser.add_argument("--worktree", action="store_true",
                        help="与工作区比较（默认与暂存区比较）")
    args = parser.parse_args(argv)

    doc = args.doc.replace("\\", "/")
    try:
        base_text = _read_doc(args.rev, index=False, doc=doc)
    except (RuntimeError, ValueError) as exc:
        print(f"读取基线失败：{args.rev}:{doc}（{exc}）", file=sys.stderr)
        return EXIT_ERROR

    if args.worktree:
        path = ROOT / doc
        if not path.exists():
            print(f"工作区没有 {doc}", file=sys.stderr)
            return EXIT_ERROR
        target_text = path.read_text(encoding="utf-8")
    else:
        try:
            target_text = _read_doc(args.rev, index=True, doc=doc)
        except RuntimeError:
            # 暂存区里没有这份文档：本笔没动它，不算违规。
            print(f"暂存区无 {doc}：本笔未改动该文件，跳过（0 行缺失）")
            return EXIT_OK

    try:
        missing = removed_rows(base_text, target_text)
    except ValueError as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return EXIT_ERROR

    base_n = len(table_rows(base_text))
    target_n = len(table_rows(target_text))
    if not missing:
        print(f"§6.1 只增检查通过：基线 {base_n} 行 / 现值 {target_n} 行 / 缺失 0 行")
        return EXIT_OK

    print(f"§6.1 只增检查未通过：基线 {base_n} 行 / 现值 {target_n} 行 / "
          f"缺失 {len(missing)} 行")
    for row in missing:
        shown = row if len(row) <= 160 else row[:157] + "..."
        print(f"  - {shown}")
    print("规则：表格行不许删、不许改写；释放请在行末追加「〔释放 <时间> <方式>〕」。")
    return EXIT_VIOLATION


if __name__ == "__main__":
    raise SystemExit(main())
