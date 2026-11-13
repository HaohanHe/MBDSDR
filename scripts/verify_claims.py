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
  * 格式错误（多证据目标）一行里有 ≥2 个完整四元组且跨不同文件——
                          不猜哪个是证据，交写的人拆行或改成纯引用

跳过：标题行、``` 围栏代码块、``$ git`` 证据命令行 —— 它们不是断言。

默认只报告、退出码 0（存量 92 条待各线认领）；``--fail-on-unverified``
供存量清零后接 CI。``--check-lines`` 默认也只报，``--strict-lines`` 才参与退出码
（白名单清零后自动转严格）；白名单见 ``docs/line-check-waivers.md``。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

CLAIM_RE = re.compile(r"(已修复|已提交|已落地|已收口|已补齐|已解决|已修|已改|更正)")
HASH_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
PATHLINE_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|md|json|yaml|yml|ts|dart|cpp|c|h|hpp))"
    r":(\d+(?:-\d+)?)")
#: 无扩展名的脚本路径也是合法坐标（``scripts/hooks/post-commit:279``）。
#: 只认「含 / 且最后一段无扩展名」的形态，避免把 ``05:54`` 这类时间当坐标。
PATHLINE_NOEXT_RE = re.compile(
    r"([A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+):(\d+(?:-\d+)?)")
_PATH_TOKEN = (r"(?:[A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+"
               r"|[A-Za-z0-9_.\-]+\.(?:py|md|json|yaml|yml|ts|dart|cpp|c|h|hpp))")
#: 显式证据段 ``〔证：`hash` — `path:line` — `literal`〕``：段内四元组优先于行内其它坐标。
EVIDENCE_SEG_RE = re.compile(r"〔证[：:]([^〕]*)〕")
#: 完整四元组 ``<hash> — <path>:<line> — <字面量>``（破折号分隔，反引号可省）。
QUAD_RE = re.compile(
    r"`?([0-9a-f]{7,40})`?\s*[—–-]\s*`?(" + _PATH_TOKEN +
    r":\d+(?:-\d+)?)`?\s*[—–-]\s*`([^`]+)`")
TICK_RE = re.compile(r"`([^`]+)`")
#: 行内坐标基准：``（基准 `abc1234`）`` / ``baseline abc1234``
BASELINE_RE = re.compile(r"(?:基准|baseline)[:：]?\s*`?([0-9a-f]{7,40})`?")
#: 文件级坐标基准：``<!-- line-baseline: abc1234 -->``（写在文首，整篇生效）
DOC_BASELINE_RE = re.compile(r"<!--\s*line-baseline:\s*([0-9a-f]{7,40})\s*-->")

OK = "可证实"
STALE = "未证实（文档过期）"
NO_HASH = "未证实（无 hash）"
#: 格式错误：一行里有 ≥2 个完整四元组、且证据目标落在不同文件。
#: 不猜「哪个才是证据」，交写的人拆行或改成纯引用（见 ``_evidence_quads``）。
FORMAT_ERROR = "格式错误（多证据目标）"

EXCLUDE_DIRS = {"audit_r2"}
#: 白名单/账本本身不是断言；它按设计要引用「已修复…」原文，扫它等于自吞。
EXCLUDE_FILES = {"claim-waivers.md", "line-check-waivers.md"}

#: 第 4 步白名单文件名（放在 docs/ 下）
WAIVER_FILE = "line-check-waivers.md"
#: 白名单条数上限：**只减不增**。要新增豁免必须显式改这个常量（diff 可见，
#: 评审能拦住），不许悄悄往表里加行——「先记账再放行」是明确禁止的姿势。
MAX_WAIVERS = 1


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


def _is_pathline(token: str) -> bool:
    """``path:line`` 坐标（扩展名白名单，或「含 / 的无扩展名脚本」）。"""
    if PATHLINE_RE.fullmatch(token):
        return True
    m = PATHLINE_NOEXT_RE.fullmatch(token)
    return bool(m) and "." not in m.group(1).rsplit("/", 1)[-1]


def _pathlines(text: str) -> List[Tuple[str, str]]:
    """按出现顺序取坐标 ``(path, line)``，两种形态合并、不重复。

    旧实现只认 ``PATHLINE_RE``，``scripts/hooks/post-commit:279`` 这种无扩展名
    脚本永远匹配不到，于是行内更早的 ``docs/COORDINATION.md:1914`` 抢了证据位。
    """
    out: List[Tuple[str, str]] = []
    for m in PATHLINE_RE.finditer(text):
        item = (m.group(1), m.group(2))
        if item not in out:
            out.append(item)
    for m in PATHLINE_NOEXT_RE.finditer(text):
        path = m.group(1)
        if "." in path.rsplit("/", 1)[-1]:
            continue
        item = (path, m.group(2))
        if item not in out:
            out.append(item)
    out.sort(key=lambda it: text.find(f"{it[0]}:{it[1]}"))
    return out


def _evidence_quads(text: str) -> List[Tuple[str, str, str]]:
    """取一行里的完整四元组 ``(hash, target, literal)``。

    两处来源，口径不猜：

    1. 行内有 ``〔证：…〕`` 段时，**只认段内的四元组**——这是写的人显式指定的
       证据；行内其它 ``path:line`` 只是引用。
    2. 没有显式段时，认所有 ``<hash> — <path>:<line> — <字面量>`` 形态
       （必须有破折号分隔，光靠反引号相邻会把「基准 + 实现位置」误判成四元组）。

    返回空表表示这行没有结构化四元组，调用方退回旧的「首个坐标」口径。
    """
    segments = EVIDENCE_SEG_RE.findall(text) or [text]
    out: List[Tuple[str, str, str]] = []
    for segment in segments:
        for m in QUAD_RE.finditer(segment):
            h, target, literal = m.group(1), m.group(2), m.group(3).strip()
            if not _is_pathline(target):
                continue
            quad = (h, target, literal)
            if quad not in out:
                out.append(quad)
    return out


def _is_literal_token(t: str, hashes: List[str], target: str) -> bool:
    """能否作为「改动后唯一字面量」候选。"""
    if not t or t in hashes or t in target:
        return False
    if _is_pathline(t) or HASH_RE.fullmatch(t):
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
        if _is_pathline(token) and _is_literal_token(follow, hashes, target):
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

    ``baseline`` 是「坐标基准」（写入该四元组时该行的 blame 提交），只给第 4 步
    判红/黄用。它**不是证据**：候选提交里非基准的优先试，基准兜底——这样既能
    避免「基准写在证据 hash 之前被当成被引提交」，又不会误伤「基准恰好也是证据」
    的行（如单位统一那条，证据与基准同为 `ff964183`）。缺省则第 4 步一律按
    「需重定位」处理。
    """
    c = Claim(doc="", line=0, text=text.strip(), baseline=baseline)
    hashes = _hashes(text, root)
    targets = _pathlines(text) or _pathlines(heading)
    quads = _evidence_quads(text)
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

    quad_paths = list(dict.fromkeys(t.rpartition(":")[0] for _, t, _ in quads))
    if len(quad_paths) >= 2:
        # 拒绝，不猜：多个完整四元组指向不同文件时必须由写的人指定证据目标。
        c.status = FORMAT_ERROR
        c.reason = (f"一行里有 {len(quads)} 个完整四元组、跨 {len(quad_paths)} 个文件："
                    + "、".join(f"{t}（{h}）" for h, t, _ in quads))
        c.action = ("把证据拆到多行；或只保留一个四元组，其余改成不带"
                    "「hash — path:line — 字面量」结构的纯引用；"
                    "也可用 〔证：…〕 显式圈定唯一证据")
        return c

    if quads:
        target, path = quads[0][1], quad_paths[0]
    else:
        if len(targets) >= 2:
            # 没有结构化四元组、行内却有多个坐标：同样拒绝，不拿「第一个」当证据。
            c.status = FORMAT_ERROR
            c.reason = (f"有 hash 但无完整四元组，行内有 {len(targets)} 个坐标："
                        + "、".join(f"{p}:{n}" for p, n in targets))
            c.action = ("用 〔证：`hash` — `path:line` — `字面量`〕 圈定唯一证据目标，"
                        "或把其余坐标拆到别的行")
            return c
        target, path = f"{targets[0][0]}:{targets[0][1]}", targets[0][0]
    c.target = target

    if quads:
        # 同文件多个四元组是正常写法，但每一条都要核过：第二条字面量造假不得蒙混。
        for h, t, literal in quads:
            p = t.rpartition(":")[0]
            if not _commit_exists(root, h):
                c.commit = h
                c.status = STALE
                c.reason = f"hash {h} 不存在或不是 commit（四元组 {t} — `{literal}`）"
                c.action = "换成真实 commit hash；换不出就删断言"
                return c
            if not _commit_touches(root, h, p):
                c.commit = h
                c.status = STALE
                c.reason = f"{h} 未改动 {p}（四元组 {t} — `{literal}`）"
                c.action = "换成真正改到该文件的 hash（多半是另一条线的提交）"
                return c
            if not _log_s_hits(root, h, literal, p):
                c.commit = h
                c.status = STALE
                c.reason = f"git log -S `{literal}` 未命中 {h} — {p}（四元组 {t}）"
                c.action = "改字面量为该提交真实引入/删除的文本；对不上就判未证实并删断言"
                return c
        c.commit, c.target, c.literal = quads[0]
        c.status = OK
        c.reason = "逐条四元组：cat-file + show --name-only + log -S 全过"
        c.detail = [f"{h} — {t} — {lit}" for h, t, lit in quads]
        return c

    known = [h for h in dict.fromkeys(hashes) if _commit_exists(root, h)]
    if not known:
        c.status = STALE
        c.reason = f"hash {hashes[0]} 不存在或不是 commit"
        c.action = "换成真实 commit hash；换不出就删断言"
        return c

    candidates = _literal_candidates(text, hashes, target)
    if not candidates:
        c.status = STALE
        c.commit = known[0]
        c.reason = "缺「改动后唯一字面量」，git log -S 无法定位"
        c.action = "补反引号字面量（改动后代码里真实存在的那一段）"
        return c

    # 基准是元数据不是证据：非基准候选优先，基准兜底（见函数 docstring）。
    def _is_base(h: str) -> bool:
        return bool(baseline) and (h.startswith(baseline) or baseline.startswith(h))

    ordered = [h for h in known if not _is_base(h)] + [h for h in known if _is_base(h)]
    touching = [h for h in ordered if _commit_touches(root, h, path)]
    if not touching:
        c.commit = ordered[0]
        c.status = STALE
        c.reason = f"{ordered[0]} 未改动 {path}"
        c.action = "换成真正改到该文件的 hash（多半是另一条线的提交）"
        return c

    for h in touching:
        literal = next((t for t in candidates if _log_s_hits(root, h, t, path)), "")
        if literal:
            c.commit, c.literal = h, literal
            c.status = OK
            c.reason = "cat-file + show --name-only + log -S 三步全过"
            c.detail = [h, target, literal]
            return c

    c.commit = touching[0]
    c.status = STALE
    c.reason = f"git log -S 候选 {candidates} 均未命中 {touching}"
    c.action = "改字面量为该提交真实引入/删除的文本；对不上就判未证实并删断言"
    return c


def _doc_sources(docs_root: Path, recursive: bool, root: Optional[Path],
                 source: str) -> List[tuple]:
    """(相对路径, 行列表) 列表；决定「被评审的文档正文从哪取」。

    ``source="worktree"``（默认）读磁盘文件；``source="head"`` 只读
    ``git ls-tree HEAD`` 列出的文档 + ``git show HEAD:<path>``，工作区里
    别人的在途改动不参与评审——「以工作区计算的 PASS 不算通过」。
    """
    if source == "head":
        base = Path(root) if root else docs_root.parent
        rel_docs = docs_root.relative_to(base).as_posix()
        proc = _git(base, ["ls-tree", "-r", "--name-only", "HEAD", "--", rel_docs])
        if proc is None or proc.returncode != 0:
            return []
        picked: List[tuple] = []
        for rel in sorted(proc.stdout.splitlines()):
            rel = rel.strip()
            if not rel or not rel.endswith(".md"):
                continue
            parts = rel.split("/")
            if parts[-1] in EXCLUDE_FILES:
                continue
            if any(part in EXCLUDE_DIRS for part in parts[:-1]):
                continue
            if not recursive and "/" in "/".join(parts[1:]):
                continue
            blob = _git(base, ["show", f"HEAD:{rel}"])
            if blob is None or blob.returncode != 0:
                continue
            picked.append((rel, blob.stdout.splitlines()))
        return picked

    pattern = "**/*.md" if recursive else "*.md"
    picked = []
    for p in sorted(docs_root.glob(pattern)):
        if p.name in EXCLUDE_FILES:
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        picked.append((p.relative_to(docs_root.parent).as_posix(), lines))
    return picked


def iter_claim_lines(docs_root: Path, recursive: bool = False,
                     root: Optional[Path] = None,
                     source: str = "worktree") -> List[tuple]:
    """返回 (相对路径, 行号, 文本, 最近标题, 坐标基准)；跳过围栏与 $ 证据命令。

    ``source="head"`` 时正文取自 HEAD 树（见 :func:`_doc_sources`），
    条目集合与字面量都不受工作区在途改动影响。

    坐标基准的优先级：本行 ``（基准 `abc1234`）`` > 最近标题里的同写法 >
    文件首部 ``<!-- line-baseline: abc1234 -->``；都没有则为空串（第 4 步按
    「需重定位」处理，不判红）。

    默认只扫 ``docs/*.md`` 顶层（与历史盘点的 103 条同口径）；
    ``--recursive`` 才下沉到 docs/learn 等历史子目录。
    """
    out: List[tuple] = []
    for rel, lines in _doc_sources(docs_root, recursive, root, source):
        in_fence = False
        heading = ""
        heading_baseline = ""
        doc_baseline = ""
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
                out.append((rel, i, s, heading, baseline))
    return out


def verify_docs(root: Path = ROOT, docs_root: Optional[Path] = None,
                recursive: bool = False, source: str = "worktree") -> List[Claim]:
    root = Path(root)
    docs_root = Path(docs_root) if docs_root else root / "docs"
    claims: List[Claim] = []
    for rel, line, text, heading, baseline in iter_claim_lines(
            docs_root, recursive, root, source):
        c = classify(text, root, heading, baseline)
        c.doc, c.line = rel, line
        claims.append(c)
    return claims


def summarize(claims: List[Claim]) -> Dict[str, int]:
    counts = {OK: 0, STALE: 0, NO_HASH: 0, FORMAT_ERROR: 0}
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


# ——— 第 4 步：行号内容校验（--check-lines；--strict-lines 才拦） ———
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
    shift: int = 0                # 非严格模式下自动重定位的位移量（0 = 没动过）


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


def _blame_commit(root: Path, path: str, n: int) -> str:
    """``HEAD`` 中该路径第 N 行的 blame 提交（40 位哈希）。

    判据：``git blame -L N,N --porcelain HEAD -- <path>`` 输出的**首行首字段**
    就是该行最后一次改动的提交；``porcelain`` 首行之后才是元数据行。

    这就是「坐标基准」唯一合法的取值来源（§7.7）：基准必须是这一行的债主，
    随手取当前 HEAD 会在下次漂移时把错坐标永久判黄。
    取不到（路径不在 HEAD、行号越界、git 失败）返回空串。
    """
    proc = _git(root, ["blame", "-L", f"{n},{n}", "--porcelain", "HEAD", "--", path], 60)
    if proc is None or proc.returncode != 0:
        return ""
    first = proc.stdout.split("\n", 1)[0].strip()
    token = first.split()[0] if first else ""
    return token if re.fullmatch(r"[0-9a-f]{40}", token) else ""


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
      * ``LINE_MISLOCATED`` 行存在但不含，且 ``baseline`` **就是该行的 blame 提交
                            （或创建该行的提交）** → 真的引错了（红）
      * ``LINE_RELOCATE``   行存在但不含，但 ``baseline`` 与该行 blame 不一致，
                            或根本没有基准 → 正常老化/基准不合规，需重定位（黄）
      * ``LINE_UNRESOLVED`` 取不到那一行，``detail`` 区分「HEAD 无此路径」与「行数不够」

    判红/黄**不再看「基准之后该路径有没有提交」**，而看「基准是不是这一行的
    blame 提交（或创建该行的提交）」。原因：基准若随手取当时的 HEAD，它永远不是
    该行的债主，之后任何漂移都会被算成「文件改过 → 黄」，等于给错坐标发永久黄牌。
    基准取 blame 后，只有「同一作者的同一行、此后没人碰过、内容却对不上」才判红。
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

    blame = _blame_commit(root, path, n)
    info = _changes_since(root, baseline, path)
    changes, latest = info if info else (None, "")
    if not baseline:
        proxy = _changes_since(root, commit, path)
        hint = ""
        if proxy is not None:
            moves, last = proxy
            hint = (f"参考：自被引提交 {commit} 以来该文件改动 {moves} 次"
                    + (f"（最近 {last}）" if last else ""))
        return LineCheck(result=LINE_RELOCATE, hint=hint, changes_since=changes,
                         latest_change=latest,
                         detail=f"无坐标基准（或缺基准哈希解析不出）；当前第 {n} 行：{text.strip()[:100]}",
                         **base)
    same = bool(blame) and (blame.startswith(baseline) or baseline.startswith(blame))
    if same:
        return LineCheck(result=LINE_MISLOCATED, changes_since=changes,
                         latest_change=latest,
                         detail=f"基准 {baseline} 即第 {n} 行的 blame 提交，内容却对不上："
                                f"{text.strip()[:100]}", **base)
    return LineCheck(result=LINE_RELOCATE, changes_since=changes, latest_change=latest,
                     detail=f"基准 {baseline} 不是第 {n} 行的 blame（{blame[:8] or '未知'}）"
                            f"；当前第 {n} 行：{text.strip()[:100]}", **base)


def line_checks(root: Path, claims: List[Claim]) -> List[LineCheck]:
    """对带完整 ``hash/target/literal`` 的断言逐条做第 4 步。"""
    out: List[LineCheck] = []
    for c in claims:
        if c.target and c.literal:
            out.append(check_line(root, c.commit, c.target, c.literal,
                                  doc=c.doc, doc_line=c.line, baseline=c.baseline))
    return out


#: 自动重定位的单次位移上限：超过就退回报黄，交人工确认。
#: 阈值越小越安全：它挡的是「不是纯位移、只是恰好字面量还在附近」的误挪。
AUTO_SHIFT_MAX = 50


def _shift_since(root: Path, baseline: str, path: str, n: int) -> Optional[int]:
    """``baseline..HEAD`` 的纯位移量；不是纯位移（引用行本身被改）返回 None。

    只累加完全落在 N 之前的 hunk。任何覆盖 N 的 hunk 都意味着这一行被改写
    而不是被挪动——此时改号会把错坐标「修」成另一个错坐标。
    """
    if not baseline or not _commit_exists(root, baseline):
        return None
    proc = _git(root, ["diff", "-U0", f"{baseline}..HEAD", "--", path], 60)
    if proc is None or proc.returncode != 0:
        return None
    shift = 0
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
                         proc.stdout, re.M):
        a = int(m.group(1))
        b = int(m.group(2) or 1)
        d = int(m.group(4) or 1)
        if b == 0:                  # 纯插入：插在第 a 行之后
            if n > a:
                shift += d
            continue
        if a <= n <= a + b - 1:     # 引用行本身被改过，不是纯位移
            return None
        if n > a + b - 1:
            shift += d - b
    if shift == 0 or abs(shift) > AUTO_SHIFT_MAX:
        return None
    return shift


def auto_relocate(root: Path, rows: List[LineCheck]) -> List[str]:
    """非严格模式：把「纯位移」的坐标自动改到新行号，并逐条回报 shift。

    严格模式禁止调用——自动挪完再报绿，等于把漂移藏起来（人工确认是严格
    模式存在的唯一理由）。四项条件缺一即放弃、仍报黄：

    * 有坐标基准，且 ``基准..HEAD`` 是纯插入/删除（hunk 不覆盖引用行）；
    * ``|shift| <= AUTO_SHIFT_MAX``；
    * 新行仍含原字面量；
    * 新行的 blame 仍是该基准（否则是改写，不是移动）。
    """
    logs: List[str] = []
    for r in rows:
        if r.result != LINE_RELOCATE or not r.baseline:
            continue
        path, n = _split_target(r.target)
        if "-" in r.target.rpartition(":")[2]:   # 区间坐标不自动挪
            continue
        shift = _shift_since(root, r.baseline, path, n)
        if shift is None:
            continue
        m = n + shift
        lines = _head_lines(root, path)
        if not lines or not (1 <= m <= len(lines)):
            continue
        line_text = lines[m - 1]
        if not (r.literal in line_text
                or _norm_ws(r.literal) in _norm_ws(line_text)):
            continue
        blame = _blame_commit(root, path, m)
        if not blame or not (blame.startswith(r.baseline)
                             or r.baseline.startswith(blame)):
            continue
        doc_path = Path(r.doc)
        if not doc_path.is_absolute():
            doc_path = root / doc_path
        try:
            with open(doc_path, "r", encoding="utf-8", newline="") as f:
                text = f.read()
        except OSError:
            continue
        doc_lines = text.splitlines(keepends=True)
        if not (1 <= r.line <= len(doc_lines)):
            continue
        if doc_lines[r.line - 1].count(r.target) != 1:   # 同 target 出现多次不动
            continue
        new_target = f"{path}:{m}"
        doc_lines[r.line - 1] = doc_lines[r.line - 1].replace(r.target, new_target)
        with open(doc_path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(doc_lines))
        logs.append(f"  {r.doc}:{r.line}  {r.target} → {new_target}"
                    f"（shift={shift:+d}）")
        r.target = new_target
        r.result = LINE_OK
        r.shift = shift
        r.detail = f"自动重定位 shift={shift:+d}；{line_text.strip()[:80]}"
    return logs


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
    print("判红/黄：基准 == 该行 blame 提交 → 错引（红）；基准不是 blame 或无基准 → 需重定位（黄）")

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
            if result == LINE_OK and r.shift:
                print(f"      自动重定位：shift={r.shift:+d}（位移已写回文档）")

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


@dataclass
class Waiver:
    """一条「在途豁免」：只允许挂「他线正在改、我这轮不能动」的条目。

    四要素缺一不可：文档 + target（定位）/ owner（谁欠）/ 到期日（何时必须清）/
    原因（为什么它是在途而不是错误）。缺任何一项都判失败，防止白名单变成垃圾桶。
    """
    doc: str
    target: str
    owner: str
    due: str
    reason: str
    line: int = 0


def load_waivers(path: Path) -> Tuple[List[Waiver], List[str]]:
    """解析白名单 Markdown 表；返回 (条目, 错误)。错误一律视为失败，不放行。"""
    if not path.exists():
        return [], []
    waivers: List[Waiver] = []
    errors: List[str] = []
    seen = set()
    today = date.today()
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        s = raw.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5:
            continue
        doc, target, owner, due, reason = cells[:5]
        if doc in ("文档", "") or set(doc) <= set("-: "):
            continue
        key = (doc, target)
        if key in seen:
            errors.append(f"白名单第 {i} 行：重复豁免 {doc} {target}")
            continue
        seen.add(key)
        if not owner or owner in ("—", "-"):
            errors.append(f"白名单第 {i} 行：{doc} {target} 缺 owner")
        if not reason or reason in ("—", "-"):
            errors.append(f"白名单第 {i} 行：{doc} {target} 缺原因")
        try:
            due_date = date.fromisoformat(due)
        except ValueError:
            errors.append(f"白名单第 {i} 行：{doc} {target} 到期日 `{due}` 不是 YYYY-MM-DD")
        else:
            if due_date < today:
                errors.append(f"白名单第 {i} 行：{doc} {target} 豁免已过期（{due}）")
        waivers.append(Waiver(doc=doc, target=target, owner=owner, due=due,
                              reason=reason, line=i))
    if len(waivers) > MAX_WAIVERS:
        errors.append(f"白名单 {len(waivers)} 条 > 上限 {MAX_WAIVERS}：只减不增，"
                      f"新增豁免必须改 MAX_WAIVERS 常量并说明理由")
    return waivers, errors


def print_waivers(waivers: List[Waiver], errors: List[str]) -> None:
    print(f"\n白名单（line-check-waivers）：{len(waivers)} 条 / 上限 {MAX_WAIVERS}")
    for w in waivers:
        print(f"  {w.doc} {w.target}  owner={w.owner}  到期={w.due}  {w.reason}")
    for e in errors:
        print(f"  [白名单不合规] {e}")


def print_report(claims: List[Claim]) -> None:
    counts = summarize(claims)
    print(f"断言总数：{len(claims)}  |  可证实 {counts[OK]}  |  "
          f"文档过期 {counts[STALE]}  |  无 hash {counts[NO_HASH]}  |  "
          f"格式错误 {counts[FORMAT_ERROR]}")
    for status in (FORMAT_ERROR, STALE, NO_HASH, OK):
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
    ap.add_argument("--head", action="store_true",
                    help="文档正文取自 HEAD 树（git show HEAD:<path>），工作区在途改动不参与评审")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fail-on-unverified", action="store_true")
    ap.add_argument("--check-lines", action="store_true",
                    help="第 4 步：校验 path:N 在 HEAD 第 N 行是否含字面量")
    ap.add_argument("--fail-on-line-misref", action="store_true",
                    help="第 4 步里只有「错引」（红）算失败；「需重定位」（黄）不影响退出码。"
                         "隐含开启 --check-lines")
    ap.add_argument("--strict-lines", action="store_true",
                    help="第 4 步全量硬门：错引 / 需重定位 / 坐标不存在，只要不在白名单里就失败。"
                         "严格模式禁止自动重定位（自动挪完再报绿会把漂移藏起来）。"
                         "白名单清零后自动生效（无需再加此参数）")
    ap.add_argument("--waivers", default=None,
                    help=f"白名单路径，默认 <docs>/{WAIVER_FILE}")
    args = ap.parse_args(argv)
    if args.fail_on_line_misref:
        args.check_lines = True

    root = Path(args.root)
    if not is_git_repo(root):
        print(f"错误: {root} 不是 git 仓库，无法核实断言", file=sys.stderr)
        return 2
    docs_root = Path(args.docs) if args.docs else root / "docs"
    waivers_path = Path(args.waivers) if args.waivers else docs_root / WAIVER_FILE
    waivers, waiver_errors = load_waivers(waivers_path)
    # 白名单清零 → 自动转严格：临时放宽必须有账，账清了闸门自己合上。
    strict = args.strict_lines or (args.check_lines and not waivers)
    claims = verify_docs(root, Path(args.docs) if args.docs else None, args.recursive,
                         "head" if args.head else "worktree")
    print(f"评审对象：{'HEAD 树（--head，工作区不计）' if args.head else '工作区文件'}")
    print_report(claims)
    rows: List[LineCheck] = []
    open_problems: List[LineCheck] = []
    if args.check_lines:
        rows = line_checks(root, claims)
        if not strict:
            auto_logs = auto_relocate(root, rows)
            if auto_logs:
                print(f"\n=== 自动重定位（非严格模式，{len(auto_logs)} 处；"
                      f"严格模式不做任何自动改写） ===")
                print("\n".join(auto_logs))
        print_line_checks(rows)
        print_waivers(waivers, waiver_errors)
        keys = {(w.doc, w.target) for w in waivers}
        problems = [r for r in rows if r.result != LINE_OK]
        open_problems = [r for r in problems if (r.doc, r.target) not in keys]
        if problems:
            waived = len(problems) - len(open_problems)
            print(f"\n第 4 步：对不上 {len(problems)} 条（白名单吸收 {waived}，"
                  f"未豁免 {len(open_problems)}）；模式={'严格' if strict else '只报不拦'}")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": summarize(claims),
                   "claims": [asdict(c) for c in claims]}
        if args.check_lines:
            payload["line_checks"] = [asdict(r) for r in rows]
            payload["waivers"] = [asdict(w) for w in waivers]
            payload["waiver_errors"] = waiver_errors
            payload["strict"] = strict
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")
    if args.fail_on_unverified and any(c.status != OK for c in claims):
        return 1
    if args.fail_on_line_misref and any(r.result == LINE_MISLOCATED for r in rows):
        return 1
    if strict and (open_problems or waiver_errors
                   or any(c.status == FORMAT_ERROR for c in claims)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
