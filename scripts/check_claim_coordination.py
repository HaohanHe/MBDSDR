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

退出码：0 = 两个方向都无发现；1 = 用法/解析错误；3 = 有发现（同 audit-stale）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import file_claim as fc  # noqa: E402  （复用占位文件路径与只读窥视）

DOC = ROOT / "docs" / "COORDINATION.md"
SECTION_HEAD = "### 6.1 当前占用"
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FINDINGS = 3

TICK_RE = re.compile(r"`([^`]+)`")
#: 显式历史/已释放标记：只有写明的旧行才允许「文档有、台账无」。
RELEASED_MARKERS = ("已释放", "（历史）", "(历史)")
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
    claims 目录再逐个解析——那是 0.3s 预算里最大的一块（实测 100+ 个占位文件
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


def _uncovered(
    pairs: Iterable[Tuple[str, str]],
    rows: List[Row],
) -> Tuple[List[str], List[Row]]:
    """逐条判覆盖。返回（缺行明细, 目录级行清单）。

    strict 路径必须命中**逐条**行；非 strict 可被 owner 匹配的目录级行覆盖。
    """
    file_rows = [(ps, oc, rel) for ps, oc, rel in rows
                 if any(not _is_pattern(p) for p in ps)]
    pattern_rows = [(ps, oc, rel) for ps, oc, rel in rows
                    if any(_is_pattern(p) for p in ps)]
    out: List[str] = []
    for path, owner in pairs:
        exact = any(path in ps and _owner_matches(_owner_label(oc), owner)
                    for ps, oc, _rel in file_rows)
        if exact:
            continue
        pat_hit = any(_matches(path, p) and _owner_matches(_owner_label(oc), owner)
                      for ps, oc, _rel in pattern_rows for p in ps if _is_pattern(p))
        if is_strict_path(path):
            out.append(
                f"{path} owner={owner}（仅目录级登记；代码/共享文档必须逐条）"
                if pat_hit else f"{path} owner={owner}")
        elif not pat_hit:
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="claim 台账 ↔ COORDINATION.md §6.1 交叉校验")
    parser.add_argument("--doc", default=str(DOC), help="协调文档路径")
    parser.add_argument("--registry", default=str(fc.REGISTRY_PATH), help="claims.json 路径")
    parser.add_argument("--markers", default=str(fc.MARKER_DIR), help="占位文件目录（活跃 claim）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--staged", action="store_true",
                        help="只查暂存文件里的活跃 claim 有没有 §6.1 行（pre-commit 用）")
    args = parser.parse_args(argv)

    try:
        text = Path(args.doc).read_text(encoding="utf-8")
        if args.staged:
            # 只为「这次要提交的文件」算：按路径直接定位占位文件，不 glob 全量，
            # 也不读 claims.json —— 每一块都在 0.3s 预算里。
            rows = parse_coordination_rows(text)
            staged = set(staged_paths(Path(args.doc).resolve().parents[1]))
            active = active_claims_for_paths(
                staged, marker_dir=Path(args.markers) if args.markers != str(fc.MARKER_DIR) else None)
            pairs = [(p, o) for p, o in active if p in staged]
            missing, _pats = _uncovered(pairs, rows)
            result: Dict[str, Any] = {
                "ledger_only": missing, "doc_only": [],
                "rows": len(rows), "active": len(active),
                "staged": len(staged), "staged_claims": len(pairs),
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
        for line in result["ledger_only"]:
            print(f"  - {line}")
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
              "逐文件拦（0.3s 预算），不靠事后扫全量。")

    findings = len(result["ledger_only"]) + len(result["doc_only"])
    return EXIT_FINDINGS if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
