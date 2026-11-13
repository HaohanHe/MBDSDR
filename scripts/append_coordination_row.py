#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""向 ``docs/COORDINATION.md`` §6.1 表格**追加一行**（窄锁，不需要整文件 claim）。

为什么要这个工具
----------------
§6.1 的登记行原本要求先 ``file_claim.py claim docs/COORDINATION.md``，而 §6.1
表格所在文件被多条线共用：一次「只想补一行表格」的动作，会把整本协调文档占住
一个租期（实测今天三条线各被挡一次，单次等待十几分钟）。登记行是**追加型写入**，
不需要重读全文语义，只需要「读-插-原子写回」在锁内完成。

两条路并存，判据不重复
----------------------
* **大段编辑**（改章节、改规则、重排表格、删行）→ 仍然整文件 claim，走
  ``file_claim.py``；因为那需要人读上下文、判断冲突。
* **追加一行登记** → 走本工具：独立的毫秒级锁（``.locks/coordination-append.lock``），
  不 claim 整文件。它只做一件事：读当前内容 → 插到 §6.1 表格末尾 → 原子写回 →
  立刻放锁。

不放宽校验
----------
写入前仍要过 §6.1 的字段检查，任一不过直接拒绝且**不改动文件**：
* ``--path`` 可解析（相对仓库根、不允许绝对路径与 ``..``），且文件存在；
  支持 ``docs/{a,b}.md`` 花括号组，组内每个文件都要能被台账对上；
* ``--owner`` 与台账**活跃 claim** 的 owner 一致（不区分大小写）；没有活跃
  claim 时，接受「同一 owner 在 ``claim_window.RELEASE_WINDOW_S``（60 分钟）内
  释放过」——这一条与 pre-commit ④ 的判据是**同一个函数**，否则闸要求补行、
  工具却拒收，人只能 ``--no-verify`` 绕过；
* ``--note`` 非空、不含 ``|`` 与换行（否则会把表格写坏）。

用法::

    python scripts/append_coordination_row.py \
        --path scripts/foo.py --owner "收尸线/6ae85f56" \
        --note "一句话说明为什么占这个文件"

退出码：0 成功；1 校验失败；2 用法错误；3 锁超时。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import file_claim as fc  # noqa: E402  （占位文件名/只读窥视，与闸同一来源）
from claim_window import RELEASE_WINDOW_S, window_release  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC = "docs/COORDINATION.md"
LOCK_NAME = "coordination-append.lock"
LOCK_TIMEOUT_S = 2.0
LOCK_STALE_S = 60.0
HEADER_HINT = "| 文件 | 线名"


def _locks_dir(root: Path) -> Path:
    env = os.environ.get("MBDSDR_LOCKS_DIR")
    return Path(env) if env else root.parent / ".locks"


def _acquire_lock(lock_path: Path) -> None:
    """O_EXCL 建锁文件；拿不到就 1ms 退避重试，超过 LOCK_TIMEOUT_S 抛超时。"""
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
            os.close(fd)
            return
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > LOCK_STALE_S:
                    lock_path.unlink(missing_ok=True)  # 持锁进程已死，清掉重试
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"锁等待超过 {LOCK_TIMEOUT_S:.1f}s：{lock_path}")
            time.sleep(0.001)


def _release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().lower()


def expand_paths(raw: str) -> List[str]:
    """把 ``a/{b,c}.py`` 展开成多条；也接受单条路径。"""
    raw = raw.strip().strip("`")
    if "{" not in raw:
        return [raw]
    prefix, rest = raw.split("{", 1)
    if "}" not in rest:
        raise ValueError(f"花括号组不完整：{raw}")
    body, suffix = rest.split("}", 1)
    return [prefix + part.strip() + suffix for part in body.split(",") if part.strip()]


def _load_registry(registry: Path) -> dict:
    """读台账；读不了/不是 JSON 对象就直接拒绝（fail-closed，不猜）。"""
    if not registry.is_file():
        return {}
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"台账不可解析：{registry}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"台账格式不对（顶层不是对象）：{registry}")
    return data


def _active_owner(locks: Path, path: str, now: float) -> str:
    """该路径活跃 claim 的 owner；没有活跃 claim 返回空串。

    判活读**占位文件**（心跳 + TTL），与 pre-commit ④ 完全同一来源。台账
    ``current`` 只是旁证：用它判活会出现「闸说有活跃 claim、工具说没有」，
    照着拒绝信息补行反而补不上。
    """
    # 命名必须是 file_claim._marker_for 那一个（sha1(规范化路径)[:16].claim）；
    # 这里按 --root 推导锁目录，避免 MBDSDR_LOCKS_DIR 未设时读到另一棵树。
    digest = hashlib.sha1(_norm(path).encode("utf-8")).hexdigest()[:16]
    payload = fc._peek_marker(locks / "claims" / f"{digest}.claim")
    if not payload:
        return ""
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or 0)
    ttl_s = float(payload.get("ttl_min") or 0) * 60
    if not ttl_s or now - heartbeat > ttl_s:
        return ""
    return str(payload.get("owner") or "")


def validate(root: Path, registry: Path, raw_paths: str, owner: str, note: str) -> List[str]:
    errors: List[str] = []
    try:
        paths = expand_paths(raw_paths)
    except ValueError as exc:
        return [str(exc)]
    try:
        data = _load_registry(registry)
    except ValueError as exc:
        return [str(exc)]
    if not paths:
        errors.append("--path 为空")
    if "/" not in owner or not owner.strip():
        errors.append('--owner 必须形如「线名/会话短号」，例如 "收尸线/6ae85f56"')
    note = note.strip()
    if not note:
        errors.append("--note 不能为空（写清为什么占这个文件）")
    if "|" in note or "\n" in note or "\r" in note:
        errors.append("--note 不能含 | 或换行（会把 §6.1 表格写坏）")
    for path in paths:
        p = Path(path)
        if p.is_absolute() or ".." in p.parts:
            errors.append(f"路径必须是仓库内相对路径：{path}")
            continue
        if not (root / path).exists():
            errors.append(f"路径不存在：{path}")
            continue
        now = time.time()
        claimed = _active_owner(registry.parent, path, now)
        if claimed:
            if _norm(claimed) != _norm(owner):
                errors.append(
                    f"owner 不一致：{path} 的活跃 claim 属于 {claimed!r}，不是 {owner!r}")
            continue
        # 与 pre-commit ④ 同一个窗口判定（claim_window.window_release）。
        entry = (data.get("claims") or {}).get(_norm(path)) or {}
        if window_release(entry, owner, now) is not None:
            continue
        other = window_release(entry, "", now)
        if other is not None:
            errors.append(
                f"{path} 最近 {RELEASE_WINDOW_S / 60:.0f} 分钟内被 {other[0]!r} 释放，"
                f"不是 {owner!r}（release 不消行；换对 owner，或等窗口过期）")
        else:
            errors.append(
                f"台账里既没有活跃 claim、也没有最近 {RELEASE_WINDOW_S / 60:.0f} 分钟内的"
                f"释放记录：{path}（先 file_claim.py claim，或等接管）")
    return errors


def append_row(doc: Path, row: str) -> Tuple[int, int]:
    """把 ``row`` 插到 §6.1 表格末尾；返回 (插入行号 1-based, 表格总行数)。

    行尾必须原样保留：这个仓库的文档是 LF；如果用默认 newline 打开写回，
    Python 会把每个 ``\\n`` 翻成 ``os.linesep``（Windows 上是 CRLF），
    等于把整本 370KB 文档重写一遍——「追加一行」就退化成「全文换行」。
    """
    with doc.open("r", encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    start = next((i for i, ln in enumerate(lines) if HEADER_HINT in ln), None)
    if start is None:
        raise ValueError(f"找不到 §6.1 表头（含 {HEADER_HINT!r}）：{doc}")
    eol = "\r\n" if lines[start].endswith("\r\n") else "\n"
    end = start + 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    if end == start + 1:
        raise ValueError("§6.1 表格只有表头、没有分隔行")
    row = row.rstrip("\r\n") + eol
    lines.insert(end, row)
    tmp = doc.with_name(doc.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    os.replace(tmp, doc)
    return end + 1, end - start


def main(argv: Iterable[str] | None = None) -> int:
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="向 COORDINATION.md §6.1 表格追加一行（窄锁，不占整文件）")
    ap.add_argument("--path", required=True, help="文件路径（支持 docs/{a,b}.md 花括号组）")
    ap.add_argument("--owner", required=True, help='线名/会话短号，例如 "收尸线/6ae85f56"')
    ap.add_argument("--note", required=True, help="为什么占这个文件（写入表格第三列）")
    ap.add_argument("--doc", default=DEFAULT_DOC, help="协调文档路径（相对仓库根）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve()
    doc = root / args.doc
    registry = _locks_dir(root) / "claims.json"

    errors = validate(root, registry, args.path, args.owner, args.note)
    if errors:
        print("APPEND DENIED：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2 if not args.path.strip() else 1

    stamp = time.strftime("%Y-%m-%d %H:%M")
    row = (f"| `{args.path.strip()}` | {args.owner.strip()}（{args.note.strip()}） "
           f"| {stamp} | 本笔 |")

    lock_path = _locks_dir(root) / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _acquire_lock(lock_path)
    except TimeoutError as exc:
        print(f"APPEND FAIL：{exc}", file=sys.stderr)
        return 3
    try:
        line_no, rows = append_row(doc, row)
    except ValueError as exc:
        print(f"APPEND FAIL：{exc}", file=sys.stderr)
        return 1
    finally:
        _release_lock(lock_path)

    print(f"APPEND OK 第 {line_no} 行（表格 {rows} 行）耗时 {(time.monotonic() - started) * 1000:.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
