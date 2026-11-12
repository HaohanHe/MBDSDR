#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""提交后 hunk 残留检查：某提交新增的行，不得以删除形态回到工作区的未提交 diff。

**这是事后脚本，不是钩子**：判据要在任意历史 commit 上可复跑、能在临时 worktree
里重放出红/绿，才够格强制；钩子只能看 HEAD 一笔，批量补查仍要人跑。理由与门槛
见文末「为什么先不做钩子」。

事故形态（`840d10ef`，2026-11-11）：docs 类共享文件走 hunk 级提交——提交树里
是「当时的 HEAD blob + 我这笔 hunk」，工作区里那份草稿却是各人往同一文件追加
的旧底稿。hunk 写进了提交树、却没有同步回工作区，于是工作区相对 HEAD 永久少
一段；下一次谁整文件提交（paths 覆盖 index / 私有 index 基于旧快照），这段就
从提交树里消失，而 `git log -S` 对丢失文本零命中——查不到「谁删的」。

判据只有一个：
  对提交 C 改动的每个文件 P，取 C 在 P 上**新增的连续行块** A；
  取当前工作区相对 `HEAD` 的未提交 diff 里 P 的**删除行块** D；
  若 D 中有一段 ≥2 行、且逐字等于某个 A 的连续子序列 → 判 RESIDUE。
  含义就是：提交把这段写进了库，工作区却没有它。

为什么不是「文件 dirty 就报」：
  共享工作区里别人未提交的 hunk 是常态。判据必须落在**内容**上——只有工作区
  删掉的那几行与本次提交新增的几行逐字相同，才算残留。别人在同一文件、甚至
  相邻 hunk 里的改动不会命中。

已知边界（明确说做不到）：
  * 只查**删除侧**。提交新增行若以 `+` 出现在工作区 diff（HEAD 后来被别人回退、
    工作区又补回），那是重新引入，不是本形态，不报。
  * 工作区整文件被删时不判：无法与「有意删除整份文件」区分，逐条 SKIP。
  * merge commit 不判：合并引入的行不归属单笔 hunk。
  * 两侧都先把 CRLF 归一成 LF 再比；不忽略空白差异（缩进变了就不算逐字相同）。
  * 只认 ≥2 行的连续块，且块内至少 1 行非空。提交只新增 1 行、工作区丢了这
    1 行不会命中——这是精度换召回，宁漏不误（单行 / 纯空行块误报率太高）。

为什么先不做钩子：
  判据要能对任意历史 commit 复跑、能在临时 worktree 里重放出红/绿，才谈得上
  强制。上一道 DIRTY 闸门的教训是判据没验干净就上强制，最后把人逼去绕。
  钩子版本等这批反证与边界用例跑稳之后再议。

用法：
  python scripts/check_commit_residue.py <commit> [<commit> ...]
  python scripts/check_commit_residue.py --range A..B
  python scripts/check_commit_residue.py --repo <另一份工作树> <commit>
  python scripts/check_commit_residue.py --json <commit>

  `--repo` 用来对**别的**工作树做判定（临时 worktree 重放反证就靠它）：
  提交内容取自该仓库的对象库，工作区 diff 也取该仓库的 `HEAD` 与工作区。

退出码：0 干净；1 有残留；2 用法/git 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

#: 命中所需的最短连续逐字相同行数。低于这个值（单行、纯空行）误报率过高。
MIN_RUN = 2

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git(repo, *args):
    """跑一条 git 命令，返回 CompletedProcess（不抛异常，调用方看 returncode）。"""
    return subprocess.run(
        ["git", "-C", repo] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_ok(repo, *args):
    proc = git(repo, *args)
    if proc.returncode != 0:
        raise RuntimeError(
            "git %s 失败（rc=%d）：%s"
            % (" ".join(args), proc.returncode, proc.stderr.strip())
        )
    return proc.stdout


def resolve_repo(path):
    proc = git(path, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise RuntimeError("不是 git 仓库：%s（%s）" % (path, proc.stderr.strip()))
    return proc.stdout.strip()


def _line(raw):
    """归一化 diff 里的一行内容：去掉行首标记，去掉 CRLF 的 CR。"""
    return raw[1:].rstrip("\r")


def parse_blocks(diff_text, side):
    """把 `-U0` 统一 diff 按 hunk 切成连续行块。

    side='+' 返回新增块（起始行取新文件行号），side='-' 返回删除块（取旧文件
    行号）。`-U0` 下每个 hunk 里同一侧的行天然连续，因此一个 hunk 就是一个块。
    """
    blocks = []
    current = None
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            continue
        m = _HUNK_RE.match(raw)
        if m:
            old_start = int(m.group(1))
            new_start = int(m.group(3))
            current = {
                "start": new_start if side == "+" else old_start,
                "lines": [],
            }
            blocks.append(current)
            continue
        if current is None or raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith(side):
            current["lines"].append(_line(raw))
        elif raw.startswith(" "):
            # 理论上 -U0 不产生上下文行；真出现就断开，避免跨块拼接。
            current = None
    return [b for b in blocks if b["lines"]]


def longest_common_run(d_lines, a_lines):
    """返回 (长度, D 起点下标, A 起点下标)：D 与 A 的最长连续逐字相同段。"""
    n, m = len(d_lines), len(a_lines)
    if n == 0 or m == 0:
        return 0, 0, 0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    best = (0, 0, 0)
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            if d_lines[i] == a_lines[j]:
                length = nxt[j + 1] + 1
                row[j] = length
                if length > best[0]:
                    best = (length, i, j)
    return best


def _non_blank(lines):
    return sum(1 for ln in lines if ln.strip())


def commit_paths(repo, commit, base):
    """本次提交改动的文件（跳过删除、改名按新路径；root commit 也能列）。"""
    if base:
        out = git_ok(repo, "diff", "--name-status", "--no-renames", base, commit)
    else:
        out = git_ok(
            repo, "diff-tree", "-r", "--root", "--no-renames",
            "--no-commit-id", "--name-status", commit,
        )
    paths = []
    for raw in out.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        status, path = parts[0], parts[-1]
        if status.startswith("D"):
            continue
        paths.append((status, path))
    return paths


def commit_added_blocks(repo, commit, base, path):
    if base:
        diff = git_ok(repo, "diff", "--unified=0", "--no-color", base, commit, "--", path)
    else:
        diff = git_ok(repo, "show", "--format=", "--unified=0", "--no-color", commit, "--", path)
    return parse_blocks(diff, "+")


def worktree_del_blocks(repo, path):
    if not os.path.exists(os.path.join(repo, path)):
        return None, "工作区没有该文件（整文件删除不判）"
    proc = git(repo, "diff", "--unified=0", "--no-color", "HEAD", "--", path)
    if proc.returncode != 0:
        return None, "git diff HEAD 失败：%s" % proc.stderr.strip()
    if not proc.stdout.strip():
        return [], None
    if "new file mode" in proc.stdout:
        return [], None
    return parse_blocks(proc.stdout, "-"), None


def check_commit(repo, commit):
    """返回 (records, notes)。records 为空即干净。"""
    parents = git_ok(repo, "rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) > 2:
        return [], ["%s：merge commit（%d 个父提交），不判" % (commit[:12], len(parents) - 1)]
    base = parents[1] if len(parents) == 2 else None

    records, notes = [], []
    for status, path in commit_paths(repo, commit, base):
        added = commit_added_blocks(repo, commit, base, path)
        if not added:
            continue
        dels, note = worktree_del_blocks(repo, path)
        if note:
            notes.append("%s：%s" % (path, note))
            continue
        for d in dels:
            best_len, di, ai = 0, 0, 0
            for a in added:
                length, i, j = longest_common_run(d["lines"], a["lines"])
                if length > best_len:
                    best_len, di, ai = length, i, j
            if best_len < MIN_RUN:
                continue
            matched = d["lines"][di:di + best_len]
            if _non_blank(matched) == 0:
                continue
            records.append({
                "commit": commit,
                "path": path,
                "worktree_line": d["start"] + di,
                "matched_lines": best_len,
                "sample": matched[:8],
            })
    return records, notes


def _print_text(commit, records, notes):
    print("commit %s" % commit)
    if not records:
        print("  OK  本次提交新增的行没有以删除形态回到工作区未提交 diff")
    for n in notes:
        print("  SKIP %s" % n)
    for r in records:
        print("  RESIDUE %s" % r["path"])
        print(
            "    工作区未提交 diff 第 %d 行起删掉 %d 行，与本次提交新增块逐字相同"
            % (r["worktree_line"], r["matched_lines"])
        )
        for line in r["sample"]:
            print("      - %s" % line)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="检查提交新增的行是否又以删除形态回到工作区未提交 diff",
    )
    ap.add_argument("commits", nargs="*", help="要检查的 commit（可多个）")
    ap.add_argument("--range", dest="rev_range", help="提交范围，如 A..B（含 B 不含 A）")
    ap.add_argument("--repo", default=".", help="仓库/工作树路径（默认当前目录）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    try:
        repo = resolve_repo(args.repo)
        commits = list(args.commits)
        if args.rev_range:
            commits += git_ok(repo, "rev-list", "--reverse", args.rev_range).split()
        if not commits:
            ap.error("至少要给一个 commit，或用 --range")

        all_records, all_notes, per_commit = [], [], []
        for commit in commits:
            records, notes = check_commit(repo, commit)
            all_records += records
            all_notes += ["%s %s" % (commit[:12], n) for n in notes]
            per_commit.append({"commit": commit, "records": records, "notes": notes})
            if not args.json:
                _print_text(commit, records, notes)
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "repo": repo,
            "commits": per_commit,
            "residue": len(all_records),
            "notes": all_notes,
        }, ensure_ascii=False, indent=2))
    else:
        print()
        if all_records:
            print("RESULT: %d 处残留（%d 个提交）" % (len(all_records), len(per_commit)))
        else:
            print("RESULT: 干净（%d 个提交）" % len(per_commit))
    return 1 if all_records else 0


if __name__ == "__main__":
    sys.exit(main())
