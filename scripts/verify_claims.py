#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""文档断言核实器：把「已修复/已提交」从口头承诺变成可复跑证据。

断言格式（四元组，缺一即判「未证实」）：

    <结论> — <commit hash> — <path>:<line> — <改动后唯一字面量>

三步验证：

  1. ``git cat-file -e <hash>^{commit}``（纯数字 hash 也走这一步；真日期解析失败即丢弃）
  2. ``git show --name-only --format= <hash> -- <path>`` 必须命中声明路径
  3. ``git log -S <字面量> --oneline -- <path>`` 必须命中同一 hash

第 4 步（``--check-lines``，只报不拦）核对**行号内容**：取 ``git show HEAD:<path>``
的第 N 行，判断该行是否包含被引字面量。坐标口径见 ``check_line()`` 的注释与
``docs/evidence-standard.md §7.7``。

分类：

  * 可证实                三步全过
  * 未证实（文档过期）    有 hash，但 hash/路径/字面量对不上
  * 未证实（无 hash）     没写 hash（或没写 path:line/字面量）

跳过：标题行、``` 围栏代码块、``$ git`` 证据命令行 —— 它们不是断言。

默认只报告、退出码 0（存量 92 条待各线认领）；``--fail-on-unverified``
供存量清零后接 CI。``--check-lines`` 的输出**不参与**任何退出码。
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
#: 行内坐标基准：``（基准 `abc1234`）`` / ``baseline abc1234``
BASELINE_RE = re.compile(r"(?:基准|baseline)[:：]?\s*`?([0-9a-f]{7,40})`?")
#: 文件级坐标基准：``<!-- line-baseline: abc1234 -->``（写在文首，整篇生效）
DOC_BASELINE_RE = re.compile(r"<!--\s*line-baseline:\s*([0-9a-f]{7,40})\s*-->")

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
    #: 坐标基准（写入该四元组时的 HEAD 短 hash）；缺省则第 4 步一律判「需重定位」
    baseline: str = ""


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


def _hashes(text: str, root: Optional[Path] = None) -> List[str]:
    """取候选 commit hash，并区分「纯数字真 hash」与日期/行号。

    形状无法区分 ``12610725``（真提交）和 ``20261106``（日期），所以纯数字
    候选一律交给 ``git cat-file -e`` 试解析：能解析成 commit 才收；真日期解析
    失败即丢弃，仍按「无 hash」判，不会把日期当证据。误报代价是每个纯数字
    7–40 位候选一次 git 调用（通常每行 0–1 次）。
    """
    out: List[str] = []
    for t in HASH_RE.findall(text):
        if any(c in "abcdef" for c in t):
            out.append(t)
        elif root is not None and _commit_exists(root, t):
            out.append(t)
    return out


def classify(text: str, root: Path, heading: str = "", baseline: str = "") -> Claim:
    """把一行文本判成三栏之一，并给出修复动作。

    ``heading`` 是最近一个标题行：标题里常写着 `path:line`（如
    ``### 7.1 desktop/troubleshooting.py:92``），断言本体则常省略它。
    只借用标题里的定位，不跨段落借用 hash/字面量。

    ``baseline`` 是「坐标基准」（写入四元组时的 HEAD），只给第 4 步判
    红/黄用，不参与前三步；缺省则第 4 步一律按「需重定位」处理。
    """
    c = Claim(doc="", line=0, text=text.strip(), baseline=baseline)
    hashes = _hashes(text, root)
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
    """返回 (相对路径, 行号, 文本, 最近标题, 坐标基准)；跳过围栏与 $ 证据命令。

    坐标基准的优先级：本行 ``（基准 `abc1234`）`` > 最近标题里的同写法 >
    文件首部 ``<!-- line-baseline: abc1234 -->``；都没有则为空串（第 4 步按
    「需重定位」处理，不判红）。

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
        heading_baseline = ""
        doc_baseline = ""
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for raw in lines[:20]:
            m = DOC_BASELINE_RE.search(raw)
            if m:
                doc_baseline = m.group(1)
                break
        for i, raw in enumerate(lines, 1):
            s = raw.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not s or s.startswith("$ git"):
                continue
            if s.startswith("#"):
                heading = s
                m = BASELINE_RE.search(s)
                heading_baseline = m.group(1) if m else ""
                continue
            if CLAIM_RE.search(s):
                m = BASELINE_RE.search(s)
                baseline = m.group(1) if m else (heading_baseline or doc_baseline)
                out.append((p.relative_to(docs_root.parent).as_posix(), i, s, heading, baseline))
    return out


def verify_docs(root: Path = ROOT, docs_root: Optional[Path] = None,
                recursive: bool = False) -> List[Claim]:
    root = Path(root)
    docs_root = Path(docs_root) if docs_root else root / "docs"
    claims: List[Claim] = []
    for rel, line, text, heading, baseline in iter_claim_lines(docs_root, recursive):
        c = classify(text, root, heading, baseline)
        c.doc, c.line = rel, line
        claims.append(c)
    return claims


def summarize(claims: List[Claim]) -> Dict[str, int]:
    counts = {OK: 0, STALE: 0, NO_HASH: 0}
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


# ——— 第 4 步：行号内容校验（--check-lines，只报不拦） ———
#
# 三态会误判：HEAD 每前进一次，行号就漂一次，文件改过之后「行存在但不含」
# 是**正常老化**，不是引错。故扩成四态，用「坐标基准」把两者分开。

LINE_OK = "含字面量"
LINE_MISLOCATED = "错引"
LINE_RELOCATE = "需重定位"
LINE_UNRESOLVED = "path 或行号不存在"
LINE_UNRESOLVED_SUB = {
    "path-missing": "HEAD 里没有这个路径",
    "line-out-of-range": "路径存在但行数不够",
}


@dataclass
class LineCheck:
    doc: str
    line: int          # 断言所在文档行
    commit: str
    target: str        # path:N 或 path:N-M
    literal: str
    result: str        # LINE_OK / LINE_MISLOCATED / LINE_RELOCATE / LINE_UNRESOLVED
    detail: str = ""
    baseline: str = ""            # 坐标基准（写入四元组时的 HEAD）
    changes_since: Optional[int] = None   # 该文件自基准以来被改过几次
    latest_change: str = ""       # 最近一次改动：<短hash> <日期> <标题>
    hint: str = ""                # 无基准时的代理信息（仅供派活参考，不参与判定）


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _head_lines(root: Path, path: str) -> Optional[List[str]]:
    """取 ``HEAD:<path>`` 的行；路径不在 HEAD 里返回 None（与「文件为空」区分）。"""
    proc = _git(root, ["show", f"HEAD:{path}"])
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.splitlines()


def _split_target(target: str) -> tuple:
    """``path:N`` / ``path:N-M`` → (path, N)。

    **区间只取首行 N**（§7.7 写死）：``path:10-20`` 只校验第 10 行；要判
    「区间内任一行含字面量」必须另写显式标记，当前不支持。
    """
    path, _, lines = target.rpartition(":")
    return path, int(lines.split("-")[0])


def _changes_since(root: Path, baseline: str, path: str) -> Optional[tuple]:
    """``baseline..HEAD`` 之间该路径被改过几次：(次数, 最近一次描述)。

    基准缺失或解析不出 commit 时返回 ``None``——调用方据此判「需重定位」，
    **不得**当成「未改动」判红。
    """
    if not baseline or not _commit_exists(root, baseline):
        return None
    proc = _git(root, ["log", "--format=%h\t%ad\t%s", "--date=short",
                       f"{baseline}..HEAD", "--", path])
    if proc is None or proc.returncode != 0:
        return None
    rows = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return len(rows), (rows[0] if rows else "")


def check_line(root: Path, commit: str, target: str, literal: str,
               doc: str = "", doc_line: int = 0, baseline: str = "") -> LineCheck:
    """第 4 步：校验 ``path:N`` 在 **HEAD** 的第 N 行是否含字面量。

    **坐标口径（写死，勿改成被引提交坐标）**：行号是「当前 HEAD 坐标」，必须取
    ``git show HEAD:<path>`` 的第 N 行。不得取 ``git show <hash>:<path>`` ——
    被引提交当时那一行未必是文中写的那个内容。8/8 反例：
    ``2614c7f8`` 的 ``desktop/config_schema.py:377`` 当时是 ``agc_enabled``；
    ``6ed9ee7d`` 的 ``mbdsdr_ai/sdr_backend.py:111`` 当时是空行。
    按被引提交坐标判，会把现在正确的四元组判成红。

    四种结果分开返回，不合并：
      * ``LINE_OK``         第 N 行含该字面量（先精确子串，再空白归一后子串）
      * ``LINE_MISLOCATED`` 行存在但不含，且该文件自 ``baseline`` 以来**未改动**
                            → 真的引错了（红）
      * ``LINE_RELOCATE``   行存在但不含，但文件此后**改过**（或根本没有基准）
                            → 正常老化，需重定位（黄，不算失败）
      * ``LINE_UNRESOLVED`` 取不到那一行，``detail`` 区分「HEAD 无此路径」与「行数不够」

    判红/黄的分界只看「``baseline`` 之后该路径有没有提交」：没有提交却对不上，
    说明写四元组时就引错了；有提交则可能只是行号被推移，按黄处理。
    """
    path, n = _split_target(target)
    lines = _head_lines(root, path)
    base = dict(doc=doc, line=doc_line, commit=commit, target=target,
                literal=literal, baseline=baseline)
    if lines is None:
        return LineCheck(result=LINE_UNRESOLVED,
                         detail=LINE_UNRESOLVED_SUB["path-missing"], **base)
    if n < 1 or n > len(lines):
        return LineCheck(result=LINE_UNRESOLVED,
                         detail=f'{LINE_UNRESOLVED_SUB["line-out-of-range"]}（共 {len(lines)} 行）',
                         **base)
    text = lines[n - 1]
    if literal in text or _norm_ws(literal) in _norm_ws(text):
        return LineCheck(result=LINE_OK, detail=text.strip()[:120], **base)

    info = _changes_since(root, baseline, path)
    if info is None:
        # 无基准就判不了红/黄；用被引提交当代理给个老化线索，方便按文件派活。
        proxy = _changes_since(root, commit, path)
        hint = ""
        if proxy is not None:
            moves, last = proxy
            hint = (f"参考：自被引提交 {commit} 以来该文件改动 {moves} 次"
                    + (f"（最近 {last}）" if last else ""))
        return LineCheck(result=LINE_RELOCATE, hint=hint,
                         detail=f"无坐标基准（或缺基准哈希解析不出）；当前第 {n} 行：{text.strip()[:100]}",
                         **base)
    changes, latest = info
    if changes == 0:
        return LineCheck(result=LINE_MISLOCATED, changes_since=0,
                         detail=f"基准 {baseline} 之后该文件未改动，第 {n} 行：{text.strip()[:100]}",
                         latest_change=latest, **base)
    return LineCheck(result=LINE_RELOCATE, changes_since=changes, latest_change=latest,
                     detail=f"基准之后该文件改动 {changes} 次；当前第 {n} 行：{text.strip()[:100]}",
                     **base)


def line_checks(root: Path, claims: List[Claim]) -> List[LineCheck]:
    """对带完整 ``hash/target/literal`` 的断言逐条做第 4 步。"""
    out: List[LineCheck] = []
    for c in claims:
        if c.target and c.literal:
            out.append(check_line(root, c.commit, c.target, c.literal,
                                  doc=c.doc, doc_line=c.line, baseline=c.baseline))
    return out


def print_line_checks(rows: List[LineCheck]) -> None:
    order = (LINE_OK, LINE_MISLOCATED, LINE_RELOCATE, LINE_UNRESOLVED)
    counts = {k: 0 for k in order}
    for r in rows:
        counts[r.result] = counts.get(r.result, 0) + 1
    print("\n=== 行号内容校验（--check-lines，只报不拦） ===")
    print("坐标口径：git show HEAD:<path> 第 N 行（evidence-standard §7.7），"
          "不取被引提交坐标；区间 N–M 只判首行 N")
    print(f"含字面量 {counts[LINE_OK]}  |  错引（红） {counts[LINE_MISLOCATED]}  |  "
          f"需重定位（黄） {counts[LINE_RELOCATE]}  |  "
          f"path 或行号不存在 {counts[LINE_UNRESOLVED]}  |  合计 {len(rows)}")
    print("判红/黄：基准之后该文件无提交 → 错引；有提交或无基准 → 需重定位（不进失败数）")

    for result in (LINE_MISLOCATED, LINE_UNRESOLVED, LINE_RELOCATE, LINE_OK):
        group = [r for r in rows if r.result == result]
        if not group:
            continue
        print(f"\n[{result}] {len(group)} 条")
        for r in group:
            print(f"  {r.doc}:{r.line}  {r.commit}  {r.target}  `{r.literal}`")
            if result == LINE_RELOCATE:
                changes = "-" if r.changes_since is None else f"{r.changes_since} 次"
                print(f"      上次基准：{r.baseline or '（无）'}  |  此后改动：{changes}"
                      f"  |  最近一次：{r.latest_change or '-'}")
                if r.hint:
                    print(f"      {r.hint}")
            print(f"      结果：{r.result}" + (f"（{r.detail}）" if r.detail else ""))

    drift = [r for r in rows if r.result in (LINE_MISLOCATED, LINE_RELOCATE)]
    if drift:
        _print_grouped(drift)


def _print_grouped(rows: List[LineCheck]) -> None:
    """按文件聚合，先看集中度再决定重定位怎么派活。"""
    by_doc: Dict[str, List[LineCheck]] = {}
    by_path: Dict[str, List[LineCheck]] = {}
    for r in rows:
        by_doc.setdefault(r.doc, []).append(r)
        by_path.setdefault(r.target.rpartition(":")[0], []).append(r)

    def _dump(title: str, groups: Dict[str, List[LineCheck]]) -> None:
        print(f"\n--- 按{title}聚合（{len(groups)} 个文件 / {len(rows)} 条对不上）---")
        for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            red = sum(1 for x in items if x.result == LINE_MISLOCATED)
            yellow = sum(1 for x in items if x.result == LINE_RELOCATE)
            print(f"  {len(items):3d}  {name}   （错引 {red} / 需重定位 {yellow}）")

    _dump("文档", by_doc)
    _dump("目标代码路径", by_path)


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
    ap.add_argument("--check-lines", action="store_true",
                    help="第 4 步：校验 path:N 在 HEAD 第 N 行是否含字面量（只报不拦）")
    ap.add_argument("--fail-on-line-misref", action="store_true",
                    help="第 4 步里只有「错引」（红）算失败；「需重定位」（黄）不影响退出码。"
                         "隐含开启 --check-lines")
    args = ap.parse_args(argv)
    if args.fail_on_line_misref:
        args.check_lines = True

    root = Path(args.root)
    if not is_git_repo(root):
        print(f"错误: {root} 不是 git 仓库，无法核实断言", file=sys.stderr)
        return 2
    claims = verify_docs(root, Path(args.docs) if args.docs else None, args.recursive)
    print_report(claims)
    rows: List[LineCheck] = []
    if args.check_lines:
        rows = line_checks(root, claims)
        print_line_checks(rows)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": summarize(claims),
                   "claims": [asdict(c) for c in claims]}
        if args.check_lines:
            payload["line_checks"] = [asdict(r) for r in rows]
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")
    if args.fail_on_unverified and any(c.status != OK for c in claims):
        return 1
    if args.fail_on_line_misref and any(r.result == LINE_MISLOCATED for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
