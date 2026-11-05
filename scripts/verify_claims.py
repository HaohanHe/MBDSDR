#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""文档断言核实器：把「已修复/已提交」从口头承诺变成可复跑证据。

断言格式（四元组，缺一即判「未证实」）：

    <结论> — <commit hash> — <path>:<line> — <改动后唯一字面量>

三步验证：

  1. ``git cat-file -e <hash>^{commit}``
  2. ``git show --name-only --format= <hash> -- <path>`` 必须命中声明路径
  3. ``git log -S <字面量> --oneline -- <path>`` 必须命中同一 hash

分类：

  * 可证实                三步全过
  * 未证实（文档过期）    有 hash，但 hash/路径/字面量对不上
  * 未证实（无 hash）     没写 hash（或没写 path:line/字面量）

跳过：标题行、``` 围栏代码块、``$ git`` 证据命令行 —— 它们不是断言。

默认只报告、退出码 0（存量 92 条待各线认领）；``--fail-on-unverified``
供存量清零后接 CI。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

CLAIM_RE = re.compile(r"(已修复|已提交|已落地|已收口|已补齐|已解决|已修|已改|更正)")
HASH_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
PATHLINE_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|md|json|yaml|yml|ts|dart|cpp|c|h|hpp))"
    r":(\d+(?:-\d+)?)")
TICK_RE = re.compile(r"`([^`]+)`")

OK = "可证实"
STALE = "未证实（文档过期）"
NO_HASH = "未证实（无 hash）"

EXCLUDE_DIRS = {"audit_r2"}
#: 白名单/账本本身不是断言；它按设计要引用「已修复…」原文，扫它等于自吞。
EXCLUDE_FILES = {"claim-waivers.md"}


@dataclass
class Claim:
    doc: str
    line: int
    text: str
    status: str = ""
    reason: str = ""
    action: str = ""
    commit: str = ""
    target: str = ""
    literal: str = ""
    detail: List[str] = field(default_factory=list)


def _git(root: Path, args: List[str], timeout: int = 30) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def is_git_repo(root: Path) -> bool:
    proc = _git(root, ["rev-parse", "--show-toplevel"])
    return proc is not None and proc.returncode == 0


def _commit_exists(root: Path, h: str) -> bool:
    proc = _git(root, ["cat-file", "-e", f"{h}^{{commit}}"])
    return proc is not None and proc.returncode == 0


def _commit_touches(root: Path, h: str, path: str) -> bool:
    proc = _git(root, ["show", "--name-only", "--format=", h, "--", path])
    if proc is None or proc.returncode != 0:
        return False
    return path in [ln.strip() for ln in proc.stdout.splitlines()]


def _log_s_hits(root: Path, h: str, literal: str, path: str) -> bool:
    proc = _git(root, ["log", "-S", literal, "--oneline", "--", path])
    if proc is None or proc.returncode != 0:
        return False
    return any(ln.split()[0].startswith(h[:7]) for ln in proc.stdout.splitlines() if ln.strip())


def _is_literal_token(t: str, hashes: List[str], target: str) -> bool:
    """能否作为「改动后唯一字面量」候选。"""
    if not t or t in hashes or t in target:
        return False
    if PATHLINE_RE.fullmatch(t) or HASH_RE.fullmatch(t):
        return False
    return len(t) >= 3 and not t.isdigit()


def _literal_candidates(text: str, hashes: List[str], target: str) -> List[str]:
    """列出候选字面量，按可信度排序。

    旧实现假设行内第一个合格反引号 token 就是字面量；但表格行/带前缀的
    断言会先出现文件名或符号名，``git log -S`` 就比对了错对象。改为：

    1. 四元组 ``<hash> — <path>:<line> — <literal>`` 中紧跟在 path:line
       后面的那个 token（可能不止一个四元组）；
    2. 行内没有这种位置时，退回旧的「第一个合格 token」。

    调用方对候选逐个 ``git log -S``，命中即算；不再要求唯一。
    """
    tokens = [raw.strip() for raw in TICK_RE.findall(text)]
    preferred: List[str] = []
    for index, token in enumerate(tokens[:-1]):
        follow = tokens[index + 1]
        if PATHLINE_RE.fullmatch(token) and _is_literal_token(follow, hashes, target):
            if follow not in preferred:
                preferred.append(follow)
    if preferred:
        return preferred
    for token in tokens:
        if _is_literal_token(token, hashes, target):
            return [token]
    return []


def _hashes(text: str) -> List[str]:
    """取候选 commit hash；纯数字是日期/行号（如 20260919），不是提交。"""
    return [t for t in HASH_RE.findall(text) if any(c in "abcdef" for c in t)]


def classify(text: str, root: Path, heading: str = "") -> Claim:
    """把一行文本判成三栏之一，并给出修复动作。

    ``heading`` 是最近一个标题行：标题里常写着 `path:line`（如
    ``### 7.1 desktop/troubleshooting.py:92``），断言本体则常省略它。
    只借用标题里的定位，不跨段落借用 hash/字面量。
    """
    c = Claim(doc="", line=0, text=text.strip())
    hashes = _hashes(text)
    targets = PATHLINE_RE.findall(text) or PATHLINE_RE.findall(heading)
    if not hashes:
        c.status = NO_HASH
        c.reason = "未写 commit hash，无法用 git 复核"
        c.action = "补 <commit hash> — <path>:<line> — <改动后字面量>；不知道是哪笔就删断言"
        return c
    if not targets:
        c.status = STALE
        c.reason = "有 hash 但缺 <path>:<line>，无法定位改动"
        c.action = "补 path:line，或删掉无法定位的断言"
        return c

    target = f"{targets[0][0]}:{targets[0][1]}"
    c.target = target
    path = targets[0][0]

    known = [h for h in hashes if _commit_exists(root, h)]
    if not known:
        c.status = STALE
        c.reason = f"hash {hashes[0]} 不存在或不是 commit"
        c.action = "换成真实 commit hash；换不出就删断言"
        return c
    h = known[0]
    c.commit = h

    if not _commit_touches(root, h, path):
        c.status = STALE
        c.reason = f"{h} 未改动 {path}"
        c.action = "换成真正改到该文件的 hash（多半是另一条线的提交）"
        return c

    candidates = _literal_candidates(text, hashes, target)
    if not candidates:
        c.status = STALE
        c.reason = "缺「改动后唯一字面量」，git log -S 无法定位"
        c.action = "补反引号字面量（改动后代码里真实存在的那一段）"
        return c

    literal = next((t for t in candidates if _log_s_hits(root, h, t, path)), "")
    c.literal = literal
    if not literal:
        c.status = STALE
        c.reason = f"git log -S 候选 {candidates} 均未命中 {h}"
        c.action = "改字面量为该提交真实引入/删除的文本；对不上就判未证实并删断言"
        return c

    c.status = OK
    c.reason = "cat-file + show --name-only + log -S 三步全过"
    c.detail = [h, target, literal]
    return c


def iter_claim_lines(docs_root: Path, recursive: bool = False) -> List[tuple]:
    """返回 (相对路径, 行号, 文本, 最近标题)；跳过围栏代码块与 $ 证据命令。

    默认只扫 ``docs/*.md`` 顶层（与历史盘点的 103 条同口径）；
    ``--recursive`` 才下沉到 docs/learn 等历史子目录。
    """
    out: List[tuple] = []
    pattern = "**/*.md" if recursive else "*.md"
    for p in sorted(docs_root.glob(pattern)):
        if p.name in EXCLUDE_FILES:
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        in_fence = False
        heading = ""
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, raw in enumerate(lines, 1):
            s = raw.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not s or s.startswith("$ git"):
                continue
            if s.startswith("#"):
                heading = s
                continue
            if CLAIM_RE.search(s):
                out.append((p.relative_to(docs_root.parent).as_posix(), i, s, heading))
    return out


def verify_docs(root: Path = ROOT, docs_root: Optional[Path] = None,
                recursive: bool = False) -> List[Claim]:
    root = Path(root)
    docs_root = Path(docs_root) if docs_root else root / "docs"
    claims: List[Claim] = []
    for rel, line, text, heading in iter_claim_lines(docs_root, recursive):
        c = classify(text, root, heading)
        c.doc, c.line = rel, line
        claims.append(c)
    return claims


def summarize(claims: List[Claim]) -> Dict[str, int]:
    counts = {OK: 0, STALE: 0, NO_HASH: 0}
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


def print_report(claims: List[Claim]) -> None:
    counts = summarize(claims)
    print(f"断言总数：{len(claims)}  |  可证实 {counts[OK]}  |  "
          f"文档过期 {counts[STALE]}  |  无 hash {counts[NO_HASH]}")
    for status in (STALE, NO_HASH, OK):
        rows = [c for c in claims if c.status == status]
        if not rows:
            continue
        print(f"\n[{status}] {len(rows)} 条")
        for c in rows:
            print(f"  {c.doc}:{c.line}  {c.reason}")
            if status != OK:
                print(f"      建议：{c.action}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="文档断言核实器")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--docs", default=None, help="文档目录，默认 <root>/docs")
    ap.add_argument("--recursive", action="store_true",
                    help="下沉到 docs 子目录（含 docs/learn 历史档）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fail-on-unverified", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not is_git_repo(root):
        print(f"错误: {root} 不是 git 仓库，无法核实断言", file=sys.stderr)
        return 2
    claims = verify_docs(root, Path(args.docs) if args.docs else None, args.recursive)
    print_report(claims)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"summary": summarize(claims),
                                   "claims": [asdict(c) for c in claims]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")
    if args.fail_on_unverified and any(c.status != OK for c in claims):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
