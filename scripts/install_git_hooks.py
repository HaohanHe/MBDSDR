#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""安装/校验本仓的 git 机械闸钩子（pre-commit + post-commit + commit-msg）。

用法：
  python scripts/install_git_hooks.py            # 安装（覆盖同名 MBDSDR 钩子）
  python scripts/install_git_hooks.py --check    # 校验已安装内容与源文件一致
  python scripts/install_git_hooks.py --uninstall
  python scripts/install_git_hooks.py --force    # 覆盖非本机制的既有钩子

设计：
  * 钩子源文件在 scripts/hooks/，安装到 `git rev-parse --git-common-dir` 的
    hooks 目录（linked worktree 也共享同一套）。
  * pre-commit 调 scripts/check_commit_scope.py --staged；≥2 个活跃 owner 直接
    拒绝提交。再调 claim 闸（逐文件查台账，见下）拒绝「别人仍持有的文件」。
    通过时在 D:\\mbdsdr\\.locks\\hook_ok\\<tree>.json 留标记。
  * claim 闸逻辑在 scripts/claim_gate.py（依赖 scripts/file_claim.py 读台账）。
    这两个文件会**一并复制到 hooks 目录**：hooks 是 common-dir 共享的，而
    `root/scripts/claim_gate.py` 只存在于已更新到本提交的工作树；旧工作树里
    没有它，若只在 root 下找，那些工作树的每次提交都会因「找不到闸脚本」被
    失败关闭挡住。安装副本 = 每棵树都有一份可用实现。
  * post-commit 消费标记；没有标记的提交（--no-verify 或钩子未执行）追加到
    D:\\mbdsdr\\.locks\\git_hook_bypass.log，便于事后定位绕过者。
  * commit-msg 扫**提交信息**（pre-commit 看不见的通道）：`git commit -m "ghp_…"`
    不产生 blob，消息进 commit 对象后再删就要重写历史。判据复用
    `scripts/secret_scan.py --commit-msg`（同一套形状规则），只跑形状不跑熵；
    命中行加 `pragma: allowlist secret` 可放行（逐行，不做消息级豁免）。
    已知误报面：revert/amend/squash 会带回旧消息，旧消息里本就有凭据时会被拦——
    正确动作是改消息。`--no-verify` 会同时跳过它（post-commit 记 bypass）。
  * 状态与日志默认只写 D 盘，可用 MBDSDR_HOOK_MARKER_DIR / MBDSDR_HOOK_LOG
    环境变量改道（测试用）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_SRC = ROOT / "scripts" / "hooks"
HOOK_NAMES = ("pre-commit", "post-commit", "commit-msg")
#: 闸门的运行时依赖：装到 hooks 目录，保证旧工作树也能跑（见模块 docstring）。
SUPPORT_FILES = ("claim_gate.py", "file_claim.py", "secret_scan.py")
SIGNATURE = "MBDSDR-MANAGED-HOOK"


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


def _hooks_dir() -> Path:
    common = _git("rev-parse", "--git-common-dir").strip()
    path = Path(common)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path / "hooks"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_managed(path: Path) -> bool:
    try:
        return SIGNATURE in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def install(force: bool) -> int:
    target_dir = _hooks_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in HOOK_NAMES:
        src = HOOKS_SRC / name
        dst = target_dir / name
        if dst.exists() and not _is_managed(dst) and not force:
            print(f"[skip] {dst} 已存在且非本机制钩子；加 --force 才覆盖。")
            failed += 1
            continue
        shutil.copy2(src, dst)
        mode = dst.stat().st_mode
        os.chmod(dst, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"[install] {dst}  <-  {src.relative_to(ROOT)}  sha256={_sha256(dst)[:16]}")
    for name in SUPPORT_FILES:
        src = ROOT / "scripts" / name
        dst = target_dir / name
        shutil.copy2(src, dst)
        print(f"[install] {dst}  <-  scripts/{name}  sha256={_sha256(dst)[:16]}")
    Path(r"D:\mbdsdr\.locks\hook_ok").mkdir(parents=True, exist_ok=True)
    print(f"[info] bypass 日志：{os.environ.get('MBDSDR_HOOK_LOG') or r'D:\mbdsdr\.locks\git_hook_bypass.log'}")
    return 1 if failed else 0


def check() -> int:
    target_dir = _hooks_dir()
    bad = 0
    for name in HOOK_NAMES:
        src = HOOKS_SRC / name
        dst = target_dir / name
        if not dst.exists():
            print(f"[miss] {dst}")
            bad += 1
            continue
        if _sha256(src) != _sha256(dst):
            print(f"[diff] {dst} 与源文件不一致（重新安装即可）")
            bad += 1
            continue
        print(f"[ok] {dst} sha256={_sha256(dst)[:16]}")
    for name in SUPPORT_FILES:
        src = ROOT / "scripts" / name
        dst = target_dir / name
        if not dst.exists():
            print(f"[miss] {dst}")
            bad += 1
        elif _sha256(src) != _sha256(dst):
            print(f"[diff] {dst} 与 scripts/{name} 不一致（重新安装即可）")
            bad += 1
        else:
            print(f"[ok] {dst} sha256={_sha256(dst)[:16]}")
    return 1 if bad else 0


def uninstall() -> int:
    target_dir = _hooks_dir()
    for name in HOOK_NAMES:
        dst = target_dir / name
        if dst.exists() and _is_managed(dst):
            dst.unlink()
            print(f"[remove] {dst}")
    for name in SUPPORT_FILES:
        dst = target_dir / name
        if dst.exists():
            dst.unlink()
            print(f"[remove] {dst}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="安装 / 校验 MBDSDR git 机械闸钩子")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="仅校验安装内容与源一致")
    group.add_argument("--uninstall", action="store_true", help="卸载本机制钩子")
    parser.add_argument("--force", action="store_true", help="覆盖非本机制的既有钩子")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.uninstall:
        return uninstall()
    return install(args.force)


if __name__ == "__main__":
    sys.exit(main())
