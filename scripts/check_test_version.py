#!/usr/bin/env python3
"""检测「改测试销红」：HEAD 版本与工作区版本的测试结果必须一致。

同一用例在 HEAD 稳定红、在工作区绿，唯一原因是测试文件被未提交改写——
这时绿灯是假的。本脚本把 HEAD 版本放进临时 `git worktree` 跑一遍，再跑工作区版本：

  * HEAD 红 + 工作区绿 → `WARN 被测版本不同，绿的结论无效`，退出码 1；
  * 两版同红 / 同绿 → 结论一致，退出码 0；
  * HEAD 绿 + 工作区红 → 工作区更严，退出码 0，但会提示。

用法：
  python scripts/check_test_version.py tests/test_foo.py
  python scripts/check_test_version.py tests/test_foo.py --json
退出码：0 结论一致；1 被测版本不同且工作区更绿；2 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _pytest(cwd: Path, target: str) -> tuple:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--tb=no"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    lines = [x for x in (proc.stdout + proc.stderr).splitlines() if x.strip()]
    return proc.returncode, lines[-1] if lines else ""


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="HEAD 版 vs 工作区版测试结果对账")
    parser.add_argument("test", help="测试文件（相对仓库根或绝对路径）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root_proc = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if root_proc.returncode != 0:
        print("不是 git 仓库")
        return 2
    root = Path(root_proc.stdout.strip())

    given = Path(args.test)
    test_path = given if given.is_absolute() else root / given
    try:
        rel = test_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        print(f"测试文件不在仓库内：{test_path}")
        return 2

    if _git(root, "cat-file", "-e", f"HEAD:{rel}").returncode != 0:
        print(f"HEAD 里没有这个文件：{rel}")
        return 2

    worktree = Path(tempfile.mkdtemp(prefix="mbdsdr_head_"))
    try:
        add = _git(root, "worktree", "add", "--detach", str(worktree), "HEAD")
        if add.returncode != 0:
            print(f"git worktree add 失败：{add.stderr.strip()}")
            return 2
        head_rc, head_tail = _pytest(worktree, rel)
    finally:
        _git(root, "worktree", "remove", "--force", str(worktree))
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)

    wt_rc, wt_tail = _pytest(root, rel)

    head_green = head_rc == 0
    wt_green = wt_rc == 0
    mismatch = head_green != wt_green
    fake_green = (not head_green) and wt_green

    payload = {
        "test": rel,
        "head": {"rc": head_rc, "green": head_green, "tail": head_tail},
        "worktree": {"rc": wt_rc, "green": wt_green, "tail": wt_tail},
        "mismatch": mismatch,
        "fake_green": fake_green,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"HEAD     rc={head_rc} {'绿' if head_green else '红'}  {head_tail}")
        print(f"工作区   rc={wt_rc} {'绿' if wt_green else '红'}  {wt_tail}")
        if fake_green:
            print(f"WARN 被测版本不同，绿的结论无效：{rel} 在 HEAD 红、工作区绿。")
            print(f"     先看 `git diff HEAD -- {rel}`；把工作区测试改回 HEAD 应重新变红。")
        elif mismatch:
            print(f"NOTE 两版结果不同（HEAD {'绿' if head_green else '红'} / 工作区 {'绿' if wt_green else '红'}），工作区更严。")
        else:
            print("PASS 两版结论一致。")

    return 1 if fake_green else 0


if __name__ == "__main__":
    sys.exit(main())
