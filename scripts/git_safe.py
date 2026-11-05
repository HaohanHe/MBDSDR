#!/usr/bin/env python3
"""破坏性 git 操作的闸门包装（opt-in，不全局安装钩子、不改 git alias）。

用法：
  python scripts/git_safe.py reset --hard <target>
  python scripts/git_safe.py push --force origin main
  python scripts/git_safe.py <普通 git 子命令...>          # 原样透传

规则：
  - `reset` 带 `--soft` / `--mixed` / `--hard`，或无 pathspec 的裸 reset（no-op 习惯），
    视为破坏性；
  - `push` 带 `-f` / `--force*`，或 refspec 以 `+` 开头，视为强推；
  - 破坏性操作必须带环境变量 `MBDSDR_DESTRUCTIVE_REASON`（写清事由、影响提交、预期结果），
    否则拒绝（退出码 3）；
  - 每次尝试（放行或被拒）都写 `.locks/destructive_ops.log` 一行 JSON，
    放行记录含具体种类与执行前后的 HEAD；
  - `git reset -- <path>`（仅取消暂存）不算破坏性，透传。

退出码：0 成功；3 被闸门拒绝；其余为 git 自身退出码。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)
LOG_PATH = LOCKS_DIR / "destructive_ops.log"
REASON_ENV = "MBDSDR_DESTRUCTIVE_REASON"
MIN_REASON_CHARS = 8


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _head() -> str:
    return _git("rev-parse", "HEAD")


def _reset_kind(args: List[str]) -> Optional[str]:
    if "--hard" in args:
        return "hard"
    if "--soft" in args:
        return "soft"
    if "--mixed" in args:
        return "mixed"
    if "--" in args:
        return None  # 只取消暂存，安全
    return "no-op"


def _push_kind(args: List[str]) -> Optional[str]:
    force_like = [a for a in args if a.startswith("--force")]
    if force_like:
        if any(a.startswith("--force-with-lease") for a in force_like):
            return "force-with-lease"
        if any(a.startswith("--force-if-includes") for a in force_like):
            return "force-if-includes"
        return "force"
    if "-f" in args:
        return "force"
    if any(a.startswith("+") and not a.startswith("++") for a in args):
        return "force(refspec+)"
    return None


def _pathspec_args(args: List[str]) -> List[str]:
    """从 `git commit` 参数里挑出 pathspec（含 `--` 之后的路径）。

    形态⑩的触发器：设了私有 index 还带 pathspec，git 会按**工作区**取这些路径，
    私有 index 的 blob 被绕过。这里只负责认出来，不做任何推断。
    """
    value_opts = {
        "-m", "--message", "-F", "--file", "-C", "-c", "--author",
        "--date", "--template", "-S", "--gpg-sign", "--fixup", "--squash",
    }
    found: List[str] = []
    skip_next = False
    for tok in args:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            return ["--"]
        if tok.startswith("-") and len(tok) > 1:
            if tok in value_opts:
                skip_next = True
            continue
        found.append(tok)
    return found


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("用法：python scripts/git_safe.py <git 子命令...>")
        return 2

    op, rest = args[0], args[1:]
    kind: Optional[str] = None
    if op == "commit" and (os.environ.get("MBDSDR_DECLARED_INDEX") or "").strip():
        pathspecs = _pathspec_args(rest)
        if pathspecs:
            print("REJECT 已设置 MBDSDR_DECLARED_INDEX（私有 index 模式），禁止 `git commit` 带 pathspec。")
            print("  原因：pathspec 会让 git 按**工作区**取内容，私有 index 的 blob 被绕过，")
            print("        他线在途改动会被一起带进本次提交（形态⑩）。")
            print("  改法：改用不含 pathspec 的 `git commit`，或先 unstage 到干净 index 再提交。")
            return 3
    if op == "reset":
        kind = _reset_kind(rest)
    elif op == "push":
        kind = _push_kind(rest)

    if kind is None:
        return subprocess.run(["git", *args]).returncode

    reason = (os.environ.get(REASON_ENV) or "").strip()
    digest = (os.environ.get("MBDSDR_DESTRUCTIVE_DIGEST") or "").strip()
    head_before = _head()

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "op": op,
        "kind": kind,
        "args": args,
        "cwd": os.getcwd(),
        "head_before": head_before,
        "reason": reason or None,
        "digest": digest or None,
    }

    if not reason or len(reason) < MIN_REASON_CHARS:
        record.update({"allowed": False, "exit": 3, "head_after": head_before})
        _log(record)
        print(f"REJECT 破坏性操作 {op}（{kind}）缺少 {REASON_ENV}。")
        print(f"  日志：{LOG_PATH}")
        print("  要放行，必须写清：事由 / 影响的提交 / 预期结果，例如：")
        print(f"  $env:{REASON_ENV} = '回滚误提交 d42a5cc3，预期 main 回到 dc0d1a65，不影响他人'")
        print("  并补 `MBDSDR_DESTRUCTIVE_DIGEST=<被移出的提交号>`。")
        print("  禁止用 reset 改写已推送历史；合法撤销优先 `git revert`。")
        return 3

    proc = subprocess.run(["git", *args])
    head_after = _head()
    record.update(
        {"allowed": True, "exit": proc.returncode, "head_after": head_after, "head_moved": head_before != head_after}
    )
    _log(record)
    print(f"ALLOW {op}（{kind}）已执行；HEAD {head_before[:12]} -> {head_after[:12]}；日志 {LOG_PATH}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
