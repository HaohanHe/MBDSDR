# SPDX-License-Identifier: MIT
"""钉扎 CLI 退出码文档与 `core/cli.py` 常量一致。

退出码表漂移过一次：代码有 5（--decode-file / 工具发现失败），
docs/cli_usage.md 只到 4。本测试解析文档表格，与代码常量做集合比较，
任何一侧新增/删除都会红。
"""
from __future__ import annotations

import os
import re

from mbdsdr_ai.core import cli

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "cli_usage.md")


def _doc_exit_codes() -> dict[int, str]:
    codes: dict[int, str] = {}
    in_section = False
    with open(_DOC, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped == "## 退出码":
                in_section = True
                continue
            if in_section and stripped.startswith("## "):
                break
            if not in_section:
                continue
            m = re.match(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*$", stripped)
            if m:
                codes[int(m.group(1))] = m.group(2)
    return codes


def test_doc_exit_codes_match_code_constants():
    doc_codes = _doc_exit_codes()
    assert doc_codes, "docs/cli_usage.md 未解析到退出码表格"
    assert set(doc_codes) == set(cli.EXIT_CODES), (
        "文档退出码与 core/cli.py 常量漂移："
        f"文档={sorted(doc_codes)} 代码={sorted(cli.EXIT_CODES)}")
    assert len(cli.EXIT_CODES) == len(set(cli.EXIT_CODES))


def test_exit_code_5_documented_as_decode_or_tool_discovery_failure():
    doc_codes = _doc_exit_codes()
    assert 5 in doc_codes
    meaning = doc_codes[5]
    assert "解码" in meaning
    assert "工具" in meaning
