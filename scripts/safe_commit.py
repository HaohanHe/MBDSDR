#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""安全提交：私有 index + 提交前重校验基线 + 撞锁整盘重建。

形态⑪（私有 index 的基线不是当时的 HEAD）反复出现：建完 index 到真正
``git commit`` 之间，别的线可能已经推进 HEAD。此时旧 index 相对新 HEAD 会多出
幻影 ``D`` 和别线的 ``M``，提交要么被 ref 锁拒，要么把别人的状态带进去。

本脚本把「建 index → 校验文件集 → 校验基线 → 提交 → 复核 blob」串成一次原子
操作，任何一步不满足就退出且**不提交**；撞上 ref 锁就用新 HEAD 整盘重建 index
再试（默认 3 次）。

用法::

    python scripts/safe_commit.py --files a.py b.md --message-file msg.txt

退出码：0 成功；1 前置校验失败（未提交）；2 重试耗尽（未提交）；3 提交后复核失败
（已提交，需按事故卡处理）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOCK_HINTS = ("cannot lock ref", "expected ", "Unable to create", "index.lock")


def _run(args, env, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], env=env,
                          capture_output=True, input=stdin)


def _index_env(path: Path) -> dict:
    env = dict(os.environ)
    env.pop("GIT_INDEX_FILE", None)          # 形态⑫：不继承会话级泄漏
    env["GIT_INDEX_FILE"] = str(path)        # 只进子进程
    return env


def _staged_set(env) -> list[str]:
    proc = _run(["diff", "--cached", "--name-status", "--no-renames"], env)
    out = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append(f"{parts[0]}\t{parts[1]}")
    return out


def _attempt(files: list[str], message: bytes, index: Path) -> tuple[int, str]:
    """一次完整尝试。返回 (退出码, 说明)。"""
    if index.exists():
        index.unlink()
    env = _index_env(index)

    base = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()

    # 1) 用当时的 HEAD 建 index（形态⑧/⑪：绝不复用陈旧 index）
    if _run(["read-tree", "HEAD"], env).returncode != 0:
        return 1, "read-tree 失败"

    # 2) 只 add 声明文件；工作区内容即 index 内容
    add = _run(["add", "--", *files], env)
    if add.returncode != 0:
        return 1, f"git add 失败：{add.stderr.decode('utf-8', 'replace')[:200]}"

    staged = _staged_set(env)
    expect = []
    for f in files:
        in_head = _run(["cat-file", "-e", f"HEAD:{f}"], env).returncode == 0
        expect.append(f"{'M' if in_head else 'A'}\t{f}")
    if sorted(staged) != sorted(expect):
        extra = [s for s in staged if s not in expect]
        missing = [e for e in expect if e not in staged]
        return 1, f"文件集不符：多 {extra} 缺 {missing}"

    # 3) 记录待提交 blob，供提交后复核（形态⑩）
    wanted = {}
    for f in files:
        proc = _run(["rev-parse", f":{f}"], env)
        wanted[f] = proc.stdout.decode().strip()

    # 4) 提交前重校验基线：HEAD 变了就整盘重建
    now = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()
    if now != base:
        return 1, f"基线在提交前变化：{base[:8]} → {now[:8]}（下次重试用新 HEAD 重建）"

    # 5) 提交（消息走 stdin bytes，避开编码坑）
    commit = _run(["commit", "-F", "-"], env, stdin=message)
    if commit.returncode != 0:
        err = (commit.stderr + commit.stdout).decode("utf-8", "replace")
        if any(h in err for h in LOCK_HINTS):
            return 1, f"撞 ref 锁：{err.strip()[:160]}（下次重试用新 HEAD 重建）"
        return 1, f"git commit 失败：{err.strip()[:300]}"

    # 6) 提交后复核：HEAD 里每个文件的 blob 必须等于暂存时的 blob
    bad = []
    for f, blob in wanted.items():
        head_blob = _run(["rev-parse", f"HEAD:{f}"], env).stdout.decode().strip()
        if head_blob != blob:
            bad.append(f"{f}（暂存 {blob[:8]} / HEAD {head_blob[:8]}）")
    if bad:
        return 3, "提交后 blob 不一致：" + "；".join(bad)

    head = _run(["rev-parse", "--short", "HEAD"], env).stdout.decode().strip()
    return 0, f"已提交 {head}，blob 逐文件复核一致"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="私有 index 安全提交（形态⑪）")
    ap.add_argument("--files", nargs="+", required=True, help="本笔要提交的文件（精确清单）")
    ap.add_argument("--message-file", required=True, help="提交信息文件（UTF-8）")
    ap.add_argument("--retries", type=int, default=3, help="撞锁/基线变化时的重建次数")
    args = ap.parse_args(argv)

    files = [f.replace("\\", "/").lstrip("./") for f in args.files]
    message = Path(args.message_file).read_bytes()
    index = Path(tempfile.gettempdir()) / f"safe_commit_{os.getpid()}_{int(time.time())}.idx"

    try:
        for n in range(1, args.retries + 1):
            code, note = _attempt(files, message, index)
            print(f"[try {n}] {note}")
            if code == 0:
                return 0
            if code == 3:
                return 3
        print(f"重试 {args.retries} 次仍未提交，已放弃（工作区与 index 未改动）")
        return 2
    finally:
        if index.exists():
            index.unlink()


if __name__ == "__main__":
    sys.exit(main())
