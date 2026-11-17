#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""文档一致性巡检（Task 61）。

在工具面四方对账（``scripts/tool_surface_audit.py``）之外，检查三处
文档与实现的漂移：

1. ``docs/cli_usage.md`` 的退出码表 ↔ ``mbdsdr_ai/core/cli.py`` 的
   ``EXIT_CODES``（缺项、多项都算不一致）；
2. ``docs/tool-availability.md`` 的「逐项总表」↔ registry（复用
   ``tool_surface_audit`` 的解析与 registry 加载，不另抄一套）；
3. ``docs/limitations-register.md`` 是否登记了外部阻塞关键词
   （QGIS / 真实 LLM / 真实多类 IQ 样本 / Flutter SDK）。

任一处不一致打印清单并退出 1；``--json`` 输出机器可读报告。
只读仓库、不联网、不构建。

    python scripts/docs_consistency_audit.py
    python scripts/docs_consistency_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

CLI_DOC = ROOT / "docs" / "cli_usage.md"
TOOL_DOC = ROOT / "docs" / "tool-availability.md"
LIMITATIONS_DOC = ROOT / "docs" / "limitations-register.md"

#: 外部阻塞关键词组：标签 → 正则。文档里必须能命中每一组。
EXTERNAL_BLOCKERS = (
    ("QGIS", r"QGIS"),
    ("真实 LLM", r"(真实\s*LLM|真实\s*DeepSeek|DeepSeek\s*权重|LLM\s*权重|真实模型服务)"),
    ("真实多类 IQ 样本", r"(真实多类\s*IQ|多类\s*IQ\s*样本|真实\s*IQ\s*样本)"),
    ("Flutter SDK", r"Flutter\s*SDK"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _symmetric_diff(a: Iterable[str], b: Iterable[str]):
    a, b = set(a), set(b)
    return sorted(a - b), sorted(b - a)


def _cli_exit_codes() -> List[int]:
    from mbdsdr_ai.core.cli import EXIT_CODES

    return [int(code) for code in EXIT_CODES]


def _documented_exit_codes(path: Path) -> List[int]:
    text = _read(path)
    marker = "## 退出码"
    if marker not in text:
        raise ValueError(f"{path} 缺少「{marker}」小节，无法对账")
    section = text.split(marker, 1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    codes: List[int] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        first = line.strip("|").split("|")[0].strip()
        if first.isdigit():
            codes.append(int(first))
    return codes


def check_cli_exit_codes(doc_path: Optional[Path] = None,
                         exit_codes: Optional[Iterable[int]] = None) -> Dict[str, Any]:
    path = doc_path or CLI_DOC
    constants = list(exit_codes) if exit_codes is not None else _cli_exit_codes()
    documented = _documented_exit_codes(path)
    missing, unknown = _symmetric_diff(constants, documented)
    return {
        "ok": not missing and not unknown,
        "path": str(path),
        "constants": sorted(constants),
        "documented": sorted(documented),
        "missing_in_docs": missing,
        "unknown_in_docs": unknown,
    }


def _registry_names() -> List[str]:
    from tool_surface_audit import load_registry

    return sorted(load_registry().tools)


def check_tool_availability(doc_path: Optional[Path] = None,
                            registry_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    from tool_surface_audit import doc_tool_names

    path = doc_path or TOOL_DOC
    registry = (sorted(registry_names) if registry_names is not None
                else _registry_names())
    documented = doc_tool_names(path)
    missing, extra = _symmetric_diff(registry, documented)
    return {
        "ok": not missing and not extra,
        "path": str(path),
        "registry_count": len(set(registry)),
        "doc_count": len(set(documented)),
        "missing_in_docs": missing,
        "extra_in_docs": extra,
    }


def check_external_blockers(doc_path: Optional[Path] = None) -> Dict[str, Any]:
    path = doc_path or LIMITATIONS_DOC
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "missing_keywords": [label for label, _ in EXTERNAL_BLOCKERS],
            "reason": "限制登记文件缺失",
        }
    text = _read(path)
    missing = [label for label, pattern in EXTERNAL_BLOCKERS
               if not re.search(pattern, text, re.IGNORECASE)]
    return {
        "ok": not missing,
        "path": str(path),
        "missing_keywords": missing,
    }


def build_report(cli_doc: Optional[Path] = None,
                 tool_doc: Optional[Path] = None,
                 limitations_doc: Optional[Path] = None,
                 exit_codes: Optional[Iterable[int]] = None,
                 registry_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    checks = {
        "cli_exit_codes": check_cli_exit_codes(cli_doc, exit_codes),
        "tool_availability": check_tool_availability(tool_doc, registry_names),
        "external_blockers": check_external_blockers(limitations_doc),
    }
    issues: List[str] = []
    cli = checks["cli_exit_codes"]
    for code in cli["missing_in_docs"]:
        issues.append(f"CLI 退出码 {code} 在文档中缺失")
    for code in cli["unknown_in_docs"]:
        issues.append(f"CLI 文档登记了未实现/未声明的退出码 {code}")
    tools = checks["tool_availability"]
    for name in tools["missing_in_docs"]:
        issues.append(f"工具文档缺 registry 工具：{name}")
    for name in tools["extra_in_docs"]:
        issues.append(f"工具文档有、registry 没有：{name}")
    for label in checks["external_blockers"]["missing_keywords"]:
        issues.append(f"限制登记缺少外部阻塞：{label}")
    return {"ok": all(c["ok"] for c in checks.values()),
            "checks": checks, "issues": issues}


def _print_human(report: Dict[str, Any]) -> None:
    print("文档一致性巡检")
    cli = report["checks"]["cli_exit_codes"]
    print(f"  CLI 退出码：常量 {cli['constants']} / 文档 {cli['documented']} "
          f"→ {'一致' if cli['ok'] else '漂移'}")
    tools = report["checks"]["tool_availability"]
    print(f"  工具文档：registry {tools['registry_count']} / 文档 {tools['doc_count']} "
          f"→ {'一致' if tools['ok'] else '漂移'}")
    blockers = report["checks"]["external_blockers"]
    print(f"  外部阻塞登记：{'齐备' if blockers['ok'] else '缺项'}")
    if report["ok"]:
        print("结论：一致")
        return
    print("结论：发现文档漂移")
    for issue in report["issues"][:60]:
        print(f"  - {issue}")
    if len(report["issues"]) > 60:
        print(f"  … 另有 {len(report['issues']) - 60} 项")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--cli-doc", type=Path, default=None)
    ap.add_argument("--tool-doc", type=Path, default=None)
    ap.add_argument("--limitations-doc", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        report = build_report(args.cli_doc, args.tool_doc, args.limitations_doc)
    except (OSError, ValueError, ImportError) as exc:
        print(f"[docs_consistency_audit] 巡检失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
