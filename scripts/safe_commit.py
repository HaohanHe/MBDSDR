#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""安全提交：私有 index + 提交前重校验基线 + 撞锁整盘重建。

形态⑪（私有 index 的基线不是当时的 HEAD）反复出现：建完 index 到真正
``git commit`` 之间，别的线可能已经推进 HEAD。此时旧 index 相对新 HEAD 会多出
幻影 ``D`` 和别线的 ``M``，提交要么被 ref 锁拒，要么把别人的状态带进去。

本脚本把「建 index → 校验文件集 → 校验基线 → 提交 → 复核 blob」串成一次原子
操作，任何一步不满足就退出且**不提交**；撞上 ref 锁就用新 HEAD 整盘重建 index
再试（默认 3 次）。

排除他人 hunk（2026-11-12 裁定）
--------------------------------

共享文件里同时有自己和他人的在途改动时，可用 ``--exclude-hunk <path>:<start-end>``
（可重复）把指定 hunk 从**私有 index** 里反向撤掉，工作树保持原样。声明会：
① 作为 ``MBDSDR_EXCLUDE_HUNKS``（JSON）传给 pre-commit 闸门，闸门据此放行
``DIRTY(allowed)``（共享 index 不认这种声明）；
② 追加到提交说明的 ``excluded-hunks:`` 段，写明路径、行号范围与 owner（可空）。
声明范围必须完整覆盖 diff 的 hunk，跨 hunk 边界或对不上任何改动一律拒绝。

意图删除（2026-11-12）
----------------------

pre-commit 闸门对**任何未声明的删除条目**一律拒绝（幻影删除 fail-closed）。
私有 index 里「HEAD 有、新索引没有」的路径就是本次真删的文件，本脚本把它们
自动导出为 ``MBDSDR_INTENT_DELETIONS``，正常删文件不用手写声明；``--files``
清单之外的路径根本不会进索引，因此不会被误声明。

用法::

    python scripts/safe_commit.py --files a.py b.md --message-file msg.txt
    python scripts/safe_commit.py --files a.md --message-file msg.txt \\
        --exclude-hunk a.md:120-138 --exclude-owner 接入层线

退出码：0 成功；1 前置校验失败（未提交）；2 重试耗尽（未提交）；3 提交后复核失败
（已提交，需按事故卡处理）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(
    os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1]
)

EXCLUDE_ENV = "MBDSDR_EXCLUDE_HUNKS"
INTENT_ENV = "MBDSDR_INTENT_DELETIONS"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_exclusions(specs: list[str], files: list[str]) -> list[dict]:
    """解析 `--exclude-hunk <path>:<start-end>`；格式错误或路径越界直接失败。"""
    items: list[dict] = []
    for spec in specs:
        path, sep, rng = spec.rpartition(":")
        match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", rng or "")
        if not sep or not match:
            raise ValueError(f"--exclude-hunk 格式应为 <path>:<start-end>，收到 {spec!r}")
        norm = path.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        if norm not in files:
            raise ValueError(f"--exclude-hunk 的路径不在 --files 清单内：{norm}")
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start:
            raise ValueError(f"--exclude-hunk 行号范围不合法：{spec!r}")
        items.append({"path": norm, "start": start, "end": end})
    return items


def _revert_index_hunks(env: dict, path: str, ranges: list[dict]) -> None:
    """把声明的 hunk 从私有 index 里反向撤掉（工作树不动）。

    只接受「完整覆盖某个 hunk」的声明：跨 hunk 边界会多撤或少撤，宁可拒绝
    也不静默扩大排除范围。新增文件与二进制文件不支持排除。
    """
    if _run(["cat-file", "-e", f"HEAD:{path}"], env).returncode != 0:
        raise ValueError(f"{path} 是新增文件，不支持 --exclude-hunk")
    proc = _run(["diff", "--cached", "--no-renames", "-U0", "HEAD", "--", path], env)
    if proc.returncode != 0:
        raise ValueError(
            f"读取 {path} 的暂存 diff 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    text = proc.stdout.decode("utf-8", "replace")
    if "GIT binary patch" in text or text.startswith("Binary files"):
        raise ValueError(f"{path} 是二进制文件，不支持 --exclude-hunk")
    header: list[str] = []
    hunks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines(True):
        if line.startswith("@@"):
            if cur is not None:
                hunks.append(cur)
            cur = {"head": line, "body": [], "start": 0, "end": -1}
        elif cur is None:
            header.append(line)
        else:
            cur["body"].append(line)
    if cur is not None:
        hunks.append(cur)
    if not hunks:
        raise ValueError(f"{path} 在私有 index 里没有改动，无法排除")

    for hunk in hunks:
        match = _HUNK_RE.match(hunk["head"].rstrip("\n"))
        if not match:
            raise ValueError(f"{path} 的 hunk 头无法解析：{hunk['head'].strip()}")
        hunk["start"] = int(match.group(1))
        count = int(match.group(2) or 1)
        if count == 0:
            raise ValueError(
                f"{path} 含纯删除 hunk（@@ … +{hunk['start']},0），暂不支持 --exclude-hunk"
            )
        hunk["end"] = hunk["start"] + count - 1

    selected: list[dict] = []
    for item in ranges:
        matched = [
            h for h in hunks if h["start"] <= item["end"] and h["end"] >= item["start"]
        ]
        if not matched:
            raise ValueError(
                f"{path}:{item['start']}-{item['end']} 没对上任何暂存 hunk"
            )
        for hunk in matched:
            if not (item["start"] <= hunk["start"] and hunk["end"] <= item["end"]):
                raise ValueError(
                    f"{path}:{item['start']}-{item['end']} 未完整覆盖 hunk "
                    f"{hunk['start']}-{hunk['end']}，请按完整 hunk 范围声明"
                )
            if all(hunk is not s for s in selected):
                selected.append(hunk)
    if not selected:
        raise ValueError(f"{path} 的排除声明没有选中任何 hunk")

    patch = "".join(header) + "".join(
        h["head"] + "".join(h["body"]) for h in selected
    )
    apply = _run(
        ["apply", "--cached", "-R", "--unidiff-zero", "--whitespace=nowarn", "-"],
        env,
        patch.encode("utf-8"),
    )
    if apply.returncode != 0:
        raise ValueError(
            f"{path} 排除 hunk 失败：{apply.stderr.decode('utf-8', 'replace')[:300]}"
        )


def _exclusion_trailer(message: bytes, exclusions: list[dict], owner: str) -> bytes:
    """把排除清单追加到提交说明（提交说明即持久日志）。"""
    if not exclusions:
        return message
    lines = ["", "", "excluded-hunks:" + (f" owner={owner}" if owner else "")]
    for item in exclusions:
        lines.append(f"- {item['path']}:{item['start']}-{item['end']}")
    return message.rstrip(b"\n") + ("\n" + "\n".join(lines) + "\n").encode("utf-8")

LOCK_HINTS = ("cannot lock ref", "expected ", "Unable to create", "index.lock")


def _run(args, env, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], env=env,
                          capture_output=True, input=stdin)


def _emit(proc: subprocess.CompletedProcess, label: str) -> None:
    """失败时把子进程输出原样打出来：只剩返回码等于看不见闸门为什么拒。"""
    out = proc.stdout.decode("utf-8", "replace").rstrip()
    err = proc.stderr.decode("utf-8", "replace").rstrip()
    if out:
        print(f"[{label}] stdout:\n{out}")
    if err:
        print(f"[{label}] stderr:\n{err}")


def _report_hooks(proc: subprocess.CompletedProcess, env: dict) -> None:
    """成功路径也把钩子交代清楚：跑没跑、scope 结果、标记去哪了。

    钩子的输出由 git 转发进 commit 的 stdout/stderr，而 ``_run`` 一直在捕获，
    所以这里主动回放 pre-commit / post-commit / 凭据扫描的关键行，再补一条结论——
    「通过了」本身必须可见，否则不可见的门禁等于没有。
    """
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.lstrip().startswith(("[pre-commit]", "[post-commit]", "[secret-scan]"))
    ]
    for line in lines:
        print(line)
    tree = _run(["rev-parse", "HEAD^{tree}"], env).stdout.decode().strip()
    marker = (
        Path(os.environ.get("MBDSDR_HOOK_MARKER_DIR") or r"D:\mbdsdr\.locks\hook_ok")
        / f"{tree}.json"
    )
    pre = "已执行" if any(l.lstrip().startswith("[pre-commit]") for l in lines) else "无输出"
    post = "已执行" if any(l.lstrip().startswith("[post-commit]") for l in lines) else "无输出"
    state = "仍在（post-commit 未消费）" if marker.exists() else "已被 post-commit 消费"
    print(
        f"[hooks] pre-commit={pre} · commit RC={proc.returncode} · "
        f"标记 {tree[:8]} {state} · post-commit={post}"
    )


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


def _attempt(
    files: list[str],
    message: bytes,
    index: Path,
    exclusions: list[dict] | None = None,
    exclude_owner: str = "",
) -> tuple[int, str]:
    """一次完整尝试。返回 (退出码, 说明)。"""
    exclusions = exclusions or []
    if index.exists():
        index.unlink()
    env = _index_env(index)

    base = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()

    # 1) 用当时的 HEAD 建 index（形态⑧/⑪：绝不复用陈旧 index）
    read_tree = _run(["read-tree", "HEAD"], env)
    if read_tree.returncode != 0:
        _emit(read_tree, "read-tree")
        return 1, "read-tree 失败"

    # 2) 只 add 声明文件；工作区内容即 index 内容
    add = _run(["add", "--", *files], env)
    if add.returncode != 0:
        _emit(add, "git add")
        return 1, f"git add 失败：{add.stderr.decode('utf-8', 'replace')[:200]}"

    # 2b) 排除他人 hunk：只从私有 index 反向撤掉，工作树不动
    if exclusions:
        for path in sorted({e["path"] for e in exclusions}):
            ranges = [e for e in exclusions if e["path"] == path]
            try:
                _revert_index_hunks(env, path, ranges)
            except ValueError as exc:
                return 1, str(exc)
            print(
                f"[exclude] {path}: "
                + ", ".join(f"{e['start']}-{e['end']}" for e in ranges)
                + (f"（owner={exclude_owner}）" if exclude_owner else "")
            )
        env[EXCLUDE_ENV] = json.dumps(
            [
                {
                    "path": e["path"],
                    "start": e["start"],
                    "end": e["end"],
                    "owner": exclude_owner,
                }
                for e in exclusions
            ],
            ensure_ascii=False,
        )
        env["MBDSDR_DECLARED_INDEX"] = str(index)
        message = _exclusion_trailer(message, exclusions, exclude_owner)

    staged = _staged_set(env)
    expect = []
    for f in files:
        in_head = _run(["cat-file", "-e", f"HEAD:{f}"], env).returncode == 0
        # 删除文件同样要能提交：HEAD 有、工作区没有 → D（旧实现一律按 M 期望，
        # 于是任何「删掉某文件」的提交都被判成文件集不符，退不回去）。
        in_wt = (ROOT / f).exists()
        mark = "D" if (in_head and not in_wt) else ("M" if in_head else "A")
        expect.append(f"{mark}\t{f}")
    if sorted(staged) != sorted(expect):
        extra = [s for s in staged if s not in expect]
        missing = [e for e in expect if e not in staged]
        if exclusions and missing:
            names = [m.split("\t", 1)[-1] for m in missing]
            return 1, (
                f"排除 hunk 后文件无剩余改动：{names}"
                "（把不该提交的 hunk 全排除了，或声明范围过大）"
            )
        return 1, f"文件集不符：多 {extra} 缺 {missing}"

    # 2c) 意图删除集：私有 index 里「HEAD 有、新索引没有」的路径就是本次真删
    #     的文件，自动导出给 pre-commit 闸门（闸门对未声明的删除一律拒绝）。
    deletions = [line.split("\t", 1)[1] for line in staged if line.startswith("D\t")]
    if deletions:
        env[INTENT_ENV] = "\n".join(deletions)
        print(f"[intent-delete] 自动声明 {len(deletions)} 个删除：{', '.join(deletions)}")

    # 3) 记录待提交 blob，供提交后复核（形态⑩）
    #    用 --verify --quiet 取：删除项（index/HEAD 都没有该路径）返回空，
    #    否则 `rev-parse :path` 会把参数原样回显，删除项必被误判成 blob 不一致。
    def _blob(rev: str) -> str:
        proc = _run(["rev-parse", "--verify", "--quiet", rev], env)
        return proc.stdout.decode().strip() if proc.returncode == 0 else ""

    wanted = {f: _blob(f":{f}") for f in files}

    # 4) 提交前重校验基线：HEAD 变了就整盘重建
    now = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()
    if now != base:
        return 1, f"基线在提交前变化：{base[:8]} → {now[:8]}（下次重试用新 HEAD 重建）"

    # 5) 提交（消息走 stdin bytes，避开编码坑）
    commit = _run(["commit", "-F", "-"], env, stdin=message)
    if commit.returncode != 0:
        _emit(commit, "git commit")
        err = (commit.stderr + commit.stdout).decode("utf-8", "replace")
        if any(h in err for h in LOCK_HINTS):
            return 1, f"撞 ref 锁：{err.strip()[:160]}（下次重试用新 HEAD 重建）"
        return 1, f"git commit 失败：{err.strip()[:300]}"
    _report_hooks(commit, env)

    # 6) 提交后复核：HEAD 里每个文件的 blob 必须等于暂存时的 blob
    #    （删除项两边都为空，相等即通过）
    bad = []
    for f, blob in wanted.items():
        head_blob = _blob(f"HEAD:{f}")
        if head_blob != blob:
            bad.append(f"{f}（暂存 {blob[:8] or '<删除>'} / "
                       f"HEAD {head_blob[:8] or '<删除>'}）")
    if bad:
        return 3, "提交后 blob 不一致：" + "；".join(bad)

    head = _run(["rev-parse", "--short", "HEAD"], env).stdout.decode().strip()
    return 0, f"已提交 {head}，blob 逐文件复核一致"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="私有 index 安全提交（形态⑪）")
    ap.add_argument("--files", nargs="+", required=True, help="本笔要提交的文件（精确清单）")
    ap.add_argument("--message-file", required=True, help="提交信息文件（UTF-8）")
    ap.add_argument("--retries", type=int, default=3, help="撞锁/基线变化时的重建次数")
    ap.add_argument(
        "--exclude-hunk",
        action="append",
        default=[],
        metavar="PATH:START-END",
        help="从私有 index 排除的 hunk（可重复）；工作树保持原样",
    )
    ap.add_argument("--exclude-owner", default="", help="排除 hunk 的归属线（可留空）")
    args = ap.parse_args(argv)

    # 只剥掉真正的 "./" 前缀：lstrip("./") 会把点文件削成无点名字
    # （.gitattributes → gitattributes），这类文件根本提交不了。
    files = [f.replace("\\", "/") for f in args.files]
    files = [f[2:] if f.startswith("./") else f for f in files]
    message = Path(args.message_file).read_bytes()
    try:
        exclusions = _parse_exclusions(args.exclude_hunk, files)
    except ValueError as exc:
        print(f"拒绝：{exc}")
        return 1
    index = Path(tempfile.gettempdir()) / f"safe_commit_{os.getpid()}_{int(time.time())}.idx"

    try:
        for n in range(1, args.retries + 1):
            code, note = _attempt(
                files, message, index, exclusions=exclusions,
                exclude_owner=args.exclude_owner,
            )
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
