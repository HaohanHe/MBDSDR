#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""按 blob 判据扫「写回旧 blob」型回退（COORDINATION §6.10.2 的机器化）。

判据（同一路径只在 blob 发生变化的提交上连线取序列）：

    blob[k] == blob[k-2] 且 blob[k-1] != blob[k]

含义：第 k 笔提交把第 k-1 笔的改动写回成了第 k-2 笔的内容。可命中两类
reflog 发现不了的回退：

  * 陈旧 index 写回：提交 A 删掉一段内容，紧接着的提交 B 又把旧版写回
    （`f664638a` 形态，reflog 里没有任何 `reset:` 记录）；
  * 反向 revert：提交 A 把在途文件卷进本笔，之后的 revert 退回
    （`09b53221`→`d8b40eb0` 形态）。

只把「blob 变了」的提交连成序列，是为了不让中间那些没碰该文件的提交
把 A→B→A 的三角关系冲散（`09b53221` 与 `d8b40eb0` 之间就夹着
`69e2b371`）。

区别：`git reset` 会在 reflog 留下 `reset:` 行；本判据不需要 reflog，
只看已入库历史的 blob 三角关系，因此能发现「reset 台账」永远查不到的
这一类。

用法：
  python scripts/find_blob_reverts.py --rev-range 448d465f~1..4132d46f
  python scripts/find_blob_reverts.py --rev-range HEAD~80..HEAD
  python scripts/find_blob_reverts.py --rev-range 09b53221~2..d8b40eb0 \
      --paths scripts/decode_sensitivity.py scripts/decoder_capability_report.py

退出码：0 零命中；1 有命中（便于定期跑）；2 用法/仓库错误。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]

# 共享文档是「陈旧 index 写回」的高发区：多线并发编辑，最容易整段回退。
DEFAULT_PATHS = (
    "docs/COORDINATION.md",
    "docs/parameter-unification.md",
    "docs/limitations-register.md",
    "docs/data-inventory.md",
    "docs/design-basis.md",
    "docs/error-code-visibility.md",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _rev_list(rev_range: str) -> List[str]:
    """按「老 → 新」返回区间内提交（含两端边界语义由 git 决定）。"""
    out = _git("rev-list", "--reverse", rev_range)
    if out.returncode != 0:
        raise SystemExit(f"git rev-list 失败：{out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _blob_at(commit: str, path: str) -> Optional[str]:
    out = _git("rev-parse", "--verify", "--quiet", f"{commit}:{path}")
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _commit_line(commit: str) -> str:
    out = _git("show", "-s", "--format=%h %ci %s", commit)
    return out.stdout.strip() or commit


def find_hits(rev_range: str, paths: List[str]) -> List[Dict[str, str]]:
    commits = _rev_list(rev_range)
    hits: List[Dict[str, str]] = []
    for path in paths:
        seq = []  # [(commit, blob)]，只留在该路径上 blob 真变了的提交
        for commit in commits:
            blob = _blob_at(commit, path)
            if blob is None:
                continue
            if seq and seq[-1][1] == blob:
                continue  # 该提交没碰这个文件，不参与三角关系
            seq.append((commit, blob))
        if len(seq) < 3:
            continue
        for k in range(2, len(seq)):
            restore_commit, restore_blob = seq[k]
            undone_commit, undone_blob = seq[k - 1]
            source_commit, source_blob = seq[k - 2]
            if restore_blob == source_blob and undone_blob != restore_blob:
                hits.append(
                    {
                        "path": path,
                        "source_commit": source_commit,   # k-2：被写回的版本来源
                        "undone_commit": undone_commit,   # k-1：被回退的改动
                        "restore_commit": restore_commit,  # k：写回旧 blob 的提交
                        "undone_line": _commit_line(undone_commit),
                        "restore_line": _commit_line(restore_commit),
                    }
                )
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按 blob 判据扫描写回旧 blob 型回退")
    parser.add_argument(
        "--rev-range",
        default="HEAD~80..HEAD",
        help="提交区间（默认 HEAD~80..HEAD）；可写 A..B 或 A~N..B",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=list(DEFAULT_PATHS),
        help="要扫描的路径（默认 6 个共享文档）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    try:
        hits = find_hits(args.rev_range, list(args.paths))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"rev_range": args.rev_range, "hits": hits}, ensure_ascii=False, indent=2))
    else:
        for h in hits:
            print(f"REVERT {h['path']}")
            print(f"  写回提交 {h['restore_line']}")
            print(f"  被回退   {h['undone_line']}")
            print(f"  回到版本（来源）{h['source_commit']}")
        print(
            f"SUMMARY: range={args.rev_range} paths={len(args.paths)} "
            f"commits={len(_rev_list(args.rev_range))} hits={len(hits)}"
        )
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
