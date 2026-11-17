#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""工具面四方对账：registry / 能力目录 / 文档台账 / MCP 面。

单一事实源是 ``ToolRegistry``（模型可见的工具都在这里）。本脚本计算：

  1. registry ↔ ``docs/tool-availability.md`` 逐项总表：双向差；
  2. registry ↔ 能力目录 ``tool_registry_capabilities()``：双向差；
  3. MCP 面（``_build_tool_table``）↔ registry：按
     ``mbdsdr_ai/core/tool_surface_map.py`` 的显式映射与白名单比对——
     MCP 只暴露 registry 的一个已登记子集，差异必须恰好等于白名单。

任一处非零差异都打印清单并退出 1；``--json`` 输出机器可读报告。

    python scripts/tool_surface_audit.py
    python scripts/tool_surface_audit.py --json

不调用任何工具、不联网、不需要真机（offline + no hardware）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "desktop") not in sys.path:
    sys.path.insert(0, str(ROOT / "desktop"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

DOC = ROOT / "docs" / "tool-availability.md"
DOC_SECTION = "## 逐项总表"


def load_registry():
    """构建真实 ToolRegistry（不联网：模型列表请求打桩）。"""
    from mbdsdr_ai import model_manager as _mm

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    try:
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        agent = MBDSDRAgent(AgentConfig(api_key="sk-test",
                                        model="test-model"))
        return agent.tool_registry
    finally:
        _mm.ModelManager.fetch_models = original


def mcp_tool_names() -> List[str]:
    from mbdsdr_ai.core.mcp_server import _build_tool_table
    return [t["name"] for t in _build_tool_table()]


def catalog_tool_names(registry) -> List[str]:
    from capability_catalog import tool_registry_capabilities
    return [cap.source.split(":", 1)[1]
            for cap in tool_registry_capabilities(registry)
            if cap.source.startswith("tool_registry:")]


def doc_tool_names(path: Path = DOC) -> List[str]:
    """解析台账「逐项总表」的工具名（首列形如 ``| `name` |``）。"""
    text = path.read_text(encoding="utf-8")
    if DOC_SECTION not in text:
        raise ValueError(f"{path} 缺少「{DOC_SECTION}」小节，无法对账")
    names: List[str] = []
    for line in text.split(DOC_SECTION, 1)[1].splitlines():
        if line.startswith("| `"):
            names.append(line.strip("|").split("|")[0].strip().strip("`"))
    return names


def _symmetric_diff(a: Iterable[str], b: Iterable[str]):
    a, b = set(a), set(b)
    return sorted(a - b), sorted(b - a)


def build_report(registry=None, doc_path: Path = DOC) -> Dict[str, Any]:
    """计算四方口径与差异；返回值可直接序列化为 JSON。"""
    from mbdsdr_ai.core.tool_surface_map import diff_surfaces

    registry = registry if registry is not None else load_registry()
    reg_names = set(registry.tools)
    doc_names = doc_tool_names(doc_path)
    cat_names = catalog_tool_names(registry)
    mcp_names = mcp_tool_names()

    missing_docs, extra_docs = _symmetric_diff(reg_names, doc_names)
    missing_cat, extra_cat = _symmetric_diff(reg_names, cat_names)
    surface = diff_surfaces(mcp_names, reg_names)

    report: Dict[str, Any] = {
        "counts": {
            "registry": len(reg_names),
            "catalog": len(set(cat_names)),
            "docs": len(set(doc_names)),
            "mcp": len(set(mcp_names)),
            "mcp_mapped": len(surface.mapped),
            "mcp_only": len(surface.mcp_only),
            "registry_only": len(surface.registry_only),
        },
        "docs": {
            "missing_in_docs": missing_docs,
            "extra_in_docs": extra_docs,
        },
        "catalog": {
            "missing_in_catalog": missing_cat,
            "extra_in_catalog": extra_cat,
        },
        "mcp": {
            "mapped": dict(sorted(surface.mapped.items())),
            "mcp_only": surface.mcp_only,
            "registry_only": surface.registry_only,
            "mcp_only_unexpected": surface.mcp_only_unexpected,
            "mcp_only_missing": surface.mcp_only_missing,
            "registry_only_unexpected": surface.registry_only_unexpected,
            "registry_only_missing": surface.registry_only_missing,
            "mapping_missing_in_registry":
                surface.mapping_missing_in_registry,
            "mcp_undeclared": surface.mcp_undeclared,
            "mapping_stale": surface.mapping_stale,
            "clean": surface.clean,
        },
    }
    report["ok"] = (not missing_docs and not extra_docs
                    and not missing_cat and not extra_cat
                    and surface.clean)
    return report


def _print_list(label: str, names: List[str], limit: int = 40) -> None:
    print(f"  {label}（{len(names)}）：")
    for name in names[:limit]:
        print(f"    - {name}")
    if len(names) > limit:
        print(f"    … 另有 {len(names) - limit} 项")


def _print_human(report: Dict[str, Any]) -> None:
    c = report["counts"]
    print("工具面四方对账")
    print(f"  registry={c['registry']} 能力目录={c['catalog']} "
          f"文档={c['docs']} MCP={c['mcp']}")
    print(f"  MCP 映射={c['mcp_mapped']} MCP 独有={c['mcp_only']} "
          f"registry 独有={c['registry_only']}")
    if report["ok"]:
        print("结论：一致（差异均落在已登记白名单内）")
        return
    print("结论：发现未登记差异")
    if report["docs"]["missing_in_docs"]:
        _print_list("registry 有、文档缺", report["docs"]["missing_in_docs"])
    if report["docs"]["extra_in_docs"]:
        _print_list("文档有、registry 缺", report["docs"]["extra_in_docs"])
    if report["catalog"]["missing_in_catalog"]:
        _print_list("registry 有、能力目录缺",
                    report["catalog"]["missing_in_catalog"])
    if report["catalog"]["extra_in_catalog"]:
        _print_list("能力目录有、registry 缺",
                    report["catalog"]["extra_in_catalog"])
    mcp = report["mcp"]
    for key, label in (
        ("mcp_only_unexpected", "MCP 独有但未登记白名单"),
        ("mcp_only_missing", "白名单有、MCP 面缺"),
        ("registry_only_unexpected", "registry 独有但未登记白名单"),
        ("registry_only_missing", "白名单有、registry 缺"),
        ("mapping_missing_in_registry", "映射目标在 registry 不存在"),
        ("mcp_undeclared", "MCP 工具未声明映射/白名单"),
        ("mapping_stale", "映射表有、MCP 面已无该工具"),
    ):
        if mcp[key]:
            _print_list(label, mcp[key])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
