#!/usr/bin/env python3
"""§6.10.2 回退事件表机检：把「事故笔数」从人眼搬到断言上。

断言：
  ① 每笔事故的提交号唯一（重复登记 = 笔数报错）；
  ② 提交号在仓库里真实存在（`git cat-file -e <sha>^{commit}`）；
  ③ 「是否已补回 / 已撤回」列里的修复提交存在；
  ④ 「被卷/被抹文件数」等于修复提交实际改动的文件数；
  ⑤ 被卷数不超过事故提交自身的改动总数。
`未提交` 行（例如被闸门拦下的幻影删除）只核对计数列是整数，不查提交。
`不回退` 行（内容复核无误、只登记不撤回，例如 `86fdbd0b`）只核对 ①②⑤，
不要求「修复提交」存在。

用法：
  python scripts/check_rollback_table.py
  python scripts/check_rollback_table.py --doc <另一个 md>   # 反证用
退出码：0 全过；1 有断言失败；2 表解析失败。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOC = ROOT / "docs" / "COORDINATION.md"
HASH_RE = re.compile(r"`([0-9a-fA-F]{7,40})`")
SECTION = "#### 6.10.2"
HEADER = "| 提交 |"


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def _commit_exists(sha: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def _changed_files(sha: str) -> List[str]:
    return [
        line
        for line in _git("show", "--format=", "--name-only", "--no-renames", sha).splitlines()
        if line.strip()
    ]


def parse_table(text: str) -> List[List[str]]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(SECTION)), None)
    if start is None:
        raise ValueError(f"找不到章节 {SECTION}")
    header_idx = next(
        (j for j in range(start, len(lines)) if lines[j].startswith(HEADER)), None
    )
    if header_idx is None:
        raise ValueError(f"{SECTION} 下找不到表头 {HEADER}")
    rows: List[List[str]] = []
    for line in lines[header_idx + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            raise ValueError(f"列数不是 6：{line}")
        rows.append(cells)
    if not rows:
        raise ValueError("表里没有数据行")
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="§6.10.2 回退事件表机检")
    parser.add_argument("--doc", default=str(DEFAULT_DOC), help="待检 md（默认仓库内 COORDINATION.md）")
    args = parser.parse_args(argv)

    try:
        rows = parse_table(Path(args.doc).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"PARSE FAIL：{exc}")
        return 2

    errors: List[str] = []
    seen: dict = {}
    checked = 0
    skipped = 0

    for cells in rows:
        incident, count_s, _files_cell, _content, fix_cell, _owner = cells
        if not count_s.isdigit():
            errors.append(f"计数列不是整数：{incident!r} -> {count_s!r}")
            continue
        count = int(count_s)

        if "未提交" in incident:
            skipped += 1
            print(f"  SKIP {incident}（未提交，仅核对计数 {count}）")
            continue

        hashes = HASH_RE.findall(incident)
        if len(hashes) != 1:
            errors.append(f"提交列应恰好一个 hash：{incident!r} -> {hashes}")
            continue
        sha = hashes[0]
        if sha.lower() in seen:
            errors.append(f"重复登记：{sha} 已在「{seen[sha.lower()]}」行出现过")
            continue
        seen[sha.lower()] = incident

        if not _commit_exists(sha):
            errors.append(f"提交号不存在于仓库：{sha}")
            continue

        total = len(_changed_files(sha))
        if "不回退" in fix_cell:
            if count > total:
                errors.append(f"被卷数超过原提交改动总数：{sha} 记 {count}，原提交共 {total}")
                continue
            checked += 1
            print(f"  OK {sha} 不回退 被卷={count} 原提交={total}")
            continue

        fix_hashes = HASH_RE.findall(fix_cell)
        if len(fix_hashes) != 1:
            errors.append(f"修复列应恰好一个 hash：{sha} -> {fix_hashes}")
            continue
        fix = fix_hashes[0]
        if not _commit_exists(fix):
            errors.append(f"修复提交不存在：{sha} -> {fix}")
            continue

        fix_files = len(_changed_files(fix))
        if fix_files != count:
            errors.append(f"计数不符：{sha} 记 {count} 个，修复提交 {fix} 实改 {fix_files} 个")
        if count > total:
            errors.append(f"被卷数超过原提交改动总数：{sha} 记 {count}，原提交共 {total}")

        checked += 1
        print(f"  OK {sha} -> {fix} 被卷={count} 撤回文件={fix_files} 原提交={total}")

    if errors:
        print()
        for item in errors:
            print(f"FAIL {item}")
        print(f"\nFAIL rows={len(rows)} checked={checked} skipped={skipped} errors={len(errors)}")
        return 1

    print(f"\nPASS rows={len(rows)} checked={checked} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
