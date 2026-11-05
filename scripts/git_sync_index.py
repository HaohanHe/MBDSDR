#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""把共享 index 中「本次提交的路径」同步到当前 HEAD（私有 index 提交后的必备收尾）。

用法：
  python scripts/git_sync_index.py --paths <p1> <p2> ...   # 只同步这些路径（默认用法）
  python scripts/git_sync_index.py --all                   # 全量 `git read-tree HEAD`
  python scripts/git_sync_index.py --check                 # 只校验，不改 index

为什么需要它：用私有 `GIT_INDEX_FILE` 提交后，共享 index 仍停在旧 HEAD，
本次提交的每个文件都会显示成「反向暂存」（相对新 HEAD 看是回退），
条目数 = 本次提交的文件数。不清掉，下一道闸「`git diff --cached` 必须为空」
天天报红，最后会被当成噪音忽略。

为什么默认只按路径同步、而不是无脑 `git read-tree HEAD`：共享 index 里可能
有**别线正在准备的暂存内容**，全量 read-tree 会把对方的提交意图抹掉（§6.9.2
禁止）。按路径用 `git update-index --cacheinfo` 精确改写自己那几条，
别人的条目一个不动；`--all` 只在确认共享 index 为空时才用。

判定「共享 index 干净」的唯一口径（两条都要）：
  1) `git diff --cached --name-only` 为空；
  2) `git ls-files -s` 与 `git ls-tree -r HEAD` 逐条比对，模式与哈希一致、条目数一致。

退出码：0 = 干净；1 = 同步后仍不干净（打印差异清单）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _git_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"不在 git 仓库内：{proc.stderr.strip()}")
    return Path(proc.stdout.strip())


def _git(root: Path, *args: str) -> str:
    env = os.environ.copy()
    # 关键：调用方可能刚用完私有 index（post-commit 会继承 GIT_INDEX_FILE），
    # 这里一律剥掉，确保操作的是共享 index，而不是那份临时快照。
    env.pop("GIT_INDEX_FILE", None)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def _index_vs_head(root: Path):
    """返回 (staged 路径, 哈希/模式不一致的路径, index 条目数, HEAD 条目数)。"""
    staged = [x for x in _git(root, "diff", "--cached", "--name-only").splitlines() if x.strip()]

    index = {}
    for line in _git(root, "ls-files", "-s").splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode, sha, stage = meta.split()
        index[path] = (mode, sha, stage)

    tree = {}
    for line in _git(root, "ls-tree", "-r", "HEAD").splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode, _type, sha = meta.split()
        tree[path] = (mode, sha)

    diff = []
    for path, (mode, sha, stage) in index.items():
        if path not in tree:
            diff.append((path, "NOT-IN-HEAD", sha))
        elif tree[path][1] != sha or tree[path][0] != mode:
            diff.append((path, f"{mode} {sha[:8]} != HEAD {tree[path][0]} {tree[path][1][:8]}", sha))
    for path in tree:
        if path not in index:
            diff.append((path, "IN-HEAD-NOT-IN-INDEX", ""))
    return staged, diff, len(index), len(tree)


def _sync_paths(root: Path, paths) -> int:
    """只改写共享 index 中这些路径的条目；别人的条目一个不动。"""
    touched = 0
    for path in paths:
        out = _git(root, "ls-tree", "HEAD", "--", path).strip()
        if not out:
            # HEAD 里没有（本次是删除）：把该条目从 index 移除
            if _git(root, "ls-files", "--", path).strip():
                _git(root, "update-index", "--force-remove", "--", path)
                touched += 1
            continue
        meta, name = out.split("\t", 1)
        mode, _type, sha = meta.split()
        cur = _git(root, "ls-files", "-s", "--", name).strip()
        if cur:
            cur_meta, _ = cur.split("\t", 1)
            if cur_meta.split()[1] == sha and cur_meta.split()[0] == mode:
                continue
        _git(root, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{name}")
        touched += 1
    return touched


def _report(root: Path, sync_all: bool, paths) -> int:
    if sync_all:
        _git(root, "read-tree", "HEAD")
    elif paths:
        touched = _sync_paths(root, paths)
        print(f"[git-sync-index] 同步路径 {len(paths)} 个，改写条目 {touched} 个")

    staged, diff, n_index, n_head = _index_vs_head(root)
    print(f"[git-sync-index] index={n_index} head={n_head} staged={len(staged)} parity_diff={len(diff)}")
    for path in staged:
        print(f"  STAGED  {path}")
    for path, why, _sha in diff[:50]:
        print(f"  DIFF    {path}  {why}")
    if len(diff) > 50:
        print(f"  ... 另有 {len(diff) - 50} 条")

    if not staged and not diff:
        print("[git-sync-index] 共享 index 与 HEAD 一致。")
        return 0

    own = set(paths)
    own_bad = [p for p in staged if p in own] + [d[0] for d in diff if d[0] in own]
    if paths and not own_bad:
        # 本次提交的路径已全部对齐；剩下的条目属于别人，按 §6.9.2 一律不动。
        others = sorted(set(staged) | {d[0] for d in diff})
        print(
            f"[git-sync-index] 本次路径已对齐；另有 {len(others)} 条他人暂存条目（未动，"
            f"须由 owner 处理）：{', '.join(others[:10])}",
            file=sys.stderr,
        )
        return 2
    print("[git-sync-index] 共享 index 不干净：参见上面的清单。", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="同步/校验共享 git index 与 HEAD 一致")
    parser.add_argument("--paths", nargs="*", default=[], help="只同步这些路径（默认）")
    parser.add_argument("--all", action="store_true", help="全量 git read-tree HEAD")
    parser.add_argument("--check", action="store_true", help="只校验，不执行同步")
    args = parser.parse_args(argv)
    root = _git_root()
    if args.check:
        return _report(root, sync_all=False, paths=[])
    return _report(root, sync_all=args.all, paths=args.paths)


if __name__ == "__main__":
    sys.exit(main())
