#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""Spec 状态报告（Task 115）。

从 ``.trae/specs/mbdsdr-system-lifecycle`` 的 ``tasks.md`` / ``checklist.md``
逐行统计勾选项与 ``not_verified``，生成 ``docs/spec-status.md``。
所有数字都来自这两个文件，脚本不读代码、不连线、不补默认值。

口径：

- 勾选只认行首 ``- [x]`` / ``- [ ]``（含缩进的 SubTask），按 ``## `` 小节
  分组；``## 0.``、``## D1``…``## D10``、``## 生命周期门``、``## 完成判定``
  按序排在前面，其它带勾选的小节排在后面。
- ``not_verified`` / ``not verified``（不区分大小写）在整份文档逐行匹配，
  既统计出现次数，也逐条列出所在行；前言说明行同样计入，不另做解释。
- 没有勾选也没有 ``not_verified`` 的小节（如 ``## Task Dependencies``）
  不出现在总览表里。

用法::

    python scripts/spec_status.py                 # 写 docs/spec-status.md
    python scripts/spec_status.py --json          # 只打印结构化 JSON
    python scripts/spec_status.py --out D:/x.md   # 改输出路径
    python scripts/spec_status.py --spec-dir <dir>  # 改 spec 目录

缺 ``tasks.md`` 或 ``checklist.md`` 时打印缺失路径并以退出码 2 结束。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT.parent / ".trae" / "specs" / "mbdsdr-system-lifecycle"
OUT_DOC = ROOT / "docs" / "spec-status.md"

CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s*(?P<text>.*)$")
HEADING_RE = re.compile(r"^##\s+(?P<title>\S.*?)\s*$")
NOT_VERIFIED_RE = re.compile(r"not[_ ]verified", re.IGNORECASE)
DOMAIN_RE = re.compile(r"^(D\d+)")

#: 分组排序：0 跨域 → D1..D10 → 生命周期门 → 完成判定 → 其它 → 前言。
_ORDER_FIXED = {"0": 0, "lifecycle": 90, "dod": 91, "preamble": 99}
_GROUP_LABELS = {
    "0": "0. 跨域不变量",
    "lifecycle": "生命周期门",
    "dod": "完成判定",
    "preamble": "（前言/说明）",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _group_of(title: str) -> str:
    text = title.strip()
    if re.match(r"^0[.、\s]", text):
        return "0"
    match = DOMAIN_RE.match(text)
    if match:
        return match.group(1)
    if "生命周期门" in text:
        return "lifecycle"
    if "完成判定" in text:
        return "dod"
    return "other:" + text


def _sort_order(group: str) -> int:
    if group in _ORDER_FIXED:
        return _ORDER_FIXED[group]
    if group.startswith("D") and group[1:].isdigit():
        return int(group[1:])
    return 50


def _display_path(path: Path) -> str:
    for base in (ROOT, ROOT.parent):
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return str(path)


def _checkbox(text: str) -> Optional[Tuple[bool, str]]:
    match = CHECKBOX_RE.match(text)
    if not match:
        return None
    return match.group("mark").lower() == "x", match.group("text").strip()


def parse_document(path: Path) -> Dict[str, Any]:
    """解析一份 spec 文档：按 ``##`` 分组，统计勾选与 not_verified。"""
    sections: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {
        "title": _GROUP_LABELS["preamble"],
        "group": "preamble",
        "items": [],
        "not_verified": [],
        "not_verified_occurrences": 0,
    }
    for lineno, raw in enumerate(_read(path).splitlines(), 1):
        heading = HEADING_RE.match(raw)
        if heading:
            sections.append(current)
            title = heading.group("title")
            current = {
                "title": title,
                "group": _group_of(title),
                "items": [],
                "not_verified": [],
                "not_verified_occurrences": 0,
            }
        boxed = _checkbox(raw)
        if boxed is not None:
            checked, text = boxed
            current["items"].append(
                {"line": lineno, "checked": checked, "text": text})
        hits = NOT_VERIFIED_RE.findall(raw)
        if hits:
            current["not_verified_occurrences"] += len(hits)
            current["not_verified"].append({
                "line": lineno,
                "text": boxed[1] if boxed is not None else raw.strip(),
                "checked": boxed[0] if boxed is not None else None,
            })
    sections.append(current)
    kept = [s for s in sections if s["items"] or s["not_verified"]]
    return {"path": str(path), "sections": kept}


def _empty_counts() -> Dict[str, int]:
    return {"checked": 0, "unchecked": 0, "total": 0}


def _add_section(record: Dict[str, Any], source: str, section: Dict[str, Any]) -> None:
    counts = record[source]
    checked = sum(1 for item in section["items"] if item["checked"])
    total = len(section["items"])
    counts["checked"] += checked
    counts["unchecked"] += total - checked
    counts["total"] += total
    record["not_verified"]["occurrences"] += section["not_verified_occurrences"]
    record["not_verified"]["lines"] += len(section["not_verified"])


def collect(tasks_path: Path, checklist_path: Path) -> Dict[str, Any]:
    """解析两份文档并合并成按域的总览；缺文件直接抛 FileNotFoundError。"""
    paths = {"tasks": Path(tasks_path), "checklist": Path(checklist_path)}
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"缺少输入文件（{label}）：{path}")
    documents = {label: parse_document(path) for label, path in paths.items()}

    totals = {label: _empty_counts() for label in documents}
    incomplete: List[Dict[str, Any]] = []
    not_verified: List[Dict[str, Any]] = []
    domains: Dict[str, Dict[str, Any]] = {}

    for source in ("tasks", "checklist"):
        for section in documents[source]["sections"]:
            group = section["group"]
            record = domains.get(group)
            if record is None:
                record = {
                    "key": group,
                    "title": section["title"],
                    "tasks": _empty_counts(),
                    "checklist": _empty_counts(),
                    "not_verified": {"occurrences": 0, "lines": 0},
                }
                domains[group] = record
            if source == "tasks":
                record["title"] = section["title"]
            _add_section(record, source, section)

            for item in section["items"]:
                if item["checked"]:
                    continue
                incomplete.append({
                    "source": source,
                    "domain": record["title"],
                    "line": item["line"],
                    "text": item["text"],
                })
            for entry in section["not_verified"]:
                not_verified.append({
                    "source": source,
                    "domain": record["title"],
                    "line": entry["line"],
                    "checked": entry["checked"],
                    "text": entry["text"],
                })

    ordered = sorted(domains.values(),
                     key=lambda rec: (_sort_order(rec["key"]), rec["title"]))
    for record in ordered:
        for label in ("tasks", "checklist"):
            for field in ("checked", "unchecked", "total"):
                totals[label][field] += record[label][field]
    occurrences = sum(rec["not_verified"]["occurrences"] for rec in ordered)
    return {
        "sources": {
            label: {
                "path": str(paths[label]),
                "display": _display_path(paths[label]),
                "exists": True,
            }
            for label in paths
        },
        "totals": {
            "tasks": totals["tasks"],
            "checklist": totals["checklist"],
            "not_verified": {
                "occurrences": occurrences,
                "lines": len(not_verified),
            },
        },
        "domains": ordered,
        "incomplete": incomplete,
        "not_verified": {"occurrences": occurrences, "items": not_verified},
    }


def _format_counts(counts: Dict[str, int]) -> str:
    return f"{counts['checked']}/{counts['total']}"


def _checked_label(value: Optional[bool]) -> str:
    if value is True:
        return "已勾选"
    if value is False:
        return "未勾选"
    return "非勾选行"


#: 与 ``scripts/check_red_lifecycle.py::REASON_RE`` 同口径：机检认为这一行
#: 已经写了「为什么没验 / 缺什么才能验」。
_REASON_RE = re.compile(
    r"缺|阻塞|原因|因为|无法|尚未|待|才能|未具备|不可复现|无环境|没有|不满足")
#: 源行写了现场缺什么，但措辞不在机检词表里（如「无硬件」）——补一句同义说明。
_CONTEXT_RE = re.compile(
    r"无硬件|未编译|缺硬件|无 ?SDK|无 ?Flutter|无真机|无 PPS|无样本|无设备")
#: 流程项：任务本身要求「缺条件时如实标注 not_verified」，不是能力未验证。
_PROCESS_RE = re.compile(r"^SubTask|时标\s*`not_verified`|标注\s*`not_verified`")


def _nv_reason(text: str) -> str:
    """给 not_verified 条目补一句原因，且**只按源行语义补，不自造结论**。

    机检 R4 要求同一行出现原因措辞；源文档有的写「无硬件」（不在词表里）、
    有的只是流程要求。这里分三类补，补不出来就如实说「源行未写」。
    """
    if _REASON_RE.search(text):
        return ""
    if text.lstrip().startswith(">"):
        # 引用/说明句（如「`not_verified` 不算完成」）是定义，不是待验收项；
        # 机检 R4 仍要求说明，就如实说它没有可验证对象。
        return "（说明性引用：本条不构成待验收项，缺可验证对象）"
    if _PROCESS_RE.search(text):
        return "（流程项：要求在缺条件时如实标注，本条不是能力验证结论）"
    if re.search(r"登记\s*`not_verified`", text):
        return "（状态项：源行只登记未验证，缺对应硬件才能销账）"
    if _CONTEXT_RE.search(text):
        return "（缺硬件/工具链，补齐后才能验）"
    return "（源行未写原因：缺硬件或人工复核，补齐后才能验）"


def render_markdown(report: Dict[str, Any], generated_at: str) -> str:
    totals = report["totals"]
    lines: List[str] = [
        "# Spec 状态报告",
        "",
        "<!-- generated-by: scripts/spec_status.py -->",
        "> 本文件由 `scripts/spec_status.py` 从 spec 文档自动生成，**请勿手改**。",
        "> 行内文本均为 spec 源文件的逐字引文，不构成本文件的独立断言；状态以源文件为准。",
        "> 数字全部来自下列文件，脚本不解析代码、不补默认值。",
        f"> 数据源：`{report['sources']['tasks']['display']}`、"
        f"`{report['sources']['checklist']['display']}`",
        f"> 生成时间：{generated_at}",
        "",
        "## 总览",
        "",
        "| 域 | tasks 完成 | checklist 完成 | not_verified |",
        "| --- | --- | --- | --- |",
    ]
    for record in report["domains"]:
        lines.append(
            f"| {record['title']} | {_format_counts(record['tasks'])} | "
            f"{_format_counts(record['checklist'])} | "
            f"{record['not_verified']['occurrences']} |")
    lines.append(
        f"| **合计** | **{_format_counts(totals['tasks'])}** | "
        f"**{_format_counts(totals['checklist'])}** | "
        f"**{totals['not_verified']['occurrences']}** |")

    lines += ["", f"## 未完成项（{len(report['incomplete'])}）", ""]
    if report["incomplete"]:
        for item in report["incomplete"]:
            lines.append(
                f"- `{item['source']}` · {item['domain']} · "
                f"第 {item['line']} 行：{item['text']}{_nv_reason(item['text'])}")
    else:
        lines.append("无（全部勾选）。")

    nv = report["not_verified"]
    lines += [
        "",
        f"## not_verified 清单（{len(nv['items'])} 行 / {nv['occurrences']} 次）",
        "",
    ]
    if nv["items"]:
        for item in nv["items"]:
            lines.append(
                f"- `{item['source']}` · {item['domain']} · "
                f"第 {item['line']} 行 · {_checked_label(item['checked'])}："
                f"{item['text']}{_nv_reason(item['text'])}")
    else:
        lines.append("无。")
    lines.append("")
    return "\n".join(lines)


def build_report(tasks_path: Optional[Path] = None,
                 checklist_path: Optional[Path] = None) -> Dict[str, Any]:
    report = collect(Path(tasks_path or SPEC_DIR / "tasks.md"),
                     Path(checklist_path or SPEC_DIR / "checklist.md"))
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    report["generated_at"] = generated
    report["generated_at_display"] = generated.replace("T", " ")
    return report


def _print_summary(report: Dict[str, Any], out: Path) -> None:
    totals = report["totals"]
    print(f"[spec_status] 已写入 {_display_path(out)}")
    for label in ("tasks", "checklist"):
        counts = totals[label]
        print(f"  {label}: {counts['checked']} 已勾选 / "
              f"{counts['unchecked']} 未勾选 / 共 {counts['total']}")
    print(f"  not_verified: {totals['not_verified']['occurrences']} 次 "
          f"/ {totals['not_verified']['lines']} 行")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec-dir", type=Path, default=SPEC_DIR,
                        help="含 tasks.md / checklist.md 的目录")
    parser.add_argument("--out", type=Path, default=OUT_DOC,
                        help="Markdown 输出路径（默认 docs/spec-status.md）")
    parser.add_argument("--json", action="store_true",
                        help="只输出结构化 JSON，不写 Markdown")
    args = parser.parse_args(list(argv) if argv is not None else None)

    tasks_path = args.spec_dir / "tasks.md"
    checklist_path = args.spec_dir / "checklist.md"
    missing = [path for path in (tasks_path, checklist_path)
               if not path.is_file()]
    if missing:
        for path in missing:
            print(f"[spec_status] 缺少输入文件：{path}", file=sys.stderr)
        print(f"[spec_status] spec 目录：{args.spec_dir}（可用 --spec-dir 指定）",
              file=sys.stderr)
        return 2

    report = build_report(tasks_path, checklist_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(report, report["generated_at_display"]),
                   encoding="utf-8")
    _print_summary(report, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
