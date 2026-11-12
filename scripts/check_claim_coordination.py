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

退出码：0 = 两个方向都无发现；1 = 用法/解析错误；3 = 有发现（同 audit-stale）。
"""
from __future__ import annotations

import argparse
import json
import re
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


def cross_check(
    active: Iterable[Tuple[str, str]],
    registry: Dict[str, Any],
    doc_text: str,
) -> Dict[str, List[str]]:
    """两个方向的差集。返回 ledger_only / doc_only，均已格式化成可打印的行。"""
    active = list(active)
    rows = parse_coordination_rows(doc_text)
    ledger_only: List[str] = []
    for path, owner in active:
        hit = any(path in paths and _owner_matches(_owner_label(owner_cell), owner)
                  for paths, owner_cell, _released in rows)
        if not hit:
            ledger_only.append(f"{path} owner={owner}")

    recorded = ledger_index(registry)
    doc_only: List[str] = []
    for paths, owner_cell, released in rows:
        if released:
            continue
        label = _owner_label(owner_cell)
        for path in paths:
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="claim 台账 ↔ COORDINATION.md §6.1 交叉校验")
    parser.add_argument("--doc", default=str(DOC), help="协调文档路径")
    parser.add_argument("--registry", default=str(fc.REGISTRY_PATH), help="claims.json 路径")
    parser.add_argument("--markers", default=str(fc.MARKER_DIR), help="占位文件目录（活跃 claim）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    try:
        text = Path(args.doc).read_text(encoding="utf-8")
        registry = _load_registry(Path(args.registry))
        active = active_claims(marker_dir=Path(args.markers))
        result = cross_check(active, registry, text)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CHECK FAIL：{exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"§6.1 表行：{result['rows']}；未过期 claim：{result['active']}")
        for name, label in (("ledger_only", "台账有、文档无"),
                            ("doc_only", "文档有、台账无")):
            items = result[name]
            print(f"[{label}] {len(items)} 条")
            for line in items:
                print(f"  - {line}")

    findings = len(result["ledger_only"]) + len(result["doc_only"])
    return EXIT_FINDINGS if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
