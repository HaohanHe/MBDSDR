#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""提交范围检查：列出某提交改了哪些文件，并提示「该文件在提交时刻是否被多线持有」。

能做到的（真检查）：
  * 从 `git show --name-status` 列出该提交改动的全部文件；
  * 从 `file_claim.py` 的认领台账 `claims.json` 里查每个文件的持有历史：
    若在**提交那一刻**（提交者本地时间，`%ct`）有 ≥2 个不同 owner 的 claim
    处于活跃期（acquired_at ≤ t ≤ released_at），标 `MULTI`；
  * 若只有一个 owner 活跃 → `ONE`；台账里没有记录 → `NONE`；
  * 附「整文件替换」提示：`--numstat` 的新增行数等于该文件新版本总行数时，
    说明这次提交把文件内容整段换掉了（正是 `git commit -- <path>` 的事故形态）。

做不到的（明确说做不到，不造假）：
  * **无法从 `git show` 判断某个 hunk 属于哪条线**。Git 只记录统一 diff，
    不记录「这一行是谁写的」；任何声称能从提交内容反推 hunk 归属的检测都是
    假的。
  * 台账只在 `file_claim.py` 落地之后才开始积累。安装之前的提交（包括
    2026-11-05 的三次事故）一律 `NO_RECORD`，本工具不会假装知道。
  * 提交时刻可能早于台账记录，也可能提交后有人续租——只能按时间区间判断。

用法：
  python scripts/check_commit_scope.py HEAD
  python scripts/check_commit_scope.py <hash> [--json]

退出码：0 无 MULTI；1 发现 MULTI（需人工确认）；2 用法/git 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)
REGISTRY_PATH = LOCKS_DIR / "claims.json"


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
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def _canonical(path: str) -> str:
    return os.path.normcase(path.replace("\\", "/")).lstrip("./")


def _load_registry() -> Dict[str, Any]:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"claims": {}}
    except (OSError, json.JSONDecodeError):
        print(f"WARN 台账不可读：{REGISTRY_PATH}", file=sys.stderr)
        return {"claims": {}}


def _owners_at(entry: Dict[str, Any], moment: float) -> List[str]:
    owners: List[str] = []
    for item in entry.get("history", []):
        start = item.get("acquired_at")
        end = item.get("released_at")
        if not isinstance(start, (int, float)):
            continue
        if start <= moment and (end is None or (isinstance(end, (int, float)) and end >= moment)):
            owner = str(item.get("owner", "?"))
            if owner not in owners:
                owners.append(owner)
    return owners


def _file_lines(sha: str, path: str) -> Optional[int]:
    try:
        text = _git("show", f"{sha}:{path}")
    except SystemExit:
        return None
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _numstat(sha: str) -> Dict[str, tuple]:
    result: Dict[str, tuple] = {}
    for line in _git("show", "--format=", "--numstat", "--no-renames", sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            result[path] = (int(added), int(deleted))
        except ValueError:
            result[path] = (-1, -1)  # 二进制
    return result


def check(ref: str, as_json: bool) -> int:
    sha = _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    meta = _git("show", "-s", "--format=%an%x00%ad%x00%ct%x00%s", "--date=iso", sha).strip()
    author, when, ctime, subject = meta.split("\x00", 3)
    moment = float(ctime)

    statuses: List[Dict[str, Any]] = []
    for line in _git("show", "--format=", "--name-status", "--no-renames", sha).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[-1].strip()
        statuses.append({"status": status, "path": path})

    numstat = _numstat(sha)
    registry = _load_registry().get("claims", {})

    rows: List[Dict[str, Any]] = []
    multi = 0
    for item in statuses:
        path = item["path"]
        entry = registry.get(_canonical(path), {})
        owners = _owners_at(entry, moment) if entry else []
        if len(owners) >= 2:
            verdict = "MULTI"
            multi += 1
        elif owners:
            verdict = "ONE"
        else:
            verdict = "NONE"

        added, _deleted = numstat.get(path, (-1, -1))
        total_new = _file_lines(sha, path) if added >= 0 else None
        whole_rewrite = bool(total_new and added >= total_new)

        rows.append(
            {
                "path": path,
                "status": status,
                "verdict": verdict,
                "owners_at_commit": owners,
                "added": added,
                "whole_rewrite": whole_rewrite,
                "claim_history": len(entry.get("history", [])) if entry else 0,
            }
        )

    if as_json:
        print(
            json.dumps(
                {
                    "commit": sha,
                    "author": author,
                    "date": when,
                    "subject": subject,
                    "multi_claim": multi,
                    "files": rows,
                    "limitations": [
                        "无法从 git show 判断 hunk 归属（git 不记录逐行的作者）",
                        "台账只在 file_claim.py 落地后积累；更早提交一律 NO_RECORD",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if multi else 0

    print(f"COMMIT {sha}")
    print(f"  {when}  {author}  {subject}")
    print(f"  改动文件 {len(rows)} 个；提交时刻多线持有 {multi} 个")
    print()
    print(f"  {'判定':<7}{'状态':<5}{'文件':<46}{'提交时持有者':<24}备注")
    for row in rows:
        owners = ", ".join(row["owners_at_commit"]) if row["owners_at_commit"] else "-"
        note = "整文件替换" if row["whole_rewrite"] else ""
        print(f"  {row['verdict']:<7}{row['status']:<5}{row['path']:<46}{owners:<24}{note}")
    print()
    if multi:
        print("结论：存在 MULTI —— 提交时刻该文件被多条线同时持有，极可能带走了他线在途改动。")
        print("      请人工核对提交内容，不要以「提交成功了」当作归属正确。")
    else:
        print("结论：未发现 MULTI。注意 NONE 不等于「干净」，只等于「无台账记录」。")
    print()
    print("限制（明说）：")
    print("  1. git 不记录 hunk 作者，任何「从提交反推某 hunk 属于哪条线」都做不到，本工具不做。")
    print(f"  2. 台账 {REGISTRY_PATH} 从机制落地起积累；更早的提交无法判定，不伪造结论。")
    return 1 if multi else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="提交范围检查（claim 台账版）")
    parser.add_argument("ref", help="提交 hash / HEAD / 分支")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)
    try:
        return check(args.ref, args.json)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
