#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate the generated-inventory section of docs/mcp_tools.md.

Truth source: the LIVE MCPToolSet surface (mbdsdr_ai.core.mcp_server). The doc
never hardcodes tool counts -- re-run this script whenever the MCP surface
changes and the marker-delimited section is rebuilt from list_tools() output.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.core.mcp_server import MCPToolSet

DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "mcp_tools.md")
BEGIN = "<!-- BEGIN generated inventory (scripts/gen_mcp_tools_doc.py) -->"
END = "<!-- END generated inventory (scripts/gen_mcp_tools_doc.py) -->"


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def build_rows(tools) -> list:
    rows = ["| 工具 | 必填参数 | 说明 |", "|---|---|---|"]
    for t in sorted(tools, key=lambda x: x["name"]):
        req = (t.get("inputSchema") or {}).get("required") or []
        req_s = ", ".join("`%s`" % r for r in req) if req else "—"
        rows.append("| `%s` | %s | %s |" % (
            cell(t["name"]), cell(req_s), cell(t.get("description", ""))))
    return rows


def main() -> int:
    tools = MCPToolSet().list_tools()
    if not tools:
        print("MCPToolSet surfaced ZERO tools -- refusing to write an empty doc")
        return 1
    with open(DOC, "r", encoding="utf-8") as f:
        doc = f.read()
    if BEGIN not in doc or END not in doc:
        print("markers missing in %s -- re-add them first" % DOC)
        return 1
    head, rest = doc.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    new_doc = head + BEGIN + "\n" + "\n".join(build_rows(tools)) + "\n" + END + tail
    with open(DOC, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_doc)
    print("regenerated inventory: %d tools" % len(tools))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
