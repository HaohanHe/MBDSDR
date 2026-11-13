#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""claim 台账 ↔ ``docs/COORDINATION.md`` §6.1「当前占用」表的语义交叉校验。

背景：此前对 ``COORDINATION.md`` 的机器检查只有 ``tools/axioms/survey.py``
统计 ``claim|CLAIM`` 的字样出现次数——那只证明「文档里提过锁」，既不证明
「台账里的每条占用都登记了」，也不证明「文档里写的每条占用真的存在」。
两个方向都可能说谎，所以两个方向都要查：

台账 → 文档（``ledger_only``）
    未过期、未释放的 claim（判活以占位文件的心跳 + TTL 为准，pid 不参与）
    必须在 §6.1 表里有行，且该行「线名」单元格包含台账 owner 原文。
    缺行 / owner 对不上 → 「台账有、文档无」。

文档 → 台账（``doc_only``）
    §6.1 表里的每个文件路径必须能对上台账记录（``current`` 或 ``history``
    里同一路径的 owner），或者该行**显式**标注「已释放 / 历史」。
    对不上 → 「文档有、台账无」。

行格式（与现有表保持一致，不另立规范）::

    | 文件 | 线名 | 开始时间 | 预计提交 hash |
    | `desktop/main_window.py` | 操作逻辑线（备注…） | 2026-11-13 01:48 | 随本笔 |

* 「文件」单元格可以有多个反引号路径，也支持 ``desktop/{a,b}.py`` 花括号组；
* 「线名」单元格 ``（…）`` 之前的部分参与 owner 匹配，括号内的备注忽略；
* 模板行 ``_（空）_``、真机独占行 ``真机（USB SDR）`` 不是文件 claim，跳过。

分层登记（防止把 100+ 条只读资料逐条抄成一张没人维护的表）：

* **必须逐条**：``desktop/``、``mbdsdr_ai/``、``scripts/``、``tools/``、
  ``tests/`` 等代码根，以及 ``docs/`` **顶层**共享文档（``docs/*.md``）。
  目录级行覆盖到这些路径 → 报「仅目录级登记」。
* **允许目录级**：``docs/<子目录>/**``（如 ``docs/learn/**``）。一行写清
  「模式 + owner + 起始时间」，覆盖该模式下该 owner 的全部 claim。
* 同一模式下的活跃 claim 若出现**第二个 owner**，目录级行只写第一个 → 报，
  必须再补一行（或多行）直到每个 owner 都有覆盖。

「先 release 再提交」窗口（2026-11-13）
---------------------------------------

④ 原来只看**活跃 claim**，于是提交前一刻 `release` 就能把行要求一起释放掉——
把「提交时必须已有 §6.1 行」偷换成「提交时只要没有活跃 claim」。现在补一条
窗口：暂存文件若在最近 ``RELEASE_WINDOW_S``（60 分钟）内被**同一 owner**持有过
并释放，仍要求行存在。判据只读台账 ``claims[key].history[].released_at`` 与
``owner``，不新增人工手续；超过窗口的旧释放不影响正常提交。

登记面自身豁免
--------------

``docs/COORDINATION.md`` 自身不参与「必须已有 §6.1 行」的判定——它是登记面，
不是被登记对象。**活跃 claim 分支与 60 分钟释放窗口分支都必须豁免**：只豁免
一条分支时，提交登记表的人会被要求先给自己写一行临时行、提交后再删掉；这是
「工具逼人制造失真」，比闸松更糟。

退出码：0 = 两个方向都无发现；1 = 用法/解析错误；3 = 有发现（同 audit-stale）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import file_claim as fc  # noqa: E402  （复用占位文件路径与只读窥视）
from claim_window import RELEASE_WINDOW_S, window_release  # noqa: E402

DOC = ROOT / "docs" / "COORDINATION.md"
SECTION_HEAD = "### 6.1 当前占用"
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FINDINGS = 3

TICK_RE = re.compile(r"`([^`]+)`")
#: 显式历史/已释放标记：只有写明的旧行才允许「文档有、台账无」。
RELEASED_MARKERS = ("已释放", "（历史）", "(历史)")
#: 「先 release 再提交」窗口（秒）由 claim_window 唯一定义，见模块文档。
#: 提交者身份环境变量（与 claim_gate 同口径）。
IDENTITY_ENV = "MBDSDR_CLAIM_OWNER"
#: 只解析台账里单条记录用的解码器（见 `_entry_for`）。
_JSON_DECODER = json.JSONDecoder()
#: 代码根 + docs 顶层共享文档必须逐条；其余 docs/<子目录> 允许目录级。
STRICT_PREFIXES = ("desktop/", "mbdsdr_ai/", "scripts/", "tools/", "tests/",
                   "web/", "cpp/", "mobile/", "deploy/", "service/")

Row = Tuple[List[str], str, bool]  # (paths, 线名原文, 是否显式历史/已释放)


def _norm(path: str) -> str:
    """台账与文档两侧的比较键：相对路径、正斜杠、小写（Windows 语义）。"""
    text = str(path).strip().strip("`").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lower()


def _expand_paths(cell: str) -> List[str]:
    """从一个单元格里取文件路径；支持 ``前缀/{a,b}.py`` 花括号组。"""
    out: List[str] = []
    for raw in TICK_RE.findall(cell):
        text = raw.strip()
        if "{" in text and "}" in text:
            prefix, rest = text.split("{", 1)
            body, suffix = rest.split("}", 1)
            for item in body.split(","):
                item = item.strip()
                if item:
                    out.append(_norm(prefix + item + suffix))
        elif "/" in text or re.search(r"\.[A-Za-z0-9]+$", text):
            out.append(_norm(text))
    # 去重且保序
    seen: Set[str] = set()
    unique = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_coordination_rows(text: str) -> List[Row]:
    """取 §6.1 的占用表行；到第一个非表格行即停止。"""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip().startswith(SECTION_HEAD))
    except StopIteration:
        raise ValueError(f"文档里找不到小节标题：{SECTION_HEAD}")
    rows: List[Row] = []
    started = False
    for ln in lines[start + 1:]:
        stripped = ln.strip()
        if stripped.startswith("|"):
            started = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 2:
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue  # 分隔行
            if cells[0] in ("文件", ""):
                continue  # 表头
            if "(" not in stripped and "（" not in stripped and not TICK_RE.search(stripped):
                continue  # 模板行 `_（空）_` 之外的纯文字行
            paths = _expand_paths(cells[0])
            if not paths:
                continue  # `_（空）_`、真机独占行
            owner_cell = cells[1]
            released = any(mark in stripped for mark in RELEASED_MARKERS)
            rows.append((paths, owner_cell, released))
            continue
        if started and stripped:
            break
    return rows


def _owner_label(cell: str) -> str:
    """「线名」单元格里参与匹配的部分：括号备注之前。"""
    return re.split(r"[（(]", cell, maxsplit=1)[0].strip()


def _owner_matches(label: str, owner: str) -> bool:
    return bool(label) and bool(owner) and (label == owner or owner in label or label in owner)


def ledger_index(registry: Dict[str, Any]) -> Dict[str, Set[str]]:
    """路径 → 台账里出现过的全部 owner（current + history）。"""
    index: Dict[str, Set[str]] = {}
    for entry in (registry.get("claims") or {}).values():
        path = entry.get("path") or ""
        owners = index.setdefault(_norm(path), set())
        current = entry.get("current")
        if isinstance(current, dict) and current.get("owner"):
            owners.add(str(current["owner"]))
        for item in entry.get("history") or []:
            if isinstance(item, dict) and item.get("owner"):
                owners.add(str(item["owner"]))
    return index


def active_claims(now: Optional[float] = None, marker_dir: Optional[Path] = None) -> List[Tuple[str, str]]:
    """未过期、未释放的 claim：(规范化路径, owner)。判活只看心跳 + TTL。"""
    now = time.time() if now is None else now
    out: List[Tuple[str, str]] = []
    for marker in sorted((marker_dir or fc.MARKER_DIR).glob("*.claim")):
        payload = fc._peek_marker(marker)
        if payload is None:
            continue
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or 0)
        ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
        if heartbeat + ttl_s <= now:
            continue  # 已过期 = 不再活跃（他人可夺取）
        out.append((_norm(str(payload.get("path") or "")), str(payload.get("owner") or "")))
    return out


def _marker_in(path: str, marker_dir: Optional[Path]) -> Path:
    """占位文件路径：默认与 file_claim 同一套命名，测试可改到临时目录。"""
    if marker_dir is None:
        return fc._marker_for(path)
    import hashlib

    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    return Path(marker_dir) / f"{digest}.claim"


def active_claims_for_paths(
    paths: Iterable[str],
    now: Optional[float] = None,
    marker_dir: Optional[Path] = None,
) -> List[Tuple[str, str]]:
    """只查这些路径的活跃 claim（pre-commit 用）。

    占位文件名是 ``sha1(比较键)[:16]``，所以能按路径直接定位，不必 glob 整个
    claims 目录再逐个解析——那是检查耗时里最大的一块（实测 100+ 个占位文件
    逐个加锁读，冷启动 0.4s 起步）。
    """
    now = time.time() if now is None else now
    out: List[Tuple[str, str]] = []
    for raw in paths:
        path = _norm(raw)
        payload = fc._peek_marker(_marker_in(path, marker_dir))
        if payload is None:
            continue
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or 0)
        ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
        if heartbeat + ttl_s <= now:
            continue
        out.append((_norm(str(payload.get("path") or "")), str(payload.get("owner") or "")))
    return out


def resolve_owner(root: Path = ROOT) -> str:
    """提交者身份：环境变量优先，其次 ``git config mbdsdr.owner``（与 claim_gate 同口径）。"""
    raw = (os.environ.get(IDENTITY_ENV) or "").strip()
    if raw:
        return raw
    proc = subprocess.run(
        ["git", "config", "--get", "mbdsdr.owner"],
        cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _entry_for(raw: bytes, path: str) -> Optional[Dict[str, Any]]:
    """从台账原文里取出 ``"<path>": {…}`` 这一条，不解析整本。

    整本 ``json.loads``（1 MB）实测中位 40ms，直接顶破钩子 0.30s 预算；这里用
    mmap + ``bytes.find`` 只切出目标条目再 ``raw_decode``（实测 0.3ms 量级）。

    台账由 ``file_claim`` 以 ``json.dumps`` 写出，``claims`` 的键就是规范化路径。
    同一段文字也可能出现在别人的 ``claim_reason`` 里，所以要求冒号后紧跟 ``{``，
    并用条目里的 ``path`` 字段再核对一次。
    """
    needle = f'"{path}":'.encode("utf-8")
    start = 0
    while True:
        idx = raw.find(needle, start)
        if idx < 0:
            return None
        start = idx + 1
        pos = idx + len(needle)
        while pos < len(raw) and raw[pos] in b" \t\r\n":
            pos += 1
        if raw[pos:pos + 1] != b"{":
            continue
        try:
            entry, _end = _JSON_DECODER.raw_decode(
                raw[pos:pos + 65536].decode("utf-8", "ignore"))
        except json.JSONDecodeError:  # 条目异常大：宁可不认，也不整本解析
            continue
        entry_path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(entry_path, str) and _norm(entry_path) == path:
            return entry


def _read_registry_bytes(registry_path: Path) -> Optional[bytes]:
    """台账原文（字节）；文件不存在 → None。读 1 MB 实测 0.4ms。"""
    try:
        return registry_path.read_bytes()
    except FileNotFoundError:
        return None


def recently_released_claims(
    registry_path: Path,
    paths: Iterable[str],
    owner: str = "",
    now: Optional[float] = None,
    window_s: float = RELEASE_WINDOW_S,
) -> List[Tuple[str, str, float]]:
    """最近 ``window_s`` 内被释放的暂存路径：[(规范化路径, 释放者 owner, 释放时间)]。

    只看 ``claims[key].history[].released_at``：释放不等于没发生过——「先 release
    再提交」若不加这条窗口，④ 就会从「提交时必须已有 §6.1 行」退化成「提交时只要
    没有活跃 claim」。

    ``owner`` 为空（提交身份未声明）时按**任一 owner**匹配（fail closed）：
    否则「release 完不声明身份」就成了绕过窗口的开关。
    """
    now = time.time() if now is None else now
    raw = _read_registry_bytes(Path(registry_path))
    if raw is None:
        return []  # 没有台账 = 没有持有过
    out: List[Tuple[str, str, float]] = []
    for path in {_norm(p) for p in paths}:
        # 窗口判定只在 claim_window.window_release 一处实现：窄工具
        # （append_coordination_row.py）调的是同一个函数，两侧不会各改各的。
        hit = window_release(_entry_for(raw, path), owner, now, window_s)
        if hit is not None:
            out.append((path, hit[0], hit[1]))
    out.sort()
    return out


def _is_pattern(path: str) -> bool:
    return path.endswith("/**")


def _matches(path: str, pattern: str) -> bool:
    return path.startswith(pattern[:-2])  # 去掉 `**`，保留末尾 `/`


def is_strict_path(path: str) -> bool:
    """代码文件、脚本、测试与 docs 顶层共享文档：必须逐条登记。"""
    if path.startswith(STRICT_PREFIXES):
        return True
    if path.startswith("docs/"):
        return "/" not in path[len("docs/"):]
    return True  # 根目录文件等一律按严格处理（fail closed）


def uncovered_pairs(
    pairs: Iterable[Tuple[str, str]],
    rows: List[Row],
) -> Tuple[List[Tuple[str, str, bool]], List[Row]]:
    """逐条判覆盖。返回（[(路径, owner, 是否仅目录级行)], 目录级行清单）。

    strict 路径必须命中**逐条**行；非 strict 可被 owner 匹配的目录级行覆盖。
    返回值保留 ``path``/``owner`` 两列，供 ``--staged`` 打印可直接执行的补登命令
    ——只返回格式化字符串的话，命令里的路径就得靠正则从提示里反解。
    """
    file_rows = [(ps, oc, rel) for ps, oc, rel in rows
                 if any(not _is_pattern(p) for p in ps)]
    pattern_rows = [(ps, oc, rel) for ps, oc, rel in rows
                    if any(_is_pattern(p) for p in ps)]
    out: List[Tuple[str, str, bool]] = []
    for path, owner in pairs:
        exact = any(path in ps and _owner_matches(_owner_label(oc), owner)
                    for ps, oc, _rel in file_rows)
        if exact:
            continue
        pat_hit = any(_matches(path, p) and _owner_matches(_owner_label(oc), owner)
                      for ps, oc, _rel in pattern_rows for p in ps if _is_pattern(p))
        if is_strict_path(path):
            out.append((path, owner, pat_hit))
        elif not pat_hit:
            out.append((path, owner, False))
    return out, pattern_rows


def _uncovered(
    pairs: Iterable[Tuple[str, str]],
    rows: List[Row],
) -> Tuple[List[str], List[Row]]:
    """``uncovered_pairs`` 的可打印形式（保持既有输出格式不变）。"""
    found, pattern_rows = uncovered_pairs(pairs, rows)
    out: List[str] = []
    for path, owner, pat_hit in found:
        if pat_hit:
            out.append(f"{path} owner={owner}（仅目录级登记；代码/共享文档必须逐条）")
        else:
            out.append(f"{path} owner={owner}")
    return out, pattern_rows


def cross_check(
    active: Iterable[Tuple[str, str]],
    registry: Dict[str, Any],
    doc_text: str,
) -> Dict[str, List[str]]:
    """两个方向的差集。返回 ledger_only / doc_only，均已格式化成可打印的行。"""
    active = list(active)
    rows = parse_coordination_rows(doc_text)
    ledger_only, pattern_rows = _uncovered(active, rows)
    file_rows = [(ps, oc) for ps, oc, _rel in rows if any(not _is_pattern(p) for p in ps)]

    # 目录级行必须覆盖该模式下的**全部** owner：出现第二个 owner 就报——
    # 除非那个 owner 已经用自己的逐条行登记过（否则「补了逐条行」也永远报红）。
    for ps, oc, rel in pattern_rows:
        if rel:
            continue
        label = _owner_label(oc)
        for pattern in (p for p in ps if _is_pattern(p)):
            extra = set()
            for path, owner in active:
                if not (path and _matches(path, pattern)):
                    continue
                if _owner_matches(label, owner):
                    continue
                covered = any(
                    path in ps0 and _owner_matches(_owner_label(oc0), owner)
                    for ps0, oc0 in file_rows)
                if not covered:
                    extra.add(owner)
            if extra:
                ledger_only.append(
                    f"{pattern} 目录级行 owner={label}，但同一模式下还有 owner={sorted(extra)}")

    recorded = ledger_index(registry)
    doc_only: List[str] = []
    for paths, owner_cell, released in rows:
        if released:
            continue
        label = _owner_label(owner_cell)
        for path in paths:
            if _is_pattern(path):
                owners = {o for p, os_ in recorded.items() if _matches(p, path) for o in os_}
                if not owners:
                    doc_only.append(f"{path}（目录级行，台账无匹配记录）")
                elif not any(_owner_matches(label, o) for o in owners):
                    doc_only.append(
                        f"{path}（文档线名={label}，台账 owner={sorted(owners)}）")
                continue
            owners = recorded.get(path)
            if not owners:
                doc_only.append(f"{path}（文档线名={label}，台账无此文件记录）")
            elif not any(_owner_matches(label, o) for o in owners):
                doc_only.append(
                    f"{path}（文档线名={label}，台账 owner={sorted(owners)}）")
    return {
        "ledger_only": ledger_only,
        "doc_only": doc_only,
        "rows": len(rows),
        "active": len(active),
    }


def _load_registry(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "claims": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"台账不是 JSON 对象：{path}")
    return data


def staged_paths(root: Path = ROOT) -> List[str]:
    """暂存区文件名（规范化）。pre-commit 用，只为「这次要提交的文件」算覆盖。"""
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--no-renames"],
        cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ValueError(f"git diff --cached 失败：{proc.stderr.strip()[:200]}")
    return [_norm(x) for x in proc.stdout.splitlines() if x.strip()]


def append_command(path: str, owner: str, doc: Path, root: Path) -> str:
    """一条可直接粘贴执行的补登记命令（窄锁，不需要整文件 claim）。

    拒绝信息里必须给出能跑通的命令：只写「补一行」让人自己拼路径/owner/说明，
    实际就是逼人 ``--no-verify``。这里把三样都填好；``--doc``/``--root`` 只在
    文档不在默认位置时追加，保证默认树里命令最短、别的根目录下也照样能跑。
    """
    parts = [
        "python scripts/append_coordination_row.py",
        f'--path "{path}"',
        f'--owner "{owner}"',
        '--note "§6.1 补登记（pre-commit ④ 拦截）"',
    ]
    doc_resolved = Path(doc).resolve()
    root_resolved = Path(root).resolve()
    if doc_resolved != (root_resolved / "docs" / "COORDINATION.md").resolve():
        parts.append(f'--root "{root_resolved}"')
        try:
            rel = doc_resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            rel = doc_resolved.as_posix()
        parts.append(f'--doc "{rel}"')
    return " ".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="claim 台账 ↔ COORDINATION.md §6.1 交叉校验")
    parser.add_argument("--doc", default=str(DOC), help="协调文档路径")
    parser.add_argument("--registry", default=str(fc.REGISTRY_PATH), help="claims.json 路径")
    parser.add_argument("--markers", default=str(fc.MARKER_DIR), help="占位文件目录（活跃 claim）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--staged", action="store_true",
                        help="只查暂存文件里的活跃 claim 有没有 §6.1 行（pre-commit 用）")
    args = parser.parse_args(argv)

    cmd_pairs: List[Tuple[str, str, bool]] = []
    try:
        text = Path(args.doc).read_text(encoding="utf-8")
        if args.staged:
            # 只为「这次要提交的文件」算：按路径直接定位占位文件，不 glob 全量。
            root = Path(args.doc).resolve().parents[1]
            rows = parse_coordination_rows(text)
            staged = set(staged_paths(root))
            # 登记面自身豁免：COORDINATION.md 不登记自己。必须在**两条**分支都生效
            # ——活跃 claim 分支原先漏了它，于是提交登记表要先给自己写一行临时行、
            # 提交后再删掉；这是「工具逼人制造失真」，比闸松更糟。
            self_key = _norm(os.path.relpath(args.doc, root))
            active = active_claims_for_paths(
                staged, marker_dir=Path(args.markers) if args.markers != str(fc.MARKER_DIR) else None)
            pairs = [(p, o) for p, o in active
                     if p in staged and p != self_key]
            missing_pairs, _pats = uncovered_pairs(pairs, rows)
            missing = [
                (f"{p} owner={o}（仅目录级登记；代码/共享文档必须逐条）" if pat else
                 f"{p} owner={o}")
                for p, o, pat in missing_pairs
            ]
            # 「先 release 再提交」窗口：释放不等于没发生过，窗口内仍要 §6.1 行。
            # 只对「没有活跃 claim」的暂存路径查，活跃的那批已由上面判过。
            # 协调文档本身豁免：它是登记面而不是被登记对象，否则「提交后删行」
            # 这一步永远要先给自己写一行、删行时又欠一行。
            rest = staged - {p for p, _o in pairs} - {self_key}
            recent: List[Tuple[str, str, float]] = []
            owner = resolve_owner(root) if rest else ""
            if rest:
                recent = recently_released_claims(
                    Path(args.registry), rest, owner)
            recent_missing, _ = _uncovered([(p, o) for p, o, _t in recent], rows)
            recent_missing_pairs, _ = uncovered_pairs(
                [(p, o) for p, o, _t in recent], rows)
            if recent_missing:
                note = "" if owner else "；提交身份未声明，按任一 owner 匹配"
                missing = missing + [
                    f"{line}（最近 {RELEASE_WINDOW_S / 60:.0f} 分钟内释放过，release 不消行{note}）"
                    for line in recent_missing
                ]
            # 补登命令按 (路径, owner) 生成：与缺失提示同源，不靠正则反解。
            cmd_pairs = missing_pairs + recent_missing_pairs
            result: Dict[str, Any] = {
                "ledger_only": missing, "doc_only": [],
                "rows": len(rows), "active": len(active),
                "staged": len(staged), "staged_claims": len(pairs),
                "staged_released": len(recent),
                "staged_released_missing": len(recent_missing),
            }
        else:
            active = active_claims(marker_dir=Path(args.markers))
            registry = _load_registry(Path(args.registry))
            result = cross_check(active, registry, text)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CHECK FAIL：{exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.staged:
        print(f"暂存文件 {result['staged']} 个，其中仍有活跃 claim "
              f"{result['staged_claims']} 条；缺 §6.1 行 {len(result['ledger_only'])} 条")
        if result.get("staged_released"):
            print(f"  最近 {RELEASE_WINDOW_S / 60:.0f} 分钟内被同一 owner 释放的暂存文件 "
                  f"{result['staged_released']} 个（release 不消行），其中缺行 "
                  f"{result['staged_released_missing']} 个")
        for line in result["ledger_only"]:
            print(f"  - {line}")
        if cmd_pairs:
            print("[补登记] 窄锁追加一行即可（毫秒级，不需要整文件 claim）；逐条执行：")
            seen_cmds: Set[str] = set()
            for path, owner, _pat in cmd_pairs:
                cmd = append_command(path, owner, Path(args.doc), root)
                if cmd in seen_cmds:
                    continue
                seen_cmds.add(cmd)
                print(f"  {cmd}")
    else:
        print(f"§6.1 表行：{result['rows']}；未过期 claim：{result['active']}")
        for name, label in (("ledger_only", "台账有、文档无"),
                            ("doc_only", "文档有、台账无")):
            items = result[name]
            print(f"[{label}] {len(items)} 条")
            for line in items:
                print(f"  - {line}")

    if not args.json:
        print("[规矩] 活跃 claim 在提交时就必须已有 §6.1 行：pre-commit ④ 用 --staged "
              "逐文件拦（补行用窄锁工具，毫秒级），不靠事后扫全量；最近 60 分钟内被同一 owner "
              "释放过的暂存文件同样要行——先 release 再提交不算消行。")

    findings = len(result["ledger_only"]) + len(result["doc_only"])
    return EXIT_FINDINGS if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
